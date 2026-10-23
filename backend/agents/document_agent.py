"""
DocumentAgent - Day 6 重构版（移除重复初始化和降级代码）

基类 BaseAgent 已统一处理：
- LLM 服务初始化（self.llm / self.mock_llm）
- mock/real 切换 + 失败自动降级
- 降级统计与告警

子类只需：
1. 设置 AGENT_NAME
2. 实现 _extract_input / _mock_generate / _real_generate
3. 实现 _build_result

═══════════ 接口契约（与 routers/agents_day4.py 对齐）══════════
- 输出字段：document (Dict)
  - 字段：title, content, topic, difficulty, estimated_time, tags
══════════════════════════════════════════════════════════
"""
import logging
import re
from typing import Dict, Any

from graph.state import AgentState
from agents import BaseAgent
from services.skill_library import skill_prompt_prefix

logger = logging.getLogger("rjianb.document")


_INVENTED_PROFILE_PATTERNS = (
    re.compile(r"你(?:是|属于).{0,8}(?:二本|一本|985|211|双一流|普通本科)"),
    re.compile(r"你(?:还|大约|约)?(?:有|剩).{0,12}(?:年|个月).{0,8}(?:毕业|备考)"),
    re.compile(r"你比.{0,18}(?:更有优势|多出优势|更适合)"),
    re.compile(r"你.{0,18}(?:背了又忘|总是(?:记不住|做错)|经常(?:做错|忘记))"),
)
_UNVERIFIED_POLICY_PATTERNS = (
    re.compile(r"(?:退役大学生士兵|退伍|专项计划).{0,30}(?:单列名额|单独划线|分数线.{0,8}(?:更低|低于)|优先录取)"),
    re.compile(r"(?:公务员|国考|省考|事业编).{0,30}(?:一定|通常|普遍).{0,18}(?:限制|要求|加分|优先)"),
    re.compile(r"(?:院校|专业|岗位).{0,20}(?:报录比|分数线|招生人数|招聘人数).{0,12}(?:是|为|约|通常)"),
    re.compile(r"(?:初试通常涉及|大四上学期.{0,8}月|学制短|竞争激烈)"),
    re.compile(r"(?:数一|数二).{0,24}(?:英一|英二).{0,24}(?:408|自命题)"),
)
_ABSOLUTE_DIRECTION_PATTERNS = (
    re.compile(r"(?:首选|最适合|必须选|不要选|别碰).{0,28}(?:方向|专业|岗位|院校|算法|专硕|学硕)"),
    re.compile(r"(?:数学|英语|编程).{0,12}(?:好才考虑|没把握别碰|差就不要)"),
    re.compile(r"(?:采用|按照).{0,8}(?:默认路径|默认方案)"),
)
_INFERRED_TIMELINE_PATTERNS = (
    re.compile(r"距离(?:考研|初试|毕业).{0,30}(?:\d|[一二三四五六七八九十两]).{0,4}(?:年|个月)"),
)
_WORK_PUSHBACK_PATTERNS = (
    re.compile(r"(?:需要|由|请)你.{0,10}(?:自行|自己).{0,8}(?:查|查询|检索|核对|整理)"),
)
# Deterministic rewrite of research pushback phrasing into assistant-owned work.
_USER_RESEARCH_PUSHBACK_REWRITES = (
    (
        re.compile(r"(?:需要|请|让|要)你[^。！？；\n]{0,12}?(?:自己|自行)[^。！？；\n]{0,10}?(查阅|翻阅|翻看|查询|检索|核对|整理|比较|筛选|查)([^。！？；\n]{0,16})"),
        r"这部分由 DARYA 下一轮\1\2",
    ),
    (
        re.compile(r"由你[^。！？；\n]{0,12}?(?:自己|自行)[^。！？；\n]{0,10}?(查阅|翻阅|翻看|查询|检索|核对|整理|比较|筛选|查)([^。！？；\n]{0,16})"),
        r"由 DARYA 下一轮\1\2",
    ),
)
_UNVERIFIED_CREATOR_PATTERNS = (
    re.compile(r"(?:每回都考|必考|高频(?:错误)?|最常见|最容易|重灾区|错误率最高)"),
    re.compile(r"(?:完播|涨粉).{0,12}(?:自然|一定|保证)"),
    re.compile(r"(?:稍后再看|自己播放|自播).{0,12}(?:自测|测试|提高).{0,8}完播率"),
    re.compile(r"(?:十题错[七八九]题|很多同学会忘|大多数学生|普遍都会错)"),
)
_LEARNING_OVERPROMISE_PATTERNS = (
    re.compile(r"(?:以后|从此|保证).{0,16}(?:不会再|都能|一定能).{0,10}(?:错|写对|丢分|学会)"),
    re.compile(r"(?:定义域|这类题|这个知识点).{0,10}(?:不会再丢分|一定能写对|保证学会)"),
    re.compile(r"(?:拿满分|不会漏|不漏不错|保证得分|稳拿分)"),
)
_CROSS_METRIC_BASELINE_PATTERNS = (
    re.compile(r"(?:播放|完播).{0,12}(?:或|和|以及).{0,12}(?:评论|理解证据).{0,18}(?:高于|不低于|低于).{0,8}基线"),
    re.compile(r"(?:评论|理解证据).{0,12}(?:或|和|以及).{0,12}(?:播放|完播).{0,18}(?:高于|不低于|低于).{0,8}基线"),
)
_PUBLIC_DECISION_ARTIFACT = re.compile(
    r"(?:考公|公务员|事业单位|事业编|考研|就业|求职|择校|选岗|岗位|职位表|报考)",
)


