# -*- coding: utf-8 -*-
"""拾光漫游 · 游戏导演（Director / GM）v3

流程：教（SCROLL）→ 练（TRIAL / INTERLEAVE）→ 战（BOSS）→ 收（TREASURY）。

导演负责把「主题 + 领域 + 身份 + 进度 + 玩家行为」编排成有温度的剧情帧：
- 敌人会“记仇”：引用玩家答错的原文来嘲讽；
- 柿小吉按领域说梗：成功、连击、受挫各有不同反应；
- BOSS 三阶段台词随玩家战绩变化。

不依赖 LLM：确定性编排，但每个主题、每段战绩都会得到不同的组合。
输出格式与前端消费契约兼容：
  narrative.state_copy[state_key] -> 旁白（前端自动替换）
  narrative.world / opposition     -> 世界规则与敌人档案
  narrative.enemy_lines / mascot_lines -> 对话帧（前端直接消费）
"""
from __future__ import annotations

from typing import Any

# ===== 场景文案库：按领域给每一幕不同的"世界质感" =====
_WORLD_RULES: dict[str, str] = {
    "programming": "在这片由代码与状态构成的世界里，每一段逻辑都要经过输入、执行与检验，才能被信任。",
    "language": "在这片词语与语气交织的原野上，每一句表达都要在对象与语境中站稳，才算真正被理解。",
    "math": "在这座由定义与条件砌成的城池里，每个结论都要说清适用条件，才不会被反例击穿。",
    "science": "在这片以机制与证据为法则的山谷中，每个解释都要经得起控制变量与观察的检验。",
    "humanities": "在这卷以主张与材料为经纬的典籍里，每个观点都要放回语境，用证据和推理支撑。",
    "open": "在这条以理由与证据为路标的小径上，每个结论都要说清为什么、在什么条件下成立、何时不成立。",
}

_ANOMALIES: dict[str, str] = {
    "programming": "一段原本运行流畅的逻辑突然卡死，错误提示闪烁在旧屏幕上。",
    "language": "一句熟悉的表达忽然变得生硬，词与词之间失去了原本的默契。",
    "math": "一道原本成立的推导开始自我矛盾，条件在关键处悄悄松动。",
    "science": "一个曾经平稳的观察结果开始偏离，因果链条在中间断开。",
    "humanities": "一段论述出现裂痕，主张与证据之间露出一道刺眼的缝隙。",
    "open": "一条看似成立的结论开始动摇，证据链在某个环节突然断裂。",
}

# ===== 敌人台词库：按状态/进度给敌人"狡辩"，引用玩家战绩 =====
_ENEMY_TRIAL: list[str] = [
    "知道一个说法就够了，何必还要拿新的情境来验证？",
    "我记住它了，难道这还不算学会？",
    "只要听上去有道理，何必计较条件？",
    "结论我背得出来，你不服吗？",
]
_ENEMY_TRIAL_REVIEW: list[str] = [
    "你刚才那句话，连墨迹都在替你脸红。",
    "说得倒快——可证据没跟上。再来。",
    "我不会让路，除非你把缺口补上。",
]
_ENEMY_INTERLEAVE: list[str] = [
    "题面都换了，刚才那套理解早该作废了。",
    "换了个情境，你还能认出它吗？我看你不行。",
    "一样的字我见过，这里可不一定一样。",
]
_ENEMY_BOSS: list[list[str]] = [
    ["只说结论不够。你能解释它为什么能面对未见情境吗？", "解释一层：别想用一句话糊弄我。"],
    ["线索收起来之后，你还能从记忆里找回关键证据吗？", "回忆一层：空口无凭，证据呢？"],
    ["最后一道雾不会被复述吹散。指出缺口，给出行动。", "迁移一层：换个地方，你还认得它吗？"],
]

