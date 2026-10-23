"""Agent-owned slot definitions used by the common task state machine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Optional
import re

from services.workspace_task_contracts import AgentType


def _clean_text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _normalise_difficulty(value: Any) -> str:
    aliases = {
        "简单": "basic", "基础": "basic", "入门": "basic", "basic": "basic",
        "中等": "intermediate", "适中": "intermediate", "intermediate": "intermediate",
        "困难": "advanced", "难": "advanced", "高级": "advanced", "advanced": "advanced",
        "自适应": "adaptive", "adaptive": "adaptive",
    }
    return aliases.get(_clean_text(value, 30).lower(), "intermediate")


def _normalise_material_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return list(dict.fromkeys(
        _clean_text(item, 80) for item in (value or []) if _clean_text(item, 80)
    ))[:8]


@dataclass(frozen=True)
class AgentAdapter:
    agent_type: AgentType
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]
    defaults: Mapping[str, Any]
    normalise: Callable[[Mapping[str, Any]], Dict[str, Any]]
    missing_question: Callable[[Iterable[str]], str]
    confirmation: Callable[[Mapping[str, Any]], str]
    execution_payload: Callable[[Mapping[str, Any]], Dict[str, Any]]
    missing_resolver: Optional[Callable[[Mapping[str, Any]], list[str]]] = None

    @property
    def allowed_slots(self) -> set[str]:
        return set(self.required_slots + self.optional_slots)

    def normalise_updates(self, updates: Mapping[str, Any]) -> Dict[str, Any]:
        unknown = set(updates) - self.allowed_slots
        if unknown:
            raise ValueError("unsupported slots: " + ", ".join(sorted(unknown)))
        return self.normalise(updates)

    def initial_slots(self) -> Dict[str, Any]:
        return dict(self.defaults)

    def missing_slots(self, slots: Mapping[str, Any]) -> list[str]:
        if self.missing_resolver:
            return self.missing_resolver(slots)
        return [name for name in self.required_slots if slots.get(name) in (None, "", [])]


_QUESTION_TYPES = {"single_choice", "true_false", "fill_blank", "short_answer"}


def _normalise_exam(updates: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if "topic" in updates:
        result["topic"] = _clean_text(updates["topic"], 160)
    if "question_count" in updates:
        count = int(updates["question_count"])
        if not 1 <= count <= 20:
            raise ValueError("question_count must be between 1 and 20")
        result["question_count"] = count
    if "question_types" in updates:
        values = list(dict.fromkeys(str(value) for value in (updates["question_types"] or [])))
        if not values or any(value not in _QUESTION_TYPES for value in values):
            raise ValueError("invalid question_types")
        result["question_types"] = values
    if "difficulty" in updates:
        result["difficulty"] = _normalise_difficulty(updates["difficulty"])
    if "source_artifact_id" in updates:
        value = updates["source_artifact_id"]
        result["source_artifact_id"] = int(value) if value is not None else None
    if "additional_requirements" in updates:
        value = updates["additional_requirements"]
        if isinstance(value, str):
            value = [value]
        result["additional_requirements"] = [_clean_text(item, 240) for item in (value or []) if _clean_text(item, 240)]
    if "source_material_ids" in updates:
        result["source_material_ids"] = _normalise_material_ids(updates["source_material_ids"])
    return result


def _exam_missing(missing: Iterable[str]) -> str:
    labels = {"topic": "想练习什么主题", "question_count": "需要几道", "question_types": "什么题型"}
    parts = [labels[name] for name in missing]
    return "可以，请告诉我" + "、".join(parts) + "？"


def _exam_confirmation(slots: Mapping[str, Any]) -> str:
    type_labels = {
        "single_choice": "单选题", "true_false": "判断题",
        "fill_blank": "填空题", "short_answer": "简答题",
    }
    types = slots["question_types"]
    type_text = type_labels[types[0]] if len(types) == 1 else "混合题"
    difficulty = {"basic": "基础", "advanced": "进阶", "adaptive": "自适应"}.get(slots.get("difficulty"), "")
    return f"我准备生成 {slots['question_count']} 道{slots['topic']}{difficulty}{type_text}，现在开始吗？"


def _exam_payload(slots: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "topic": slots["topic"],
        "question_count": slots["question_count"],
        "question_types": list(slots["question_types"]),
        "difficulty": slots.get("difficulty", "intermediate"),
        "source_artifact_id": slots.get("source_artifact_id"),
        "additional_requirements": list(slots.get("additional_requirements") or []),
    }


def _normalise_lecture(updates: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, limit in (("topic", 160), ("purpose", 120), ("detail_level", 30)):
        if key in updates:
            result[key] = _clean_text(updates[key], limit)
    for key in ("sections", "export_formats"):
        if key in updates:
            value = updates[key]
            if isinstance(value, str):
                value = [value]
            result[key] = [_clean_text(item, 120) for item in (value or []) if _clean_text(item, 120)]
    if "source_artifact_id" in updates:
        value = updates["source_artifact_id"]
        result["source_artifact_id"] = int(value) if value is not None else None
    if "source_artifact_kind" in updates:
        kind = _clean_text(updates["source_artifact_kind"], 40)
        if kind not in {"lecture", "exam", "exam_result", "code", ""}:
            raise ValueError("invalid source_artifact_kind")
        result["source_artifact_kind"] = kind or None
    if result.get("source_artifact_kind") == "exam_result" and not result.get("topic"):
        result["topic"] = "本次错题讲解与知识点复习"
    if "source_material_ids" in updates:
        result["source_material_ids"] = _normalise_material_ids(updates["source_material_ids"])
    return result


def _lecture_missing(missing: Iterable[str]) -> str:
    return "可以，想生成什么主题的讲义？"


def _lecture_confirmation(slots: Mapping[str, Any]) -> str:
    detail = {"detailed": "系统学习", "concise": "快速复习"}.get(slots.get("detail_level"), "")
    detail_text = f"，用于{detail}" if detail else ""
    return f"我准备生成一份「{slots['topic']}」讲义{detail_text}，现在开始吗？"


def _lecture_payload(slots: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(slots)


_CODE_LANGUAGES = {
    "python": "Python",
}


def _normalise_code(updates: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if "language" in updates:
        language = _clean_text(updates["language"], 30).lower()
        aliases = {"py": "python", "python3": "python"}
        language = aliases.get(language, language)
        if language not in _CODE_LANGUAGES:
            raise ValueError("当前真实代码运行器只支持 Python")
        result["language"] = language
    for key, limit in (("goal", 180), ("topic", 180), ("input_output_requirement", 300)):
        if key in updates:
            result[key] = _clean_text(updates[key], limit)
    if "run_after_generation" in updates:
        result["run_after_generation"] = bool(updates["run_after_generation"])
    return result


def _code_missing(missing: Iterable[str]) -> str:
    labels = {"language": "使用什么语言", "topic": "希望演示什么目标或知识点"}
    return "可以，请告诉我" + "、".join(labels[name] for name in missing) + "？"


def _code_confirmation(slots: Mapping[str, Any]) -> str:
    language = _CODE_LANGUAGES.get(slots["language"], slots["language"])
    io_text = f"，要求{slots['input_output_requirement']}" if slots.get("input_output_requirement") else ""
    run_text = "，并运行展示结果" if slots.get("run_after_generation", True) else ""
    return f"我准备生成一个 {language} {slots['topic']}示例{io_text}{run_text}，现在开始吗？"


def _code_payload(slots: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(slots)


_PRESENTATION_MODES = {"presentation_only", "interactive_lesson"}


def _normalise_presentation(updates: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, limit in (
        ("topic", 160), ("delivery_mode", 32), ("learning_goal", 160),
        ("level", 40), ("presentation_style", 80),
    ):
        if key in updates:
            result[key] = _clean_text(updates[key], limit)
    if "delivery_mode" in result and result["delivery_mode"] not in _PRESENTATION_MODES:
        raise ValueError("invalid delivery_mode")
    if "duration_minutes" in updates:
        value = updates["duration_minutes"]
        if value in (None, ""):
            result["duration_minutes"] = None
        else:
            duration = int(value)
            if not 1 <= duration <= 60:
                raise ValueError("duration_minutes must be between 1 and 60")
            result["duration_minutes"] = duration
    if "slide_count" in updates:
        value = updates["slide_count"]
        if value in (None, ""):
            result["slide_count"] = None
        else:
            slide_count = int(value)
            if not 1 <= slide_count <= 30:
                raise ValueError("slide_count must be between 1 and 30")
            result["slide_count"] = slide_count
    if "include_interactions" in updates:
        result["include_interactions"] = bool(updates["include_interactions"])
    if "source_artifact_id" in updates:
        value = updates["source_artifact_id"]
        result["source_artifact_id"] = int(value) if value is not None else None
    return result


def _presentation_missing(slots: Mapping[str, Any]) -> list[str]:
    missing = [name for name in ("topic", "delivery_mode") if slots.get(name) in (None, "", [])]
    if slots.get("delivery_mode") == "interactive_lesson":
        missing.extend(
            name for name in ("learning_goal",)
            if slots.get(name) in (None, "", [])
        )
    return missing


def _presentation_missing_question(missing: Iterable[str]) -> str:
    missing = list(missing)
    if missing == ["delivery_mode"]:
        return "你希望只制作一份可查看、可导出的 PPT，还是进入带讲解和互动题的互动课堂？"
    labels = {
        "topic": "学习主题",
        "delivery_mode": "只制作 PPT 还是互动课堂",
        "learning_goal": "想重点学习什么",
        "duration_minutes": "希望大约多长时间",
    }
    return "可以，请告诉我" + "、".join(labels[name] for name in missing) + "？"


def _presentation_confirmation(slots: Mapping[str, Any]) -> str:
    topic = slots["topic"]
    if slots["delivery_mode"] == "presentation_only":
        goal = f"，重点讲解{slots['learning_goal']}" if slots.get("learning_goal") else ""
        pages = f"，共 {slots['slide_count']} 页" if slots.get("slide_count") else ""
        return f"我准备生成一份《{topic}》PPT{goal}{pages}，完成后可以在线查看和导出。现在开始吗？"
    duration = int(slots["duration_minutes"])
    duration_text = {5: "五", 10: "十"}.get(duration, str(duration))
    return (
        f"我准备生成一节约{duration_text}分钟的{topic}{slots['learning_goal']}互动课堂，"
        "包含逐页讲解和互动题。现在开始吗？"
    )


def _presentation_payload(slots: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(slots)
    payload["include_interactions"] = slots.get("delivery_mode") == "interactive_lesson"
    return payload


def _normalise_mindmap(updates: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, limit in (("topic", 160), ("source_scope", 200), ("purpose", 120), ("instruction", 500)):
        if key in updates:
            result[key] = _clean_text(updates[key], limit)
    if "source_material_ids" in updates:
        result["source_material_ids"] = _normalise_material_ids(updates["source_material_ids"])
    return result


def _mindmap_missing(slots: Mapping[str, Any]) -> list[str]:
    if slots.get("topic") or slots.get("source_material_ids"):
        return []
    return ["topic"]


def _mindmap_missing_question(missing: Iterable[str]) -> str:
    return "想根据什么主题或当前资料生成思维导图？"


def _mindmap_confirmation(slots: Mapping[str, Any]) -> str:
    if slots.get("source_material_ids"):
        scope = f"（{slots['source_scope']}）" if slots.get("source_scope") else ""
        return f"我准备根据当前资料{scope}生成一张适合学习的思维导图，现在开始吗？"
    return f"我准备生成一张《{slots['topic']}》学习思维导图，现在开始吗？"


def _mindmap_payload(slots: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(slots)


def _normalise_video(updates: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if "topic" in updates:
        result["topic"] = _clean_text(updates["topic"], 160)
    if "duration_sec" in updates:
        duration = int(updates["duration_sec"])
        if not 15 <= duration <= 600:
            raise ValueError("duration_sec must be between 15 and 600")
        result["duration_sec"] = duration
    if "instruction" in updates:
        result["instruction"] = _clean_text(updates["instruction"], 500)
    if "source_material_ids" in updates:
        result["source_material_ids"] = _normalise_material_ids(updates["source_material_ids"])
    return result


def _video_missing(missing: Iterable[str]) -> str:
    return "可以，想生成什么主题的教学视频？"


def _video_confirmation(slots: Mapping[str, Any]) -> str:
    duration = int(slots.get("duration_sec") or 60)
    return f"我准备生成一段约 {duration} 秒的《{slots['topic']}》教学视频，现在开始吗？"


def _video_payload(slots: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(slots)


def _normalise_search(updates: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if "query" in updates:
        result["query"] = _clean_text(updates["query"], 240)
    if "source_scope" in updates:
        scope = _clean_text(updates["source_scope"], 32).lower()
        if scope not in {"official_first", "academic", "all"}:
            raise ValueError("invalid search source_scope")
        result["source_scope"] = scope
    if "limit" in updates:
        limit = int(updates["limit"])
        if not 1 <= limit <= 10:
            raise ValueError("search limit must be between 1 and 10")
        result["limit"] = limit
    return result


def _search_missing(missing: Iterable[str]) -> str:
    return "想搜索什么主题的权威资料？"


def _search_confirmation(slots: Mapping[str, Any]) -> str:
    scope = "中英文论文" if slots.get("source_scope") == "academic" else "中文官方与高校来源"
    return f"我会优先搜索{scope}中与“{slots['query']}”相关的真实资料。现在开始吗？"


def _search_payload(slots: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(slots)


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_GRAPH_UPDATE_MODES = {"CREATE", "EXTEND", "REVISE", "REPLACE_SOURCE", "MERGE"}


def _opaque_ids(value: Any, *, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    values = list(dict.fromkeys(str(item or "").strip() for item in (value or [])))[:limit]
    if any(not _OPAQUE_ID.fullmatch(item) for item in values):
        raise ValueError("source identities must be opaque IDs")
    return values


def _normalise_learning_graph(updates: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if "user_goal" in updates:
        result["user_goal"] = _clean_text(updates["user_goal"], 1000)
    if "domain" in updates:
        result["domain"] = _clean_text(updates["domain"], 100)
    if "source_material_ids" in updates:
        result["source_material_ids"] = _opaque_ids(updates["source_material_ids"], limit=8)
    if "update_mode" in updates:
        mode = _clean_text(updates["update_mode"], 30).upper()
        if mode not in _GRAPH_UPDATE_MODES:
            raise ValueError("invalid learning graph update_mode")
        result["update_mode"] = mode
    if "graph_id" in updates:
        value = str(updates["graph_id"] or "").strip()
        if value and not _OPAQUE_ID.fullmatch(value):
            raise ValueError("graph_id must be an opaque durable ID")
        result["graph_id"] = value or None
    if "graph_revision" in updates:
        revision = int(updates["graph_revision"] or 0)
        if revision < 0:
            raise ValueError("graph_revision must be non-negative")
        result["graph_revision"] = revision
    if "replaced_source_ref_ids" in updates:
        result["replaced_source_ref_ids"] = _opaque_ids(
            updates["replaced_source_ref_ids"], limit=40,
        )
    return result


def _learning_graph_missing(missing: Iterable[str]) -> str:
    labels = {"user_goal": "学习目标", "domain": "学科领域"}
    return "为了建立正式学习图谱，请补充" + "、".join(labels[item] for item in missing) + "。"


def _learning_graph_confirmation(slots: Mapping[str, Any]) -> str:
    action = "创建" if slots.get("update_mode") == "CREATE" else "更新"
    source = "并使用当前选择的资料" if slots.get("source_material_ids") else ""
    return f"我准备根据“{slots['user_goal']}”{action}一份专属学习安排{source}。现在开始吗？"


def _learning_graph_payload(slots: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(slots)


AGENT_ADAPTERS: Dict[AgentType, AgentAdapter] = {
    AgentType.LEARNING_GRAPH: AgentAdapter(
        agent_type=AgentType.LEARNING_GRAPH,
        required_slots=("user_goal", "domain"),
        optional_slots=(
            "source_material_ids", "update_mode", "graph_id", "graph_revision",
            "replaced_source_ref_ids",
        ),
        defaults={
            "source_material_ids": [], "update_mode": "CREATE", "graph_id": None,
            "graph_revision": 0, "replaced_source_ref_ids": [],
        },
        normalise=_normalise_learning_graph,
        missing_question=_learning_graph_missing,
        confirmation=_learning_graph_confirmation,
        execution_payload=_learning_graph_payload,
    ),
    AgentType.SEARCH: AgentAdapter(
        agent_type=AgentType.SEARCH,
        required_slots=("query",),
        optional_slots=("source_scope", "limit"),
        defaults={"source_scope": "official_first", "limit": 6},
        normalise=_normalise_search,
        missing_question=_search_missing,
        confirmation=_search_confirmation,
        execution_payload=_search_payload,
    ),
    AgentType.EXAM: AgentAdapter(
        agent_type=AgentType.EXAM,
        required_slots=("topic", "question_count", "question_types"),
        optional_slots=("difficulty", "source_artifact_id", "source_material_ids", "additional_requirements"),
        defaults={"difficulty": "intermediate", "source_artifact_id": None, "source_material_ids": [], "additional_requirements": []},
        normalise=_normalise_exam,
        missing_question=_exam_missing,
        confirmation=_exam_confirmation,
        execution_payload=_exam_payload,
    ),
    AgentType.LECTURE: AgentAdapter(
        agent_type=AgentType.LECTURE,
        required_slots=("topic",),
        optional_slots=("purpose", "detail_level", "sections", "export_formats", "source_artifact_id", "source_artifact_kind", "source_material_ids"),
        defaults={"purpose": "", "detail_level": "normal", "sections": [], "export_formats": [], "source_artifact_id": None, "source_artifact_kind": None, "source_material_ids": []},
        normalise=_normalise_lecture,
        missing_question=_lecture_missing,
        confirmation=_lecture_confirmation,
        execution_payload=_lecture_payload,
    ),
    AgentType.CODE: AgentAdapter(
        agent_type=AgentType.CODE,
        required_slots=("language", "topic"),
        optional_slots=("goal", "input_output_requirement", "run_after_generation"),
        defaults={"goal": "", "input_output_requirement": "", "run_after_generation": True},
        normalise=_normalise_code,
        missing_question=_code_missing,
        confirmation=_code_confirmation,
        execution_payload=_code_payload,
    ),
    AgentType.PRESENTATION: AgentAdapter(
        agent_type=AgentType.PRESENTATION,
        required_slots=("topic", "delivery_mode"),
        optional_slots=(
            "learning_goal", "level", "duration_minutes", "slide_count", "presentation_style",
            "include_interactions", "source_artifact_id",
        ),
        defaults={
            "learning_goal": "", "level": "", "duration_minutes": 10,
            "slide_count": None, "presentation_style": "", "include_interactions": False,
            "source_artifact_id": None,
        },
        normalise=_normalise_presentation,
        missing_question=_presentation_missing_question,
        confirmation=_presentation_confirmation,
        execution_payload=_presentation_payload,
        missing_resolver=_presentation_missing,
    ),
    AgentType.VIDEO: AgentAdapter(
        agent_type=AgentType.VIDEO,
        required_slots=("topic",),
        optional_slots=("duration_sec", "instruction", "source_material_ids"),
        defaults={"duration_sec": 60, "instruction": "", "source_material_ids": []},
        normalise=_normalise_video,
        missing_question=_video_missing,
        confirmation=_video_confirmation,
        execution_payload=_video_payload,
    ),
    AgentType.MINDMAP: AgentAdapter(
        agent_type=AgentType.MINDMAP,
        required_slots=(),
        optional_slots=("topic", "source_material_ids", "source_scope", "purpose", "instruction"),
        defaults={"topic": "", "source_material_ids": [], "source_scope": "", "purpose": "learning", "instruction": ""},
        normalise=_normalise_mindmap,
        missing_question=_mindmap_missing_question,
        confirmation=_mindmap_confirmation,
        execution_payload=_mindmap_payload,
        missing_resolver=_mindmap_missing,
    ),
}