def _confirmed_context_facts(instruction: str) -> list[str]:
    """Extract only facts literally present in the user's conversation context."""
    text = str(instruction or "")
    candidates = (
        (r"退伍|退役", "刚退伍返校"),
        (r"武汉东湖学院", "武汉东湖学院"),
        (r"计算机专业", "计算机专业"),
        (r"大二", "大二"),
        (r"数学基础一般", "数学基础一般"),
        (r"(?:每天|每日|工作日)[^。｜]{0,12}40\s*分钟", "每天可投入40分钟"),
        (r"(?:我是会计|会计(?:工作|职业|背景)|会计考公)", "会计职业背景"),
        (r"湖北", "当前考虑湖北"),
        (r"(?:优先考虑武汉|武汉优先|考虑武汉)", "优先考虑武汉"),
        (r"(?:每天|每日|工作日)[^。｜]{0,12}30\s*分钟", "工作日可投入30分钟"),
        (r"(?:受了很大刺激|遭遇刺激|工作受刺激|情绪很乱)", "近期受工作刺激、情绪较乱"),
        (r"考公务员|考公", "准备考公"),
        (r"考研", "准备考研"),
    )
    facts: list[str] = []
    for pattern, label in candidates:
        if re.search(pattern, text) and label not in facts:
            facts.append(label)
    return facts


def should_use_public_decision_guide(topic: str, instruction: str) -> bool:
    """Classify from user-owned topic/facts, never from safety boilerplate."""
    if _PUBLIC_DECISION_ARTIFACT.search(str(topic or "")):
        return True
    facts = _confirmed_context_facts(instruction)
    return any(fact in {"准备考公", "准备考研"} for fact in facts)


def build_unverified_public_decision_guide(topic: str, instruction: str) -> str:
    """Build a useful guide without letting an LLM invent current public facts."""
    text = str(instruction or "")
    facts = _confirmed_context_facts(text)
    minute_matches = re.findall(r"(?:每天|每日|工作日)[^。｜]{0,12}?(\d{1,3})\s*分钟", text)
    minutes = int(minute_matches[-1]) if minute_matches else 20
    minutes = min(120, max(10, minutes))
    settle = max(2, round(minutes * 0.1))
    clarify = max(3, round(minutes * 0.25))
    practice = max(3, round(minutes * 0.45))
    save = minutes - settle - clarify - practice
    if save < 2:
        practice -= 2 - save
        save = 2

    is_exam = bool(re.search(r"考公|公务员|事业单位|事业编|职位表|选岗", text))
    goal = "会计考公" if is_exam and re.search(r"会计", text) else ("考公" if is_exam else "考研")
    facts_md = "\n".join(f"- {fact}" for fact in facts) or "- 当前只确认了本轮提出的目标；其余个人条件保持未知。"
    if is_exam:
        unknowns = "学历与毕业年份、专业全称、应届/往届状态、政治面貌、证书与可接受地区边界"
        verification = (
            "- 最新国家与湖北招录公告的官方入口、发布日期和有效版本\n"
            "- 武汉范围内职位表中与用户专业全称匹配的岗位及逐项资格限制\n"
            "- 候选岗位的考试科目、报名窗口、招录人数等原表字段\n"
            "- 对筛选结果保留来源链接、发布日期和检索日期；没有原表证据就不下结论"
        )
        practice_task = (
            "阅读下面这段虚构练习材料，只写三行：问题、原因、一个可执行动作。\n\n"
            "> 某单位报销材料多次退回。经复盘，填写口径分散、交接记录缺失，新同事只能反复询问。\n\n"
            "这只是通用表达基线，不冒充真题，也不据此判断你的能力。"
        )
    else:
        unknowns = "目标专业方向、目标地区、预期考试年份、当前课程基础、院校偏好与可接受成本"
        verification = (
            "- 候选院校研究生院官网的最新招生简章、专业目录和参考科目\n"
            "- 与用户身份相关的专项政策原文、适用条件和有效年份\n"
            "- 复试、学费、学制等影响选择的官方字段\n"
            "- 每条结论保留官网链接、发布日期和检索日期；没有原文证据就不下结论"
        )
        practice_task = (
            "做一个不依赖院校信息的基础样本：任选一道最近做错或最没把握的题，记录“题型—卡点—下一次先检查什么”。\n\n"
            "没有现成题时，只写出当前最担心的一个科目和一个具体卡点。"
        )

    return f"""# {goal}｜{minutes}分钟可保存行动底稿

## 我已经沿用的账号信息
{facts_md}

以上只来自同一账号的用户原话。没有说过的资格、能力、优势和时间线均不补猜。

## 今天的完成信号
- 留下一份“已知条件 / 待确认条件”底稿。
- 完成一个不依赖外部数据的小样本。
- 明确下一轮由 DARYA 核对哪些官方信息；你不需要自己翻表找数据。

## 今天共 {minutes} 分钟
1. **先稳住（{settle}分钟）**：离开刚才的刺激源，喝水并做缓慢呼吸。今天不做辞职、报名或付费等不可逆决定。
2. **补一格关键条件（{clarify}分钟）**：从下列未知项中只选最容易的一项写下来，不要求一次答完：{unknowns}。
3. **留下能力基线（{practice}分钟）**：{practice_task}
4. **保存与收尾（{save}分钟）**：把今天写下的内容保存为“{goal}底稿01”，到点即停。

## 待核实清单｜由 DARYA 继续查
{verification}

当前没有附带已核验来源，因此本资料不提供岗位、院校、竞争强度、考试日期或优先级结论。下一轮拿到官方材料后，再把“可报/不可报/仍待确认”分开列给你选择。

## 跟不上时的减量方案
状态差时只做第1步，并写下一句“我现在最担心的是____”。这也算今天完成，不补时长、不追赶。

## 下一轮只问一个问题
请只补充“{unknowns.split('、')[0]}”。其余背景不必重讲。
"""