# ===== 柿小吉台词库：按领域给梗，成功/连击/受挫各有反应 =====
_MASCOT_SUCCESS: dict[str, list[str]] = {
    "programming": [
        "这条证据通过检验了——你脑子里的逻辑，比电脑里的更难调通。",
        "跑通了！给这条证据打上一个绿色的勾。",
        "循环没白绕，你找到了正确的出口。",
    ],
    "language": [
        "这句话站得很稳，词与词终于不再打架了。",
        "说得自然又准确，这页词典可以合上了。",
        "语气对上了，表达这关你过得漂亮。",
    ],
    "math": [
        "条件、结论、例子——三点一线，这道推导立住了。",
        "反例都绕着你走，这条规则你拿稳了。",
        "城池没被击穿，你守住了适用条件。",
    ],
    "science": [
        "机制讲得清，预测可检验——这组证据很扎实。",
        "控制变量这关你过了，因果链没有断。",
        "山谷里又亮了一盏灯，证据自己会说话。",
    ],
    "humanities": [
        "主张有材料撑腰，论述不再悬空。",
        "你把它放回了语境里，这卷典籍翻对了页。",
        "证据和推理咬合上了，这个观点站得稳。",
    ],
    "open": [
        "为什么、在什么条件下成立——你都答上了。",
        "这条理由链是通的，我挑不出毛病。",
        "证据收齐了，前面的路又亮了一截。",
    ],
}
_MASCOT_REVIEW: dict[str, list[str]] = {
    "programming": [
        "报错不可怕，可怕的是把报错当答案。我们看下一行。",
        "先把条件列全，逻辑自己就会浮出来。",
        "不是重说一遍，是补上缺口——编译前先想清楚。",
    ],
    "language": [
        "别急着重说一遍，先找到语气不对的那一处。",
        "换个说法试试，句子会在语境里自己站稳。",
        "这不算废稿，只是还没到定稿。",
    ],
    "math": [
        "先写适用条件，再写结论——顺序对了，推导就顺了。",
        "缺的那块通常是条件，不是计算。",
        "反例不是敌人，它只是告诉你哪里没锁紧。",
    ],
    "science": [
        "变量没控制住，结论就站不稳。先列条件。",
        "因果链断在哪一环？找到它，接上它。",
        "观察结果会说话，只是你得先把前提讲清。",
    ],
    "humanities": [
        "主张先放一放，把材料摆出来。",
        "语境没还原，观点就是悬空的。先补语境。",
        "证据不够就再找，重复不会让它变多。",
    ],
    "open": [
        "别急着重说一遍，先补上它点破的那一处。",
        "回答的缺口通常藏在“为什么”后面。",
        "我们补的是证据，不是漂亮措辞。",
    ],
}
_MASCOT_STREAK: list[str] = [
    "连中两招！墨迹兽开始打转了。",
    "三连击！它躲进雾里又探出半个头。",
    "势如破竹——这个势头，它会先怕你。",
]
_MASCOT_BOSS_START: list[str] = [
    "塔里那家伙认得你的字迹。用证据说话，别被它带偏。",
    "三道封印，一道一道拆。雾再浓，也浓不过你手里的证据。",
]
_MASCOT_REMEDY: list[str] = [
    "它换了个问法——说明旧路子走不通。从新角度再来。",
    "别怕换题。它只是把同一道缺口换了个样子。",
]

# ===== 过场/奖励文案 =====
_TRAVEL_COPY: dict[str, str] = {
    "SCROLL": "循着谷口的微光出发，卷轴就在前方……",
    "TRIAL": "石径渐窄，墨色正在聚拢……",
    "INTERLEAVE": "把刚才的规则带向陌生路段……",
    "BOSS": "古塔的轮廓从雾中靠近……",
    "TREASURY": "迷雾散去，灯火正沿山路亮起……",
    "completed": "最后一束光回到了旅者手中……",
}


