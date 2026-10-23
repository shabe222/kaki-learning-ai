# -*- coding: utf-8 -*-
"""拾光漫游 · 新冒险演示状态机（demo）

四阶段闭环（比赛演示专用）：
  1. dialogue  背景1·回声书阁：柿小吉（智能体）与玩家六轮对话，介绍世界观
  2. chest     背景2·拾光宝库：柿小吉教知识 → 选择题 → 答对开宝箱
  3. minion    背景3·雾谷：柿小吉教知识 → 选择题 → 答对打倒小怪（旁白记线索）
  4. boss      背景4·封印古塔：柿小吉提示线索 → 简答题 → 答对通关
  5. victory   胜利结算

演示主题固定为「机器学习与模型评估」（知识点：训练/验证/测试分工、过拟合、
减少过拟合的办法）。判定复用 judge.py；对话生成复用 companion.py。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

from .companion import companion_reply
from .judge import evaluate, teaching_feedback
from .brain import build_content, build_content_llm

DEMO_TOPIC = "机器学习与模型评估"

# ===== 演示用题库（与主题知识闭环，每阶段多题渐进） =====
# 宝箱题（2 题：三份数据分工 → 数据用途辨析）
_CHEST_QUESTIONS = [
    {
        "type": "multiple_choice",
        "question": "要公平地评估一个模型，三份数据应该怎么分工？",
        "options": ["训练集学习、验证集选型、测试集终评", "训练集终评、测试集调参", "验证集只用来保存文件"],
        "correct_option": 0,
        "hint": "训练集学规律，验证集挑方案，测试集只做最终评估。",
        "rationale": "检验三份数据的分工理解",
        "answer_explanation": "训练集学习、验证集选型、测试集终评才是正确分工。",
    },
    {
        "type": "multiple_choice",
        "question": "选型阶段反复用测试集看结果再调参，会带来什么问题？",
        "options": ["测试集失去最终评估的意义", "没有任何问题", "会让训练更快"],
        "correct_option": 0,
        "hint": "测试集应该只在方案确定后使用一次。",
        "rationale": "检验数据分工的边界意识",
        "answer_explanation": "反复用测试集调参，会让它被模型'记住'，最终评估就失真了。",
    },
]
# 小怪题（3 只墨迹兽，越来越难：过拟合识别 → 欠拟合识别 → 泛化解释）
_MINION_QUESTIONS = [
    {
        "type": "multiple_choice",
        "question": "第一只墨迹兽得意地喊：'我训练成绩 99 分！'——但它没见过的新题只有 72 分。它最可能是？",
        "options": ["过拟合：把训练题背下来了", "它太聪明了", "新题一定超纲了"],
        "correct_option": 0,
        "hint": "训练好、未见数据差，是过拟合的典型信号。",
        "rationale": "检验过拟合现象的识别",
        "answer_explanation": "训练远好于未见数据，说明模型记住了训练集——过拟合。",
    },
    {
        "type": "multiple_choice",
        "question": "第二只墨迹兽耷拉着耳朵：'我连训练题都答不对……'它最可能是？",
        "options": ["欠拟合：还没学会训练数据里的规律", "过拟合：背得太牢", "数据完全无噪声"],
        "correct_option": 0,
        "hint": "连训练集都没学好，通常是欠拟合。",
        "rationale": "检验欠拟合的识别",
        "answer_explanation": "训练集表现差通常是欠拟合——模型还没学会训练数据里的规律。",
    },
    {
        "type": "short_answer",
        "question": "第三只墨迹兽拦路：'那你告诉我，为什么不能只看训练数据上的表现？'",
        "accepted_groups": [["未见", "新数据", "测试", "泛化"], ["过拟合", "记住", "背"]],
        "hint": "想想：背下训练样本和真正学会规律有什么不同。",
        "rationale": "检验训练与泛化的区别",
        "answer_explanation": "训练表现好只说明学到了训练集；真正可靠的判断要用未见数据检验泛化能力。",
    },
]
# 小怪身上的线索（BOSS 旁白引用）
_MINION_CLUE = "你在墨迹兽散去的墨雾里捡到一条线索：『训练好≠泛化好，要防过拟合』。"
# BOSS 题（3 阶段：解释 → 辨析 → 迁移）
_BOSS_QUESTIONS = [
    {
        "type": "short_answer",
        "question": "墨魇之主沉声发问（第一问·解释）：请给出一种减少过拟合、改善泛化的可执行办法。",
        "accepted_groups": [["正则", "交叉验证", "更多数据", "简化模型", "早停", "数据增强"]],
        "hint": "回想一路的线索：可以从数据、模型复杂度或训练停止时机入手。",
        "rationale": "检验能否把'防过拟合'落到可执行动作",
        "answer_explanation": "正则化、交叉验证、增加数据、简化模型、早停或数据增强，都能改善泛化。",
    },
    {
        "type": "multiple_choice",
        "question": "墨魇之主的雾影翻涌（第二问·辨析）：调参选型与最终评估，分别主要使用什么数据？",
        "options": ["验证集、测试集", "测试集、训练集", "训练集、验证集"],
        "correct_option": 0,
        "hint": "调参在验证集上试，最终评估才用测试集。",
        "rationale": "检验数据分工的辨析",
        "answer_explanation": "调参选型用验证集，最终评估用测试集。",
    },
    {
        "type": "short_answer",
        "question": "墨魇之主的封印亮起最后一层（第三问·迁移）：给一个你熟悉的场景，说说你会怎样用数据分工来评估模型。",
        "accepted_groups": [["训练", "学习"], ["验证", "选"], ["测试", "评估"]],
        "hint": "按职责分工说一遍三份数据各自做什么。",
        "rationale": "检验能否把数据分工迁移到具体场景",
        "answer_explanation": "训练集学习、验证集选型、测试集最终评估，才是完整的评估流程。",
    },
]
# BOSS 阶段名（前端展示：解释/辨析/迁移）
_BOSS_STAGE_NAMES = ["解释", "辨析", "迁移"]


def _choice_from(task: dict[str, Any]) -> dict[str, Any]:
    """把 teaching_check 转成选择题结构。"""
    return {
        "type": "multiple_choice",
        "question": task.get("question", ""),
        "options": list(task.get("options") or []),
        "correct_option": int(task.get("correct_option", 0)),
        "hint": task.get("hint", ""),
        "rationale": task.get("rationale", "检验理解确认"),
        "answer_explanation": task.get("answer_explanation", ""),
    }


def _banks_from_content(content: dict[str, Any]) -> tuple[list[dict], list[dict], list[dict]]:
    """从 build_content 的任意主题蓝图抽取 宝箱/小怪/BOSS 题库。

    宝箱：理解确认题（选择题）
    小怪：题库中的选择题（最多 3 只，渐进）
    BOSS：解释/辨析/迁移三阶段
    """
    def _extract_tasks(group: dict) -> list[dict]:
        """兼容 trials 与 trial 两种键名。"""
        tasks: list[dict] = []
        for key in ("trials", "trial", "interleaves", "interleave"):
            item = group.get(key)
            if isinstance(item, list):
                tasks.extend(item)
            elif isinstance(item, dict):
                tasks.append(item)
        return tasks

    # 选择题池
    choices: list[dict[str, Any]] = []
    check = (content.get("scroll") or {}).get("teaching_check")
    if check and check.get("options"):
        choices.append(_choice_from(check))
    for group in ("understanding", "practice"):
        deck = content.get(group) or {}
        for task in _extract_tasks(deck):
            if task.get("type") == "multiple_choice" and task.get("options"):
                choices.append(task)

    chest = [choices[0]] if choices else [_FALLBACK_CHEST]

    # 小怪：从选择题池去重，最多 3 只
    seen = set()
    minion: list[dict[str, Any]] = []
    for task in choices[1:]:
        q = str(task.get("question") or "")
        if q in seen:
            continue
        seen.add(q)
        minion.append(task)
        if len(minion) >= 3:
            break
    if not minion:
        minion = [_FALLBACK_MINION]

    # BOSS：取 content.boss，不足补
    boss_pool = content.get("boss") or []
    boss: list[dict[str, Any]] = []
    for task in boss_pool:
        boss.append(task)
        if len(boss) >= 3:
            break
    while len(boss) < 3:
        boss.append(_FALLBACK_BOSS[min(len(boss), len(_FALLBACK_BOSS) - 1)])

    return chest, minion, boss


_FALLBACK_CHEST = {
    "type": "multiple_choice",
    "question": "开始学习前，怎样才算真正理解了这条规则？",
    "options": ["能说清它解决什么问题，并给出可验证的例子", "能背下这句话", "看过一遍就算懂"],
    "correct_option": 0,
    "hint": "理解需要能解释用途，并给出可检验的例子。",
    "rationale": "检验理解确认",
    "answer_explanation": "理解不是背下规则，而是能说清用途并给出可验证的例子。",
}
_FALLBACK_MINION = {
    "type": "multiple_choice",
    "question": "把这条规则用到一个新情境前，最先应确认什么？",
    "options": ["适用条件是否一致", "答案字数是否相同", "页面颜色是否相同"],
    "correct_option": 0,
    "hint": "迁移依赖条件，而不是表面形式。",
    "rationale": "检验迁移意识",
    "answer_explanation": "迁移前要确认适用条件一致，而不是表面形式。",
}
_FALLBACK_BOSS = [
    {"type": "short_answer", "question": "用自己的话解释这条规则的核心含义。", "accepted_groups": [["条件"], ["含义", "作用"]], "hint": "说清是什么、解决什么问题。", "rationale": "检验解释能力", "answer_explanation": "解释要说清核心含义与作用。"},
    {"type": "short_answer", "question": "举一个这条规则不适用的情境，并说明原因。", "accepted_groups": [["条件"], ["不适用", "反例"]], "hint": "找到条件不满足的例子。", "rationale": "检验辨析能力", "answer_explanation": "指出适用条件之外的边界，就是好的辨析。"},
    {"type": "short_answer", "question": "说说你会怎样把这条规则用到一个新问题上。", "accepted_groups": [["条件"], ["步骤", "验证", "例子"]], "hint": "描述条件、动作和验证结果。", "rationale": "检验迁移能力", "answer_explanation": "迁移要说明条件一致、给出动作和验证。"},
]

_STAGE_BG: dict[str, str] = {
    "dialogue": "echo_archive",
    "chest": "harvest_treasury",
    "minion": "mist_valley",
    "boss": "boss_core",
    "victory": "harvest_treasury",
}

_ROLES = (
    ("wandering_mage", "云游符咒师", "惯于把知识讲给同伴听"),
    ("lamp_heir", "守灯人后裔", "擅长把方法做成可操作的机关"),
    ("star_apprentice", "星象台学徒", "偏爱把概念放到陌生情境里检验"),
)


def _now() -> str:
    return datetime.utcnow().isoformat()


class DemoSession:
    """一次新冒险演示会话。"""

    def __init__(self, role_id: str = "wandering_mage", custom_name: str = "", topic: str = ""):
        self.run_id = f"demo_{uuid.uuid4().hex}"
        custom_name = str(custom_name or "").strip()[:12]
        if role_id == "custom" and custom_name:
            self.role = {"id": "custom", "name": custom_name, "style": "以你自己的名字，走进丰穗谷"}
        else:
            role = next((r for r in _ROLES if r[0] == role_id), _ROLES[0])
            self.role = {"id": role[0], "name": role[1], "style": role[2]}
        # 主题：输入主题 → 题目智能体（LLM）生成该主题的学习蓝图；失败降级模板
        self.topic = str(topic or "").strip()[:60] or DEMO_TOPIC
        self.content = build_content(self.topic)
        self._chest_questions, self._minion_questions, self._boss_questions = _banks_from_content(self.content)
        self.knowledge = {
            "rule": (self.content.get("scroll") or {}).get("rule", ""),
            "example": (self.content.get("scroll") or {}).get("example", ""),
            "knowledge_points": self.content.get("knowledge_points") or [],
        }
        self.stage = "dialogue"
        self.dialogue_index = 0
        self.dialogue_history: list[dict[str, Any]] = []
        self.options: list[str] = []
        self.chest_index = 0
        self.minion_index = 0
        self.boss_index = 0
        self.chest_opened = False
        self.minion_defeated = False
        self.boss_defeated = False
        self.feedback: dict[str, Any] = {}
        self.clue = _MINION_CLUE
        self.started_at = _now()
        self.updated_at = self.started_at
        # 当前演示题（宝箱/小怪/BOSS 共用结构）
        self._pending_task: dict[str, Any] | None = None

    # ---- 序列化（历史存档） ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "role": self.role,
            "topic": self.topic,
            "content": self.content,
            "knowledge": self.knowledge,
            "_chest_questions": self._chest_questions,
            "_minion_questions": self._minion_questions,
            "_boss_questions": self._boss_questions,
            "stage": self.stage,
            "dialogue_index": self.dialogue_index,
            "dialogue_history": self.dialogue_history,
            "options": self.options,
            "chest_index": self.chest_index,
            "minion_index": self.minion_index,
            "boss_index": self.boss_index,
            "chest_opened": self.chest_opened,
            "minion_defeated": self.minion_defeated,
            "boss_defeated": self.boss_defeated,
            "feedback": self.feedback,
            "clue": self.clue,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "_pending_task": self._pending_task,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DemoSession":
        session = cls.__new__(cls)
        for key, value in data.items():
            setattr(session, key, value)
        if not hasattr(session, "updated_at"):
            session.updated_at = getattr(session, "started_at", _now())
        return session

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "role": self.role,
            "topic": self.topic,
            "stage_bg": _STAGE_BG.get(self.stage, "echo_archive"),
            "dialogue_index": self.dialogue_index,
            "history": list(self.dialogue_history),
            "options": list(self.options),
            "feedback": self.feedback,
            "chest_opened": self.chest_opened,
            "minion_defeated": self.minion_defeated,
            "boss_defeated": self.boss_defeated,
            "clue": self.clue if self.stage == "boss" else "",
            # 阶段内进度（用于前端展示"第 x/y 只"）
            "stage_index": self._stage_index(),
            "stage_total": self._stage_total(),
            "boss_stage_name": _BOSS_STAGE_NAMES[self.boss_index] if self.stage == "boss" and self.boss_index < len(_BOSS_STAGE_NAMES) else "",
            "task": self._pending_task,
            "status": "victory" if self.stage == "victory" else "playing",
        }

    def _stage_index(self) -> int:
        if self.stage == "chest":
            return min(self.chest_index, len(self._chest_questions))
        if self.stage == "minion":
            return min(self.minion_index, len(self._minion_questions))
        if self.stage == "boss":
            return min(self.boss_index, len(self._boss_questions))
        return 0

    def _stage_total(self) -> int:
        if self.stage == "chest":
            return len(self._chest_questions)
        if self.stage == "minion":
            return len(self._minion_questions)
        if self.stage == "boss":
            return len(self._boss_questions)
        return 0

    # ---- 阶段一：智能体对话 ----
    async def advance_dialogue(self, choice: str) -> dict[str, Any]:
        if choice:
            self.dialogue_history.append({"speaker": "player", "text": choice, "at": _now()})
        reply = await companion_reply(
            stage="dialogue",
            topic=self.topic,
            player_choice=choice,
            history=self.dialogue_history,
            role_name=self.role["name"],
            fallback_index=self.dialogue_index,
            knowledge=self.knowledge,
        )
        self.dialogue_index += 1
        self.dialogue_history.append({"speaker": reply["speaker"], "text": reply["reply"], "at": _now()})
        self.options = reply["options"]
        self.feedback = {"kind": "continue", "message": reply["reply"]}
        if self.dialogue_index >= 6:
            # 六轮对话完成，进入宝箱阶段
            self._enter_chest()
        return self.snapshot()

    def _enter_chest(self) -> None:
        self.stage = "chest"
        self.chest_index = 0
        self._pending_task = self._chest_questions[0]
        self.feedback = {"kind": "info", "message": "回声书阁的微光渐亮，你们来到拾光宝库——树下的宝箱正等你解开。"}

    # ---- 阶段二：宝箱（2 题） ----
    async def advance_chest(self, answer: str) -> dict[str, Any]:
        task = self._chest_questions[self.chest_index]
        correct, score = evaluate(answer, task)
        if correct:
            self.chest_index += 1
            if self.chest_index >= len(self._chest_questions):
                self.chest_opened = True
                self.stage = "minion"
                self.minion_index = 0
                self._pending_task = self._minion_questions[0]
                self.feedback = {"kind": "success", "message": "宝箱'咔哒'一声开了，里面躺着两枚澄明知种——数据分工，你拿稳了。雾谷方向传来墨迹兽的低吼。"}
            else:
                self._pending_task = self._chest_questions[self.chest_index]
                self.feedback = {"kind": "success", "message": "宝箱松动了一层封印，还有一道锁要解。"}
        else:
            self.feedback = teaching_feedback(task, {"concept": {}, "misconception": {}}, "宝箱纹丝不动，再想想这道题的要点。", attempt=1)
        return self.snapshot()

    # ---- 阶段三：小怪（3 只，越来越难） ----
    async def advance_minion(self, answer: str) -> dict[str, Any]:
        task = self._minion_questions[self.minion_index]
        correct, score = evaluate(answer, task)
        if correct:
            self.minion_index += 1
            if self.minion_index >= len(self._minion_questions):
                self.minion_defeated = True
                self.stage = "boss"
                self.boss_index = 0
                self._pending_task = self._boss_questions[0]
                self.feedback = {"kind": "success", "message": "三只墨迹兽尽数散去。" + self.clue + "古塔的轮廓在雾中亮起。"}
            else:
                self._pending_task = self._minion_questions[self.minion_index]
                self.feedback = {"kind": "success", "message": f"第 {self.minion_index}/{len(self._minion_questions)} 只墨迹兽化作墨雾，下一只已经扑了上来！"}
        else:
            self.feedback = teaching_feedback(task, {"concept": {}, "misconception": {}}, "墨迹兽还在原地打转——它最怕你还没抓住这条规则的要点。", attempt=1)
        return self.snapshot()

    # ---- 阶段四：BOSS（3 阶段：解释 → 辨析 → 迁移） ----
    async def advance_boss(self, answer: str) -> dict[str, Any]:
        task = self._boss_questions[self.boss_index]
        correct, score = evaluate(answer, task)
        if correct:
            self.boss_index += 1
            if self.boss_index >= len(self._boss_questions):
                self.boss_defeated = True
                self.stage = "victory"
                self._pending_task = None
                self.feedback = {"kind": "success", "message": "墨魇之主的雾影散尽，古塔的灯火一路亮回丰穗谷——你通关了！"}
            else:
                self._pending_task = self._boss_questions[self.boss_index]
                self.feedback = {"kind": "success", "message": f"第 {self.boss_index}/{len(self._boss_questions)} 道封印解开（{_BOSS_STAGE_NAMES[self.boss_index - 1]}），下一层封印浮现。"}
        else:
            self.feedback = teaching_feedback(task, {"concept": {}, "misconception": {}}, "古塔的雾没有退。回想卷轴上的线索，再理一理这条规则的要点。", attempt=1)
        return self.snapshot()

    async def advance(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.updated_at = _now()
        if self.stage == "victory":
            raise ValueError("冒险已经通关")
        if self.stage == "dialogue":
            return await self.advance_dialogue(str(payload.get("choice") or ""))
        if self.stage == "chest":
            return await self.advance_chest(payload.get("answer"))
        if self.stage == "minion":
            return await self.advance_minion(payload.get("answer"))
        if self.stage == "boss":
            return await self.advance_boss(payload.get("answer"))
        raise ValueError(f"当前阶段 {self.stage} 不接受操作 {action}")


class DemoStore:
    """演示会话仓库：内存态 + JSON 持久化（历史存档，可恢复继续）。"""

    def __init__(self, persist_path: str | None = None):
        self._sessions: dict[str, DemoSession] = {}
        self._persist_path = persist_path
        self._load()

    # ---- 持久化 ----
    def _flush(self) -> None:
        if not self._persist_path:
            return
        try:
            payload = {rid: s.to_dict() for rid, s in self._sessions.items()}
            with open(self._persist_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        except Exception:
            pass

    def _load(self) -> None:
        if not self._persist_path or not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            for rid, data in payload.items():
                try:
                    self._sessions[rid] = DemoSession.from_dict(data)
                except Exception:
                    continue
        except Exception:
            pass

    def list_sessions(self) -> list[dict[str, Any]]:
        items = []
        for s in self._sessions.values():
            if s.stage == "victory":
                continue  # 已通关的不再列入继续存档
            items.append({
                "run_id": s.run_id,
                "topic": s.topic,
                "role_name": (s.role or {}).get("name", ""),
                "stage": s.stage,
                "stage_label": _STAGE_BG.get(s.stage, "dialogue"),
                "dialogue_index": s.dialogue_index,
                "updated_at": getattr(s, "updated_at", s.started_at),
            })
        items.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
        return items

    def resume(self, run_id: str) -> DemoSession:
        session = self._sessions.get(run_id)
        if session is None:
            raise LookupError("存档不存在")
        return session

    async def start(self, role_id: str, custom_name: str = "", topic: str = "") -> DemoSession:
        session = DemoSession(role_id, custom_name, topic)
        # 题目智能体（LLM）当场生成主题蓝图；失败已降级模板，不影响会话
        session.content = await build_content_llm(session.topic)
        session._chest_questions, session._minion_questions, session._boss_questions = _banks_from_content(session.content)
        session.knowledge = {
            "rule": (session.content.get("scroll") or {}).get("rule", ""),
            "example": (session.content.get("scroll") or {}).get("example", ""),
            "knowledge_points": session.content.get("knowledge_points") or [],
        }
        self._sessions[session.run_id] = session
        self._flush()
        return session

    def get(self, run_id: str) -> DemoSession:
        session = self._sessions.get(run_id)
        if session is None:
            raise LookupError("冒险记录不存在")
        return session

    def advance(self, run_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.advance_async(run_id, action, payload)

    async def advance_async(self, run_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.get(run_id)
        result = await session.advance(action, payload)
        self._flush()
        return result