def artifact_cognition_issues(
    content: str,
    *,
    has_verified_sources: bool = False,
    instruction: str = "",
) -> list[str]:
    """Return narrow high-risk defects that require an artifact repair pass."""
    text = str(content or "")
    issues: list[str] = []
    if any(pattern.search(text) for pattern in _INVENTED_PROFILE_PATTERNS):
        issues.append("把用户未确认的个人画像或时间条件写成事实")
    if any(pattern.search(text) for pattern in _INFERRED_TIMELINE_PATTERNS):
        issues.append("根据年级擅自推算考试或毕业时间")
    if not has_verified_sources and any(pattern.search(text) for pattern in _UNVERIFIED_POLICY_PATTERNS):
        issues.append("把缺少来源的政策、院校或岗位信息写成已核实结论")
    if any(pattern.search(text) for pattern in _ABSOLUTE_DIRECTION_PATTERNS):
        issues.append("在关键信息不足时给出绝对化方向判断")
    if any(pattern.search(text) for pattern in _WORK_PUSHBACK_PATTERNS):
        issues.append("把公开信息检索或整理工作推回给用户")
    if not has_verified_sources and any(pattern.search(text) for pattern in _UNVERIFIED_CREATOR_PATTERNS):
        issues.append("把平台指标、考试频率或受众错误分布写成无来源事实")
    if any(pattern.search(text) for pattern in _LEARNING_OVERPROMISE_PATTERNS):
        issues.append("对学习效果作出无法验证的保证")
    if any(pattern.search(text) for pattern in _CROSS_METRIC_BASELINE_PATTERNS):
        issues.append("把不同指标混用同一个历史基线作结论")
    if "待核实" not in text and "需要确认" not in text and "核对" not in text:
        issues.append("没有明确区分已知事实与待核实信息")
    known_context = str(instruction or "")
    v2_brief = "统一工作简报：" in known_context
    if v2_brief:
        if re.search(r"(?:基于)?(?:你|用户)(?:已经|已)?确认(?:过)?的?(?:需求|目标|身份|背景)", text):
            issues.append("把自然提到但未确认的信息写成已确认事实")
        duration_is_user_owned = bool(re.search(
            r"(?:视频|成片|口播|时长|长度)[^。\n]{0,18}\d{1,3}\s*(?:[-–—至到]\s*\d{1,3}\s*)?分钟",
            known_context.split("不得猜测用户可投入时间", 1)[0],
        ))
        if not duration_is_user_owned and re.search(r"\d{1,3}\s*(?:秒|分钟)", text):
            issues.append("在用户未给时长时擅自设定成片时长")
        direct_result_requested = bool(re.search(r"可直接(?:拍摄|使用)|完整(?:拍摄|分镜)?脚本|直接拍的版本", known_context))
        blank_count = len(re.findall(r"\[\s*(?:待定|待填|填写|____*)\s*\]", text))
        if direct_result_requested and (
            blank_count > 1
            or ("模板" in text and re.search(r"(?:请将|用户可自行|自行填充|拍摄前填写)", text))
        ):
            issues.append("用待填空白模板冒充可直接使用的成果")
        confirmation_section = re.search(
            r"#{1,6}\s*[^\n]*(?:待你确认|请你确认|需要你补充)[^\n]*\n(?P<body>[\s\S]*?)(?=\n#{1,6}\s|\Z)",
            text,
        )
        if confirmation_section:
            requested_items = re.findall(r"(?m)^\s*(?:[-*]|\d+[.)、])\s+", confirmation_section.group("body"))
            if len(requested_items) > 1:
                issues.append("一次向用户索取多个私人或选择信息")
    if "同账号此前用户原话" in known_context and re.search(
        r"(?:专业背景|职业背景|专业名称).{0,16}(?:未知|没有提供|未提供)",
        text,
    ):
        issues.append("遗忘同一账号此前已经明确的身份或职业信息")
    known_minutes = re.search(r"(?:工作日|每天|每日).{0,12}(\d{1,3})\s*分钟", known_context)
    if known_minutes:
        allowed_minutes = int(known_minutes.group(1))
        imposed_hours = [
            float(value)
            for value in re.findall(r"(?:每日|每天|周一至周五)[^\n|]{0,20}(\d+(?:\.\d+)?)\s*小时", text)
        ]
        if any(hours * 60 > allowed_minutes for hours in imposed_hours):
            issues.append("生成计划违反用户已经明确的每日可用时间")
    return issues