def _domain_of(topic: str, domain_hint: str = "") -> str:
    if domain_hint:
        return domain_hint
    text = str(topic or "").lower()
    if any(t in text for t in ("python", "java", "javascript", "代码", "编程", "循环", "算法", "数据库", "机器学习", "machinelearning", "模型", "数据")):
        return "programming"
    if any(t in text for t in ("英语", "英文", "语法", "写作", "口语", "翻译", "表达", "词汇")):
        return "language"
    if any(t in text for t in ("数学", "函数", "梯度", "概率", "几何", "代数", "微积分", "方程", "向量", "导数")):
        return "math"
    if any(t in text for t in ("物理", "化学", "生物", "实验", "力学", "细胞", "反应", "量子")):
        return "science"
    if any(t in text for t in ("历史", "政治", "哲学", "经济", "文学", "社会", "文化", "三国", "人物")):
        return "humanities"
    return "open"


def _pick(pool: list[str], index: int) -> str:
    return pool[index % len(pool)] if pool else ""


class GameDirector:
    """游戏导演：按主题/领域/身份/进度编排剧情帧。"""

    def __init__(self, topic: str, identity_id: str, domain_hint: str = ""):
        self.topic = str(topic or "").strip()[:80] or "本次主题"
        self.domain = _domain_of(self.topic, domain_hint)
        self.identity_id = identity_id or "wandering_mage"

    # ---- 世界规则 / 敌人档案（进 narrative） ----
    def world(self) -> dict[str, Any]:
        return {
            "world_rule": _WORLD_RULES.get(self.domain, _WORLD_RULES["open"]),
            "anomaly": _ANOMALIES.get(self.domain, _ANOMALIES["open"]),
        }

    def opposition(self) -> dict[str, Any]:
        return {
            "form": "misleading_claim",
            "appearance": "一团依附在关键线索旁的墨色扰动",
            "behaviour": "它不断遮住关键条件、交换相似说法，让错误判断更难被发现。",
            "transformation": "知识行动完成后，墨色扰动从线索上脱落，化作可收藏的旅程印记。",
        }

    # ---- 敌人台词（按状态/进度/玩家战绩） ----
    def enemy_line(self, state: str, *, boss_stage: int = 0, trial_index: int = 0,
                   archive: dict[str, Any] | None = None, attempt: int = 0) -> str:
        archive = archive or {}
        wrong = (archive.get("wrong_details") or [])
        snippet = ""
        if wrong and state in {"TRIAL", "INTERLEAVE", "BOSS"}:
            # 敌人“记仇”：引用玩家在练/战阶段答错的原话
            first = wrong[0]
            raw = str(first.get("snippet") or "")
            if raw:
                snippet = raw if len(raw) <= 18 else raw[:18] + "…"
        if state == "TRIAL":
            if int(attempt or 0) >= 1:
                return _pick(_ENEMY_TRIAL_REVIEW, trial_index) + ("你刚才那句「" + snippet + "」可站不住。" if snippet else "")
            return _pick(_ENEMY_TRIAL, trial_index)
        if state == "INTERLEAVE":
            if snippet:
                return _pick(_ENEMY_INTERLEAVE, trial_index) + " 尤其那句「" + snippet + "」，换个地方它可不一定成立。"
            return _pick(_ENEMY_INTERLEAVE, trial_index)
        if state == "BOSS":
            stage = max(0, min(2, int(boss_stage or 0)))
            line = _pick(_ENEMY_BOSS[stage], trial_index)
            if stage == 0 and snippet:
                line = "我认得你练功时的旧话——「" + snippet + "」。" + line
            elif stage == 1 and int(archive.get("correct_count", 0)) >= 3:
                line = "你前面的证据我看见了，但别想蒙混过关。" + line
            return line
        return "把眼前的证据连起来，我才会让开。"

    # ---- 柿小吉台词（按领域/结果/连击） ----
    def mascot_line(self, kind: str, index: int = 0) -> str:
        if kind == "success":
            return _pick(_MASCOT_SUCCESS.get(self.domain, _MASCOT_SUCCESS["open"]), index)
        if kind == "review":
            return _pick(_MASCOT_REVIEW.get(self.domain, _MASCOT_REVIEW["open"]), index)
        if kind == "streak":
            return _pick(_MASCOT_STREAK, index)
        if kind == "boss_start":
            return _pick(_MASCOT_BOSS_START, index)
        if kind == "remedy":
            return _pick(_MASCOT_REMEDY, index)
        return "先把眼前这一步看清，我们再继续。"

    # ---- 过场文案 ----
    def travel_copy(self, state: str) -> str:
        return _TRAVEL_COPY.get(state, "沿着微光继续前行……")

    # ---- 每幕旁白（state_copy，前端自动消费） ----
    def state_copy(self, state: str, archive: dict[str, Any], content: dict[str, Any]) -> dict[str, str]:
        concept = (content.get("concept") or {}).get("name") or self.topic
        mis = (content.get("misconception") or {}).get("claim") or ""
        rule = (content.get("scroll") or {}).get("rule") or ""
        world_rule = _WORLD_RULES.get(self.domain, _WORLD_RULES["open"])
        anomaly = _ANOMALIES.get(self.domain, _ANOMALIES["open"])
        topic = self.topic
        identity_name = archive.get("identity_name", "云游符咒师")

        opening = (
            f"旧墨从草叶间升起——{anomaly}"
            f"守路人循着「{topic}」最易误会之处，把规则卷轴留在了你面前。"
        )
        first_trial = (
            f"{anomaly}墨色聚成一只拦路的兽，它的眼睛盯着你的答案打转："
            f"{self.enemy_line('TRIAL', archive=archive)}"
            f"它想让你把「{topic}」当成一句背出来的话。"
        )
        interleave = (
            f"道路在前方变了模样，规则要换一种情境重新面对。"
            f"墨迹兽的虚影跟了上来：{self.enemy_line('INTERLEAVE', archive=archive)}"
            f"它要看你是不是真的带走了「{topic}」，而不是只记住了表面。"
        )
        boss_reveal = (
            f"古塔在雾中亮起，三道封印依次浮现：解释、辨析、迁移。"
            f"墨魇之主的声音压下来：{self.enemy_line('BOSS', archive=archive)}"
            f"它要把你对「{topic}」的理解一层层拆开检验。"
        )
        resolution = (
            f"三道封印一一散去，墨魇之主的虚影化作尘埃。"
            f"丰穗谷的灯火沿山路亮起，你手中多了一枚记载全程的旅印——"
            f"「{topic}」不再是陌生的名字了。作为{identity_name}，你把它真正带走了。"
        )
        return {
            "opening": opening,
            "first_trial": first_trial,
            "interleave": interleave,
            "boss_reveal": boss_reveal,
            "resolution": resolution,
        }

    # ---- 完整 narrative（前端契约） ----
    def narrative(self, content: dict[str, Any], archive: dict[str, Any]) -> dict[str, Any]:
        wrong = (archive.get("wrong_details") or [])
        return {
            "world": self.world(),
            "opposition": self.opposition(),
            "state_copy": self.state_copy("", archive, content),
            "enemy_lines": {
                "trial": self.enemy_line("TRIAL", archive=archive),
                "trial_review": self.enemy_line("TRIAL", archive=archive, attempt=1),
                "interleave": self.enemy_line("INTERLEAVE", archive=archive),
                "boss": [self.enemy_line("BOSS", boss_stage=stage, archive=archive) for stage in range(3)],
            },
            "mascot_lines": {
                "success": self.mascot_line("success"),
                "review": self.mascot_line("review"),
                "streak": self.mascot_line("streak"),
                "boss_start": self.mascot_line("boss_start"),
                "remedy": self.mascot_line("remedy"),
            },
            "memory": {
                "wrong_count": len(wrong),
                "first_wrong_snippet": (wrong[0].get("snippet") if wrong else "") or "",
            },
        }
