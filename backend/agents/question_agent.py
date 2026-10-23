"""
QuestionAgent - Day 6 重构版（移除重复初始化和降级代码）

基类 BaseAgent 已统一处理：
- LLM 服务初始化（self.llm / self.mock_llm）
- mock/real 切换 + 失败自动降级
- 降级统计与告警

子类只需：
1. 设置 AGENT_NAME
2. 实现 _extract_input / _mock_generate / _real_generate
3. 实现 _build_result

═══════════ 接口契约（与 routers/agents_day4.py 对齐）══════════
- 输出字段：questions (List[Dict])
  - 每项：id, type, question, options?, answer, explanation, difficulty, knowledge_point, hints
══════════════════════════════════════════════════════════
"""
import json
import logging
from typing import Dict, Any, List

from graph.state import AgentState
from agents import BaseAgent

logger = logging.getLogger("rjianb.question")


def _safe_int(value, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class QuestionAgent(BaseAgent):
    """生成个性化练习题（Day 6: 继承基类统一降级）"""

    AGENT_NAME = "question_agent"

    # ============= 模板方法 =============

    def _extract_input(self, state: AgentState) -> Dict[str, Any]:
        return {
            "topic": state.get("user_query", ""),
            "profile": state.get("student_profile"),
            "difficulty": self._get_difficulty_from_profile(
                state.get("student_profile")
            ),
            "user_id": _safe_int(state.get("user_id"), 0),
            "question_count": _safe_int(state.get("question_count"), 5),
            "material_context": state.get("material_context", ""),
        }

    def _get_difficulty_from_profile(self, profile):
        if profile and "ml_knowledge" in profile:
            level = profile["ml_knowledge"]
            if level <= 2:
                return "基础"
            elif level <= 4:
                return "进阶"
            else:
                return "高级"
        return "中级"

    def _build_result(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"questions": output_data["questions"]}

    # ============= mock 模式 =============

    async def _mock_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        llm = self.mock_llm
        # P0 修复 (Bug3): workflow 并行节点传的 shared_input 没有 difficulty 字段
        # 旧逻辑 input_data['difficulty'] 直接 KeyError
        topic = input_data.get("topic") or input_data.get("user_query") or "机器学习"
        difficulty = input_data.get("difficulty") or self._get_difficulty_from_profile(
            input_data.get("profile") or input_data.get("student_profile")
        )
        profile = input_data.get("profile") or input_data.get("student_profile") or {}
        question_count = _safe_int(input_data.get("question_count"), 5)
        material_context = input_data.get("material_context", "")

        material_hint = ""
        if material_context:
            material_hint = f"""\n\n【参考资料内容】\n{material_context[:3000]}\n\n请严格基于以上资料内容出题，题目中的知识点必须来自资料。"""

        prompt = f"""你是一个机器学习教学专家。为以下学生生成关于「{topic}」的练习题。

学生水平：{difficulty}
薄弱点：{', '.join(profile.get('weak_points', []))}
偏好编程语言：{profile.get('preferred_language', 'Python')}{material_hint}

请生成{question_count}道不同类型的题目（JSON格式）：
[
  {{
    "id": "q1",
    "type": "choice",
    "question": "单选题题目",
    "options": ["A选项", "B选项", "C选项", "D选项"],
    "answer": "B",
    "explanation": "详细解析",
    "difficulty": 2,
    "knowledge_point": "对应知识点",
    "hints": ["提示1", "提示2"]
  }},
  // ... 共{question_count}题，包含 choice/fill_blank/coding/case_study 类型
]

注意：
1. 题目难度要循序渐进
2. 要覆盖该知识点的核心概念
3. 解析要详细易懂
4. 编程题要有完整可运行代码
5. 严格生成{question_count}道题，不多不少
"""

        uid = int(input_data.get("user_id") or 0)
        from services.skill_library import skill_prompt_prefix
        result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=skill_prompt_prefix("question-authoring") + "你擅长设计有针对性的练习题，能根据学生水平调整难度。请严格返回JSON格式的题目列表。",
            cache_agent="quiz",
            cache_chapter=str(input_data.get("topic", ""))[:32],
            cache_stage="agent",
            cache_profile_hash=str(uid),
            cache_user_id=uid,
            use_cache=False,
            write_cache=False,
        )

        questions = self._parse_questions_text(result.get("content", ""))
        if not questions:
            questions = self._fallback_questions(topic)
        return {"questions": questions}

    # ============= real 模式 =============

    async def _real_generate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        # P0 修复 (Bug3): 同样防御 KeyError
        topic = input_data.get("topic") or input_data.get("user_query") or "机器学习"
        difficulty = input_data.get("difficulty") or "中级"
        profile = input_data.get("profile") or input_data.get("student_profile") or {}

        prompt = f"""为以下学生生成 5 道关于「{topic}」的练习题。

学生水平：{difficulty}
薄弱点：{', '.join(profile.get('weak_points', []))}

**严格返回 JSON**（不要 markdown 包裹），格式：
{{
  "questions": [
    {{
      "id": "q1",
      "type": "choice",
      "question": "题目",
      "options": ["A", "B", "C", "D"],
      "answer": "B",
      "explanation": "解析",
      "difficulty": 2,
      "knowledge_point": "对应知识点",
      "hints": ["提示1"]
    }}
  ]
}}

类型覆盖：choice / fill_blank / coding / case_study 至少各 1 道。
"""

        logger.info(
            f"[question_agent] 调用 real LLM: provider={self.llm.provider.value}, "
            f"timeout={self.llm.timeout_sec}s"
        )

        response_text = await self.llm.generate(
            prompt=prompt,
            temperature=0.4,
            max_tokens=3000,
            timeout=self.llm.timeout_sec,
            response_format="json",
        )
        parsed = self.llm._extract_json(response_text)
        questions = parsed.get("questions", [])
        if not isinstance(questions, list) or not questions:
            questions = self._fallback_questions(topic)
        return {"questions": questions}

    # ============= 工具 =============

    def _parse_questions_text(self, raw: str) -> List[Dict[str, Any]]:
        """解析 LLM 文本输出为 questions 列表（与 Day 3 兼容）"""
        if not raw:
            return []
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        try:
            questions = json.loads(raw)
        except Exception as parse_err:
            logger.warning(
                f"[{self.AGENT_NAME}] JSON解析失败: {parse_err}, 使用原始文本"
            )
            return [{
                "id": "q1",
                "type": "text",
                "question": raw[:500],
            }]
        if not isinstance(questions, list):
            questions = [questions]
        return questions

    def _fallback_questions(self, topic: str) -> List[Dict[str, Any]]:
        return [
            {
                "id": "q1",
                "type": "choice",
                "question": f"以下哪个不是「{topic}」的核心概念？",
                "options": ["A. 概念1", "B. 概念2", "C. 概念3", "D. 概念4"],
                "answer": "D",
                "explanation": f"该选项不属于 {topic} 的核心概念。",
                "difficulty": 2,
                "knowledge_point": topic,
                "hints": ["回顾核心定义"],
            },
            {
                "id": "q2",
                "type": "fill_blank",
                "question": f"在 {topic} 中，关键步骤是 ______。",
                "options": None,
                "answer": "数据预处理",
                "explanation": "数据预处理是机器学习项目中的关键步骤。",
                "difficulty": 2,
                "knowledge_point": topic,
                "hints": ["数据是关键"],
            },
        ]