def sanitize_unsupported_creator_claims(content: str) -> str:
    """Remove a small set of unsafe claim forms without discarding useful material."""
    text = str(content or "")
    replacements = (
        (r"每回都考", "本期将练习"),
        (r"必考", "需要掌握"),
        (r"高频(?:错误)?", "待验证的错误"),
        (r"最常见(?:的)?", "待验证的"),
        (r"最容易(?:的)?", "需要检查是否"),
        (r"重灾区", "需要重点观察的环节"),
        (r"错误率最高", "错误率待核实"),
        (r"(?:两个|三个|这些)?常见出错位置", "本题需要检查的位置"),
        (r"(?:最常见|常见)(?:的)?错误", "需要验证的错误"),
        (r"很多同学会忘", "本期检查是否遗漏"),
        (r"大多数学生", "目标学生"),
        (r"普遍都会错", "具体错误比例待核实"),
        (r"十题错[七八九]题", "具体错误比例待核实"),
        (r"(?:以后|从此|保证)?不会再丢分", "用于检查作答过程"),
        (r"(?:以后|从此|保证)?不会再错", "用于检查是否出错"),
        (r"拿满分|保证得分|稳拿分", "记录作答结果"),
        (r"不会漏|不漏不错", "检查是否遗漏"),
        (r"(?:完播|涨粉).{0,12}(?:自然|一定|保证)", "平台结果发布后验证"),
        (r"(?:稍后再看|自己播放|自播).{0,12}(?:自测|测试|提高).{0,8}完播率", "从频道后台记录完播率"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(
        r"(?:播放|完播).{0,12}(?:或|和|以及).{0,12}(?:评论|理解证据).{0,18}(?:高于|不低于|低于).{0,8}基线",
        "播放指标只与同名历史基线比较；评论理解证据没有自身基线时只记录",
        text,
    )
    text = re.sub(
        r"(?:评论|理解证据).{0,12}(?:或|和|以及).{0,12}(?:播放|完播).{0,18}(?:高于|不低于|低于).{0,8}基线",
        "评论理解证据没有自身基线时只记录；播放指标只与同名历史基线比较",
        text,
    )
    return text


def sanitize_user_research_pushback(content: str) -> str:
    """Rewrite "go check it yourself" phrasing into assistant-owned follow-up work.

    The artifact validator rejects handouts that push public-information
    research back to the learner; this deterministic pass lets the rest of an
    otherwise acceptable handout survive instead of failing generation.
    """
    text = str(content or "")
    for pattern, replacement in _USER_RESEARCH_PUSHBACK_REWRITES:
        text = pattern.sub(replacement, text)
    return text


def sanitize_v2_unowned_timing(content: str, instruction: str) -> str:
    """Remove only fixed production timing that the user never supplied."""
    context = str(instruction or "")
    if "统一工作简报：" not in context:
        return str(content or "")
    owned_context = context.split("不得猜测用户可投入时间", 1)[0]
    if re.search(
        r"(?:视频|成片|口播|时长|长度)[^。\n]{0,18}\d{1,3}\s*(?:[-–—至到]\s*\d{1,3}\s*)?分钟",
        owned_context,
    ):
        return str(content or "")
    text = str(content or "")
    text = re.sub(
        r"看到\s*\d{1,3}\s*(?:秒|分钟)处",
        "看到关键讲解段落结束",
        text,
    )
    text = re.sub(r"\d{1,3}\s*(?:秒|分钟)处", "关键节点", text)
    text = re.sub(r"(?:停留|静止|停顿)(?:至少)?\s*\d{1,3}\s*秒", "按内容需要短暂停顿", text)
    text = re.sub(r"\d{1,3}\s*[-–—至到]\s*\d{1,3}\s*分钟", "可缩放时长", text)
    text = re.sub(r"\d{1,3}\s*分钟", "可缩放时长", text)
    text = re.sub(r"\d{1,3}\s*秒", "短暂停留", text)
    return text


def _safe_int(value, default: int = 0) -> int:
    """安全 int 转换：支持 str/int/None；字符串非数字时回退 default。"""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class DocumentAgent(BaseAgent):
    """根据知识点和学生画像生成个性化讲解文档（Day 6: 继承基类统一降级）"""

    AGENT_NAME = "document_agent"

    @staticmethod
    def _profile_context(profile: Dict[str, Any]) -> str:
        """Render only profile facts that actually exist.

        Missing numeric fields used to default to 3/5, which turned absence of
        evidence into invented learner ability inside generated artifacts.
        """
        rows = []
        labels = {
            "major": "专业",
            "grade_level": "年级",
            "math_foundation": "数学基础（用户资料值）",
            "programming_exp": "编程经验（用户资料值）",
            "learning_style": "学习风格",
        }
        for key, label in labels.items():
            value = profile.get(key)
            if value not in (None, "", [], {}):
                rows.append(f"- {label}：{value}" + ("/5" if key in {"math_foundation", "programming_exp"} else ""))
        return "\n".join(rows) if rows else "- 尚无已确认画像；不得补默认能力、专业或偏好。"

    # ============= 模板方法 =============

    def _extract_input(self, state: AgentState) -> Dict[str, Any]:
        return {
            "topic": state.get("chapter_id") or state.get("user_query", ""),
            "instruction": state.get("workspace_instruction", ""),
            "evidence_verified": state.get("workspace_evidence_verified") is True,
            "detail_level": state.get("workspace_detail_level", "简明"),
            "profile": state.get("student_profile"),
            "messages": state.get("messages", []),
            "user_id": _safe_int(state.get("user_id"), 0),
        }

    def _build_result(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"document": output_data}

    # ============= mock 模式 =============

    async def _mock_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        llm = self.mock_llm
        profile = input_data["profile"] or {}

        prompt = f"""你是一位严谨的学习资料编辑。
为以下学生生成关于「{input_data['topic']}」的个性化讲解文档。

学生画像：
{self._profile_context(profile)}
- 薄弱点：{', '.join(profile.get('weak_points', ['未知']))}
- 兴趣方向：{', '.join(profile.get('interests', []))}

要求（严格按Markdown格式）：
1. # 标题：知识点名称
2. ## 核心内容：用通俗易懂的语言解释；只有画像中存在对应基础时才据此调整深度
3. ## 用户要求：优先落实用户明确要求的对比、计划、题目或材料形式
4. ## 实际应用：只在与主题相关时提供可执行示例，不默认加入数学或代码
5. ## 待核实信息：现实世界事实没有可靠来源时明确留空或标注待核实
6. ## 常见误区：列出初学者容易犯的错误
7. ## 总结与思考题：2-3个引导性问题

字数要求：2000-3000字
"""

        result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=f"你擅长将复杂概念简单化，特别擅长为{profile.get('grade_level', '')}学生定制教学内容。"
        )
        content = result["content"] or self._fallback_content(input_data["topic"])
        return {
            "content": content,
            "title": input_data["topic"],
            "topic": input_data["topic"],
            "difficulty": self._assess_difficulty(profile),
            "estimated_time": max(1, len(content) // 15),
            "tags": [input_data["topic"], profile.get("major", "")],
        }

    # ============= real 模式 =============

    async def _real_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        profile = input_data["profile"] or {}
        detail_level = input_data.get("detail_level") or "简明"
        instruction = (input_data.get("instruction") or "").strip()
        length_rule = {
            "速览": "控制在 600-1000 个中文字符，保留 4-6 个最重要的小节，适合快速复习。",
            "详细": "控制在 1800-3000 个中文字符，按主题需要系统展开。",
        }.get(detail_level, "控制在 1000-1800 个中文字符，信息完整但不过度展开。")

        has_verified_sources = input_data.get("evidence_verified") is True or "请严格依据以下资料片段" in instruction
        prompt = f"""{skill_prompt_prefix("resource-art-direction", "lecture-authoring")}你是一位严谨的学习资料编辑。请生成关于「{input_data['topic']}」的 Markdown 学习讲义。

学生画像（只含已确认字段）：
{self._profile_context(profile)}

任务简报与约束：{instruction or '生成结构清晰、便于学习的讲义。'}
篇幅与深度：{length_rule}

必须遵守：
1. 用户明确要求优先于默认结构；不要擅自加入数学推导、代码或无关章节。
2. 如果用户要求“对比表”或“表格”，必须输出真正的 Markdown 表格。
3. 内容应围绕主题，事实表述克制；不确定的结论要明确说明。
4. 直接输出 Markdown 正文，不要解释生成过程，不要复述这些要求。
5. 不得把缺失画像补成默认分数、专业、能力或偏好；用户没有说过的个人信息必须保持未知。
6. 没有来源材料时，不得把院校招生、考试科目、报录比、招聘限制、就业趋势等现实信息写成已核实事实；可列为待核对项，但不得伪装成针对用户的结论。
7. “发布后验证卡”只适用于短视频脚本、频道运营等创作者平台内容；学科讲义、练习、备考资料一律不附验证卡，也不得出现完播率、涨粉、频道后台、观众评论、播放指标等平台运营概念。任何资料都不得编造平台规律、考试频率、受众错误分布或学习效果保证。
8. 简报中的 confirmed_facts 才能称为“已确认”；observed_facts 只能称为“用户自然提到”或“本轮原话”。
9. 用户未给视频/成片时长时，不得擅自设定分钟数；用可缩放段落和镜头顺序表达。信息不足时最多问一个最影响下一版的问题，其余列为未知但不要求用户本轮逐项补齐。
10. 用户要可直接使用或可直接拍摄的版本时，正文必须已经写完整；不能把多个待填字段、空表或“用户自行填充”的通用模板当成未完成的初稿。缺少具体主题时，自行选择一个低风险、可替换的示例主题并标成工作假设，仍交付完整正文。
11. 这是一份给学生看的资料，语气自然直接：合规与不确定性说明集中放在文末，最多一小节、两三句话；不要堆叠“本包不声称／本包不替你设定／不虚构”式声明，也不要在正文罗列被禁用的表述或改写对照表。
12. 面向用户的文字不使用内部代号（V0、工作对象、任务简报、能力路由等）；表达“可修改的初版”时直接写“第一版（可修改）”。“待核实清单”和“向用户提的问题”各自全文只出现一次，放在文末，不要在每个部分重复。
"""

        logger.info(
            f"[document_agent] 调用 real LLM: provider={self.llm.provider.value}, "
            f"timeout={max(self.llm.timeout_sec, 90)}s"
        )

        effective_timeout = max(self.llm.timeout_sec, 90)
        response_text = await self.llm.generate(
            prompt=prompt,
            temperature=0.4,
            max_tokens=1800 if detail_level == "速览" else 3500,
            timeout=effective_timeout,
        )
        content = self.llm._validate_response(response_text)
        content = sanitize_user_research_pushback(content)
        issues = artifact_cognition_issues(
            content,
            has_verified_sources=has_verified_sources,
            instruction=instruction,
        )
        if issues:
            logger.warning("[document_agent] artifact cognition repair: %s", "；".join(issues))
            repair_prompt = f"""你是资料认知质检编辑。下面的讲义不能直接交付，问题是：{'；'.join(issues)}。

请完整重写讲义并严格做到：
1. 只把用户原话和已确认画像当作个人事实；不得推断学校层次、毕业时间、能力、优势、资格或偏好。
2. 没有可引用来源时，招生政策、典型考试组合、考试时间、分数线、岗位资格、招录数量等只能列入“待核实清单”，不得用常识举例填表；清单应说明由助手后续核对哪些官方渠道，不能出现“你自行查”。
3. 方向和岗位建议必须写成带前提的比较，不使用“首选、最适合、必须、别碰”等绝对结论。
4. 只有用户明确提供可用时间时，行动计划才使用对应时间边界；未提供时给可缩放最小版、完成信号和减量方案，不虚构天数或工时。只有用户自己提到休学、复学等中断就学的情形时，才把学制/复学安排列入待核实；不得主动向普通在校学生提出复学类条目。
5. 任何资料不得把“必考、高频、最常见、常见出错、自然涨粉、十题错八题”等写成事实，除非附有直接来源或用户频道数据；不得承诺“拿满分、不会漏、不会再错/丢分”。学科学习资料删除一切发布后验证卡、完播率、频道后台、观众评论内容；验证卡只用于短视频/频道运营类创作者内容，且平台表现必须写成发布后待验证的假设，不能用观众端自播冒充创作者数据分析。
6. 创作者平台内容（脚本、涨粉、完播等）除脚本外才需要一张“发布后验证卡”：本期只改变一个教学变量，记录发布前假设、频道后台可见指标和评论中的理解证据。每个指标只能与同一指标的用户历史基线比较；没有对应基线时只记录、不判定高低。不得承诺增长或把一次变化归因给单一因素。
7. 如果生成练习，给教师保留简短答案与判定要点；保留原稿中有用的结构和行动建议，直接输出完整 Markdown 正文。
8. 简报里的 observed_facts 不得改写成“已确认需求/目标/身份”；只有 confirmed_facts 才能这样称呼。
9. 用户未提供成片时长时，删除擅自设置的分钟数，改为可缩放段落顺序；最多保留一个会显著改变下一版的用户问题，其余未知项留空，不集中索取。
10. 如果用户要可直接使用/拍摄的版本，必须用一个明确标注、可替换的低风险假设补齐完整正文，删除多个待填字段、空表和要求用户自行填充的通用模板。
11. 语气自然直接：合规与不确定性说明集中放在文末最多一小节，不堆叠“本包不声称／本包不替你设定”式声明，不罗列禁用表述或改写对照表；不使用 V0、工作对象、任务简报等内部代号；“待核实清单”和“向用户提的问题”各自全文只保留一次，放在文末。

统一任务简报与本轮要求（其中已确认与自然提到必须分开）：
{instruction}

原稿：
{content}
"""
            repaired_text = await self.llm.generate(
                prompt=repair_prompt,
                temperature=0.2,
                max_tokens=1800 if detail_level == "速览" else 3500,
                timeout=effective_timeout,
            )
            repaired = self.llm._validate_response(repaired_text)
            repaired = sanitize_user_research_pushback(repaired)
            if not has_verified_sources:
                repaired = sanitize_unsupported_creator_claims(repaired)
            repaired = sanitize_v2_unowned_timing(repaired, instruction)
            remaining = artifact_cognition_issues(
                repaired,
                has_verified_sources=has_verified_sources,
                instruction=instruction,
            )
            if remaining:
                creator_matches = [
                    match.group(0)
                    for pattern in _UNVERIFIED_CREATOR_PATTERNS
                    if (match := pattern.search(repaired)) is not None
                ]
                logger.warning(
                    "[document_agent] artifact cognition remaining creator matches: %s",
                    creator_matches,
                )
                raise ValueError("artifact cognition validation failed: " + "；".join(remaining))
            content = repaired
        verification = await self._verify_claims_with_sources(content)
        if verification.get("repaired_content"):
            content = str(verification.pop("repaired_content"))
        illustration = await self._compose_illustration(
            content, instruction, theme=str(input_data.get("theme") or ""),
        )
        if illustration.get("markdown"):
            content = self._insert_illustration(
                content, str(illustration["markdown"]), str(illustration.get("section") or ""),
            )
        return {
            "content": content,
            "verification": str(verification.get("summary") or ""),
            "title": input_data["topic"],
            "topic": input_data["topic"],
            "difficulty": self._assess_difficulty(profile),
            "estimated_time": max(1, len(content) // 15),
            "tags": [input_data["topic"], profile.get("major", "")],
        }

    _MAX_VERIFIED_CLAIMS = 3

    async def _verify_claims_with_sources(self, content: str) -> Dict[str, Any]:
        """检索核对回路：提取可核验断言 → 有界检索 → 核对判定 → 必要时定向返修一次。

        任何一步失败都降级为"未执行核对"，不阻断交付；未核实内容保持既有待核实规则。
        """
        empty: Dict[str, Any] = {"checked": 0, "supported": 0, "problems": 0, "summary": ""}
        try:
            raw_claims = await self.llm.generate(
                prompt=(
                    "从下面的学习资料中提取最多 3 条最值得外部来源核验的具体事实断言"
                    "（数字、年份、公式适用条件、考试或政策类事实；观点、例题、公式推导本身不算）。"
                    "每行输出一条资料原句，不要改写；没有可核验断言时只输出：无。"
                    + chr(92) * 1 + "n" + chr(92) + "n"
                    + str(content or "")[:6000]
                ),
                temperature=0.0,
                max_tokens=400,
                timeout=max(self.llm.timeout_sec, 60),
            )
            claims = [
                line.strip().lstrip("-•* ").strip()
                for line in str(self.llm._validate_response(raw_claims) or "").splitlines()
            ]
            claims = [c for c in claims if len(c) >= 8][: self._MAX_VERIFIED_CLAIMS]
            if not claims:
                return empty

            from services.tide_search_evidence import SearchContext, build_evidence_bundle

            findings = []
            for claim in claims:
                try:
                    bundle = await build_evidence_bundle(
                        SearchContext(message=claim, query_override=claim[:150]),
                        None,
                        force_search=True,
                    )
                    spans = []
                    for source in list(getattr(bundle, "sources", None) or [])[:2]:
                        text = str(
                            getattr(source, "excerpt", "")
                            or getattr(source, "claim_text", "")
                            or ""
                        ).strip()
                        if text:
                            spans.append(text[:280])
                    findings.append({"claim": claim, "spans": spans})
                except Exception:
                    logger.warning("[document_agent] claim retrieval failed: %s", claim[:40])
                    findings.append({"claim": claim, "spans": []})

            nl = chr(92) + "n"
            evidence_block = nl.join(
                f"断言：{f['claim']}　来源摘录：{' ／ '.join(f['spans']) if f['spans'] else '（无可用来源）'}"
                for f in findings
            )
            judgment_raw = await self.llm.generate(
                prompt=(
                    "你是资料核对编辑。对每条断言判定：supported（来源摘录能直接支撑）、"
                    "unsupported（来源不足以支撑）、conflict（与来源冲突）。"
                    "对 unsupported/conflict 各给一句具体修改指导（改口径、限定条件、删除或改成待核实表述）。"
                    '只输出 JSON 数组：[{"claim":"原句","verdict":"...","fix":"..."}]，不要输出其他文字。'
                    + nl + nl
                    + evidence_block
                ),
                temperature=0.0,
                max_tokens=700,
                timeout=max(self.llm.timeout_sec, 60),
            )
            verdicts = self._parse_claim_verdicts(str(self.llm._validate_response(judgment_raw) or ""), claims)
            if verdicts is None:
                return {**empty, "summary": ""}
            problems = [v for v in verdicts if v.get("verdict") in ("unsupported", "conflict")]
            supported = len([v for v in verdicts if v.get("verdict") == "supported"])
            result: Dict[str, Any] = {
                "checked": len(claims),
                "supported": supported,
                "problems": len(problems),
                "summary": (
                    f"已检索核对 {len(claims)} 条关键断言：{supported} 条有来源支撑；"
                    f"{len(problems)} 条未通过，保持待核实。"
                ),
            }
            if problems:
                fix_block = nl.join(
                    f"- 原句：{v.get('claim', '')}　判定：{v.get('verdict')}　修改指导：{v.get('fix', '')}"
                    for v in problems
                )
                repair_raw = await self.llm.generate(
                    prompt=(
                        "你是学习资料修订编辑。下面资料中有几处断言未通过来源核对，只修正这些问题："
                        "与来源冲突的改成与来源一致；来源不足的要么删除、要么改成明确的待核实表述，不得保留断言口吻。"
                        "其余内容逐字保留，直接输出完整 Markdown 正文。"
                        + nl + nl
                        + fix_block
                        + nl + nl
                        + "原资料：" + nl + nl
                        + str(content or "")[:9000]
                    ),
                    temperature=0.2,
                    max_tokens=3500,
                    timeout=max(self.llm.timeout_sec, 90),
                )
                repaired = str(self.llm._validate_response(repair_raw) or "").strip()
                if len(repaired) >= 300:
                    result["repaired_content"] = repaired
                    result["summary"] = (
                        f"已检索核对 {len(claims)} 条关键断言：{supported} 条有来源支撑，"
                        f"{len(problems)} 条已按核对意见修正。"
                    )
            return result
        except Exception:
            logger.warning("[document_agent] claim verification skipped", exc_info=True)
            return empty

    async def _compose_illustration(
        self, content: str, instruction: str, theme: str = "",
    ) -> Dict[str, Any]:
        """插图技能：为资料配一张与主题匹配、不承载正文文字的教学示意图；失败静默跳过。

        视觉主题只提供色彩与材质（显式传入，默认跟随 kaki）；画面内容、构图和
        落点由内容决定。用户在请求里写明配图/风格要求时以用户为准。
        """
        try:
            from services.illustration_skill import generate_material_illustration

            brief_raw = await self.llm.generate(
                prompt=(
                    "为下面的学习资料构思一张教学示意图：先判断它应解释的空间、过程或对照关系；"
                    "不要因主题或装饰需要而硬塞插图。只输出两行，格式固定为：\n"
                    "画面：<不超过 60 字的中文画面描述（主体、场景、关键元素与位置关系），画面中不要出现任何文字>\n"
                    "章节：<资料中插图画的是哪一小节的内容，原样抄该小节标题；整篇都在讲它就抄第一个小节标题>\n\n"
                    + str(content or "")[:3000]
                ),
                temperature=0.3,
                max_tokens=200,
                timeout=max(self.llm.timeout_sec, 45),
            )
            brief = str(self.llm._validate_response(brief_raw) or "").strip()
            concept = ""
            section = ""
            for line in brief.splitlines():
                clean = line.strip().strip('"“”')
                if clean.startswith("画面："):
                    concept = clean.partition("：")[2].strip()
                elif clean.startswith("章节："):
                    section = clean.partition("：")[2].strip()
            concept = concept.splitlines()[0].strip() if concept else ""
            if len(concept) < 8:
                return {}

            style_hint = ""
            style_match = re.search(r"(?:插[图画]|配图|画风|风格)[:：，,\s]*([^。；;\n]{2,40})", str(instruction or ""))
            if style_match:
                style_hint = style_match.group(1).strip()

            outcome = await generate_material_illustration(
                concept, theme=theme or "kaki", style_hint=style_hint,
            )
            if outcome.get("ok") and outcome.get("url"):
                alt = (concept[:24] or "教学示意图").replace("[", "").replace("]", "")
                return {
                    "markdown": f"![{alt}]({outcome['url']})",
                    "url": outcome["url"],
                    "section": section,
                }
            logger.info("[document_agent] illustration unavailable: %s", outcome.get("reason"))
            return {}
        except Exception:
            logger.warning("[document_agent] illustration skipped", exc_info=True)
            return {}

    # Sections whose text is meant to be read straight through, or memorised
    # word for word. A picture dropped in here interrupts the one thing the
    # learner is trying to hold in their head -- and a generated image can carry
    # stray lettering the model was told not to draw. Measured: a watercolour
    # captioned "Outer funcction" / "vanbille" landed directly under
    # 「口述稿正文（可直接背）」 in an interview script.
    _VERBATIM_SECTION = re.compile(
        r"口述|背诵|逐字|原文|可直接背|照读|台词|讲稿|自述|开场白"
    )

    @staticmethod
    def _insert_illustration(content: str, image_markdown: str, section: str = "") -> str:
        """把插图插到它所解释的小节标题之后；对不上或没有小节时退回第一个标题。"""
        lines = str(content or "").splitlines()
        insert_at = min(2, len(lines))
        wanted = str(section or "").strip()
        headings: list[int] = []
        for index, line in enumerate(lines):
            if re.match(r"^#{2,3}\s+", line.strip()):
                headings.append(index)
        if headings:
            target = headings[0]
            if wanted:
                wanted_units = wanted.replace(" ", "")
                for heading_index in headings:
                    heading_text = re.sub(r"^#{2,3}\s+", "", lines[heading_index].strip())
                    heading_units = heading_text.replace(" ", "")
                    if wanted_units and (wanted_units in heading_units or heading_units in wanted_units):
                        target = heading_index
                        break
            insert_at = target + 1
        # Refuse to interrupt a section that has to be read verbatim.
        target_heading = ""
        for candidate in headings:
            if candidate < insert_at:
                target_heading = re.sub(r"^#{2,3}\s+", "", lines[candidate].strip())
        if target_heading and DocumentAgent._VERBATIM_SECTION.search(target_heading):
            logger.info(
                "[document_agent] illustration kept out of a verbatim section: %s",
                target_heading[:40],
            )
            return str(content or "")
        lines.insert(insert_at, "")
        lines.insert(insert_at + 1, image_markdown)
        lines.insert(insert_at + 2, "")
        return "\n".join(lines)

    @staticmethod
    def _parse_claim_verdicts(raw: str, claims: list):
        """解析核对判定；解析失败返回 None（不返修，保持待核实），避免误判引发改写。"""
        import json

        text = str(raw or "").strip()
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except Exception:
            return None
        if not isinstance(parsed, list):
            return None
        known = set(claims)
        verdicts = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim") or "").strip()
            if claim not in known:
                continue
            verdict = str(item.get("verdict") or "").strip().lower()
            if verdict not in ("supported", "unsupported", "conflict"):
                continue
            verdicts.append({"claim": claim, "verdict": verdict, "fix": str(item.get("fix") or "").strip()})
        return verdicts

    # ============= 工具 =============

    def _assess_difficulty(self, profile):
        level = (profile or {}).get("ml_knowledge")
        if level in (None, ""):
            return "general"
        if level <= 2:
            return "beginner"
        elif level <= 4:
            return "intermediate"
        else:
            return "advanced"

    def _fallback_content(self, topic: str) -> str:
        """mock 库 / real 解析双重失败时的最终兜底"""
        return (
            f"# {topic} 讲义\n\n"
            f"## 概念解释\n\n本节介绍 {topic} 的基本概念。\n\n"
            f"## 核心原理\n\n深入分析 {topic} 的工作原理。\n\n"
            f"## 实战案例\n\n通过实例演示 {topic} 的应用。\n\n"
            f"## 常见误区\n\n列举学习 {topic} 的典型错误。\n"
        )
