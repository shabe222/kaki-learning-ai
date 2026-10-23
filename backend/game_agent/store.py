# -*- coding: utf-8 -*-
"""拾光漫游 · 游戏智能体状态机（store）v3

流程精简为「教 → 练 → 战 → 收」四个阶段、五个节点：
  SCROLL（教）→ TRIAL（练·初次遭遇）→ INTERLEAVE（练·交错挑战）→ BOSS（战）→ TREASURY（收）

对比 v2 的改动：
- GOAL 并入 SCROLL：开局直接进卷轴，不再单独“确认目标”；
- METHOD 并入 TRIAL：遭遇战开场选“出招方式”（决定题目路线）；
- SUPPLY 删除：答错就地重试（三问阶梯），同一节点错 2 次后敌人“换问法”
  出示补救题，不再退回独立补给节点；
- INTERLEAVE 答对直接进 BOSS，去掉“是否进入守关”的确认门；
- 全程追踪连击（streak）、答对总数、答错原话（wrong_details），
  供导演编排“记仇”台词与结尾证据回顾。

独立状态管理：内存态会话 + 可选 JSON 持久化。
不依赖主平台数据库。节点推进与判定在 brain/judge 之上组装。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

from .brain import build_content, public_content
from .director import GameDirector
from .judge import evaluate, teaching_feedback

NODES = ["SCROLL", "TRIAL", "INTERLEAVE", "BOSS", "TREASURY"]
NODE_META = (
    ("SCROLL", "教·拾光卷轴"), ("TRIAL", "练·初次遭遇"),
    ("INTERLEAVE", "练·交错挑战"), ("BOSS", "战·迷雾守关"),
    ("TREASURY", "收·拾光宝库"),
)
IDENTITIES = (
    ("wandering_mage", "云游符咒师"), ("ink_apprentice", "墨坛见习法师"),
    ("lamp_heir", "守灯人后裔"), ("herbal_mage", "山野药术师"),
    ("librarian", "藏书塔学徒"), ("star_apprentice", "星象台学徒"),
)
IDENTITY_PROFILES = {
    "wandering_mage": {"style": "惯于把知识讲给同伴听，从复述中照见缺口", "trial_line": "我用我的方式把它讲出来，看看哪里还站不稳。", "boss_line": "最后一关，我会把三道证据串成一段完整的解释。"},
    "ink_apprentice": {"style": "习惯先闭卷回忆，再对照卷轴补上遗漏", "trial_line": "先不翻卷轴，我自己能想起多少。", "boss_line": "先凭记忆作答，再对照线索检查遗漏。"},
    "lamp_heir": {"style": "擅长把方法做成可操作的机关，靠结果校准理解", "trial_line": "把判断放进一次可观察的操作里试试。", "boss_line": "我会用行动验证每一条证据，而不是停在口头上。"},
    "herbal_mage": {"style": "偏好用例子对照，找出条件差异带来的变化", "trial_line": "我找一组相近例子，看看它们究竟差在哪。", "boss_line": "用例子和反例把边界看清楚，再给出答案。"},
    "librarian": {"style": "习惯先提取规则骨架，再逐层补充细节", "trial_line": "先把骨架立起来，再往里面填证据。", "boss_line": "逐层补全证据链，让每一道封印都站得住。"},
    "star_apprentice": {"style": "偏爱把概念放到陌生情境里检验迁移", "trial_line": "换个新情境想想，看它还能不能成立。", "boss_line": "把规则带过最后一道桥，检查它在陌生处的表现。"},
}
TALENTS = (
    ("rune_instinct", "符文直觉"), ("scroll_memory", "卷轴记忆"),
    ("spirit_stability", "灵息稳定"), ("ink_insight", "墨痕洞察"),
)
SCHOOLS = {
    "fire": {"name": "炎咒", "skill": "余焰"},
    "water": {"name": "水咒", "skill": "清心"},
    "wind": {"name": "风咒", "skill": "回风"},
    "wood": {"name": "木咒", "skill": "生息"},
}

# 出招方式：玩法路线映射（与前端 METHODS 一致）
STRATEGIES = {
    "understanding": {"name": "理解路线", "label": "讲、忆、图"},
    "practice": {"name": "实战路线", "label": "练"},
}

# 同一节点答错达到该次数后，敌人“换问法”出示补救题
REMEDY_AFTER_ATTEMPTS = 2


def _now() -> str:
    return datetime.utcnow().isoformat()


def _snippet(response: Any, limit: int = 18) -> str:
    text = "".join(str(response or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


class GameSession:
    """一次拾光漫游会话（内存态）。"""

    def __init__(self, topic: str, magic_school: str, identity_id: str | None = None, talent_id: str | None = None):
        self.run_id = f"adv_{uuid.uuid4().hex}"
        self.content = build_content(topic)
        identity = next((i for i in IDENTITIES if i[0] == identity_id), None) or IDENTITIES[0]
        talent = next((t for t in TALENTS if t[0] == talent_id), None) or TALENTS[0]
        profile = IDENTITY_PROFILES.get(identity[0]) or {}
        self.archive = {
            "run_id": self.run_id, "topic": topic,
            "identity_id": identity[0], "identity_name": identity[1],
            "identity_style": profile.get("style", ""),
            "identity_trial_line": profile.get("trial_line", ""),
            "identity_boss_line": profile.get("boss_line", ""),
            "talent_id": talent[0], "talent_name": talent[1],
            "magic_school": magic_school, "magic_school_name": SCHOOLS.get(magic_school, SCHOOLS["wood"])["name"],
            "selected_strategy": None, "selected_method": None,
            "current_node": "SCROLL", "boss_stage": 0, "boss_fog": 100,
            "teaching_passed": False, "status": "playing", "started_at": _now(),
            # 战绩追踪：连击 / 答对总数 / 答错原话（供“记仇”台词与结尾回顾）
            "streak": 0, "best_streak": 0, "correct_count": 0, "repaired_count": 0,
            "wrong_details": [],
        }
        self.state = "SCROLL"
        self.feedback: dict[str, Any] = {}
        self.timeline: list[dict[str, Any]] = [{"node": "SCROLL", "decision": "enter", "result": "卷轴已展开", "at": _now()}]
        self.attempts: dict[str, int] = {}
        # 游戏导演：按主题/身份/进度编排剧情帧
        self.director = GameDirector(topic=topic, identity_id=identity[0])

    # ---- 快照 ----
    def snapshot(self) -> dict[str, Any]:
        narrative = self.director.narrative(self.content, self.archive)
        state_copy = narrative["state_copy"]
        key_map = {
            "SCROLL": "opening", "TRIAL": "first_trial",
            "INTERLEAVE": "interleave", "BOSS": "boss_reveal", "TREASURY": "resolution",
        }
        active_key = key_map.get(self.state)
        if active_key:
            narrative["active_state_copy"] = active_key
        return {
            "run_id": self.run_id,
            "state": self.state,
            "archive": dict(self.archive),
            "attempts": dict(self.attempts),
            "content": public_content(self.content),
            "feedback": self.feedback,
            "timeline": list(self.timeline),
            "narrative": narrative,
            "nodes": [
                {"id": nid, "title": title, "status": "active" if nid == self.state else ("completed" if self._passed(nid) else "locked")}
                for nid, title in NODE_META
            ],
        }

    def _passed(self, node: str) -> bool:
        if self.state == "completed":
            return True
        order = NODES.index(node)
        current = NODES.index(self.state)
        return order < current

    # ---- 推进 ----
    def advance(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.state == "completed":
            raise ValueError("本次拾光漫游已经完成")
        state = self.state
        content = self.content
        archive = self.archive

        if action == "hint" and state in {"SCROLL", "TRIAL", "INTERLEAVE", "BOSS"}:
            task = self._current_task()
            hint = (task or {}).get("hint", "回到规则和例子，再试一次。")
            self.feedback = {"kind": "hint", "message": hint}
        elif state == "SCROLL":
            # 教：先看规则，再答理解确认题，答对才放行到练（遭遇战）。
            check = (content.get("scroll") or {}).get("teaching_check") or {}
            if action == "submit" and check:
                correct, score = evaluate(payload.get("answer"), check)
                attempt = self._record("SCROLL", payload.get("answer"), score, correct)
                if correct:
                    archive["teaching_passed"] = True
                    self.feedback = {"kind": "success", "message": "你已经确认理解这条规则。墨色在石径那头聚拢了——该出招了。"}
                else:
                    self.feedback = teaching_feedback(check, content, "这一问是为了确认你真的看懂了规则。", attempt=attempt)
            elif action == "continue" and archive.get("teaching_passed"):
                self.feedback = {"kind": "continue", "message": "石径尽头墨色正在聚拢——选择你的出招方式。"}
                self._enter("TRIAL", "进入初次遭遇，选择出招方式")
            elif action == "continue":
                self.feedback = {"kind": "needs_review", "message": "先答对卷轴上的理解确认题，才能继续。"}
            else:
                raise ValueError("当前卷轴节点操作无效")
        elif state == "TRIAL":
            # 练·第一战：开场先选“出招方式”（决定题目路线），再作答。
            if action in {"choose_strategy", "choose_branch"}:
                choice = str(payload.get("choice") or "")
                if choice not in {"understanding", "practice"}:
                    raise ValueError("请选择理解路线或实战路线")
                archive["selected_strategy"] = choice
                archive["selected_method"] = str(payload.get("method") or "")[:24]
                self.feedback = {"kind": "continue", "message": f"你选择了{STRATEGIES[choice]['name']}，出招已定。"}
            elif action == "submit":
                strategy = archive.get("selected_strategy") or "understanding"
                # 换问法机制：同一节点错 2 次后，敌人换个说法出示补救题。
                attempts = int(self.attempts.get("TRIAL", 0))
                task = content["remedy"] if attempts >= REMEDY_AFTER_ATTEMPTS else content[strategy]["trial"]
                correct, score = evaluate(payload.get("answer"), task)
                attempt = self._record("TRIAL", payload.get("answer"), score, correct)
                if correct:
                    if attempts >= REMEDY_AFTER_ATTEMPTS:
                        archive["repaired_count"] = int(archive.get("repaired_count", 0)) + 1
                    self._enter("INTERLEAVE", "初次遭遇通过，进入交错挑战")
                    self.feedback = {"kind": "success", "message": "第一战拿下，墨迹兽后退了半步。证据在手里变沉了。"}
                else:
                    if attempts >= REMEDY_AFTER_ATTEMPTS:
                        self.feedback = teaching_feedback(task, content, "它换了个问法——缺口还是一样的，换个角度补上它。", attempt=attempt)
                    else:
                        self.feedback = teaching_feedback(task, content, "这条证据还不充分。", attempt=attempt)
            else:
                raise ValueError("当前遭遇节点操作无效")
        elif state == "INTERLEAVE" and action == "submit":
            # 练·第二战：规则换情境，答对直接进战。
            strategy = archive.get("selected_strategy") or "understanding"
            task = content[strategy]["interleave"]
            correct, score = evaluate(payload.get("answer"), task)
            attempt = self._record("INTERLEAVE", payload.get("answer"), score, correct)
            if correct:
                self._enter("BOSS", "交错挑战通过，迷雾守关显现")
                self.feedback = {"kind": "success", "message": "换了个情境也站得住——古塔已经亮起，最终守关来了。"}
            else:
                self.feedback = teaching_feedback(task, content, "迁移证据不足。", attempt=attempt)
        elif state == "BOSS" and action == "submit":
            # 战：三道封印（解释/辨析/迁移），迷雾即血量。
            stage = int(archive.get("boss_stage", 0))
            task = content["boss"][stage]
            correct, score = evaluate(payload.get("answer"), task)
            attempt = self._record("BOSS", payload.get("answer"), score, correct, boss_stage=stage)
            if correct and stage < 2:
                archive["boss_stage"] = stage + 1
                archive["boss_fog"] = [100, 66, 33][stage + 1]
                self.feedback = {"kind": "success", "message": f"{task['label']}封印解开，迷雾继续消散。"}
            elif correct:
                archive["boss_stage"] = 3
                archive["boss_fog"] = 0
                self._enter("TREASURY", "迷雾散尽，拾光宝库开启")
                self.feedback = {"kind": "success", "message": "解释、辨析与迁移三项证据齐全，墨魇之主退散。"}
            else:
                self.feedback = teaching_feedback(task, content, "这次回答还缺少关键证据。", attempt=attempt)
        elif state == "TREASURY" and action == "claim":
            self._claim()
        else:
            raise ValueError(f"当前节点 {state} 不接受操作 {action}")

        return self.snapshot()

    def _current_task(self) -> dict[str, Any] | None:
        state = self.state
        content = self.content
        archive = self.archive
        if state == "SCROLL":
            return (content.get("scroll") or {}).get("teaching_check") or None
        if state == "TRIAL":
            strategy = archive.get("selected_strategy") or "understanding"
            if int(self.attempts.get("TRIAL", 0)) >= REMEDY_AFTER_ATTEMPTS:
                return content["remedy"]
            return content[strategy]["trial"]
        if state == "INTERLEAVE":
            strategy = archive.get("selected_strategy") or "understanding"
            return content[strategy]["interleave"]
        if state == "BOSS":
            return content["boss"][int(archive.get("boss_stage", 0))]
        return None

    def _enter(self, node: str, note: str) -> None:
        self.state = node
        self.archive["current_node"] = node.lower()
        self.timeline.append({"node": node, "decision": "enter", "result": note, "at": _now()})

    def _record(self, node: str, response: Any, score: float, correct: bool, *, boss_stage: int | None = None) -> int:
        """记录一次作答，维护连击/战绩，返回该节点的累计作答次数（attempt）。"""
        attempt = int(self.attempts.get(node, 0)) + 1
        self.attempts[node] = attempt
        archive = self.archive
        if correct:
            archive["correct_count"] = int(archive.get("correct_count", 0)) + 1
            archive["streak"] = int(archive.get("streak", 0)) + 1
            archive["best_streak"] = max(int(archive.get("best_streak", 0)), int(archive["streak"]))
        else:
            archive["streak"] = 0
            if node in {"TRIAL", "INTERLEAVE", "BOSS"}:
                details = list(archive.get("wrong_details") or [])
                details.append({
                    "node": node,
                    "boss_stage": boss_stage,
                    "snippet": _snippet(response),
                    "attempt": attempt,
                })
                archive["wrong_details"] = details[-2:]  # 保留最近两次，够导演“记仇”用
        self.timeline.append({
            "node": node, "boss_stage": boss_stage, "task_type": "learning_check",
            "response": str(response or "")[:500], "score": round(score, 2), "attempts": attempt,
            "decision": "pass" if correct else "needs_review",
            "result": "passed" if correct else "needs_review",
            "evidence": f"{node.lower()}_attempt_{attempt}", "at": _now(),
        })
        return attempt

    def _claim(self) -> None:
        self.state = "completed"
        self.archive.update({"status": "completed", "current_node": "completed", "completed_at": _now(), "boss_fog": 0})
        self.feedback = {"kind": "success", "message": "拾光漫游纪要已保存。你完成了解释、辨析与迁移三重验证。"}


class GameStore:
    """会话仓库：内存态 + 可选 JSON 持久化。"""

    def __init__(self, persist_path: str | None = None):
        self._sessions: dict[str, GameSession] = {}
        self._persist_path = persist_path

    def start(self, topic: str, magic_school: str, identity_id: str | None = None, talent_id: str | None = None) -> GameSession:
        session = GameSession(topic, magic_school, identity_id, talent_id)
        self._sessions[session.run_id] = session
        self._flush()
        return session

    def get(self, run_id: str) -> GameSession:
        session = self._sessions.get(run_id)
        if session is None:
            raise LookupError("拾光漫游记录不存在")
        return session

    def advance(self, run_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.get(run_id)
        result = session.advance(action, payload)
        self._flush()
        return result

    def _flush(self) -> None:
        if not self._persist_path:
            return
        try:
            payload = {rid: session.snapshot() for rid, session in self._sessions.items()}
            with open(self._persist_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        except Exception:
            pass
