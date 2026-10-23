"""Restore the last working Codex workspace-agent module from its CPython 3.11 cache.

The source file was structurally corrupted by a later edit.  The preserved cache
was generated from the last working source on 2026-07-15 00:11 local time.
"""
from importlib.machinery import SourcelessFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys
import re
import inspect
from dataclasses import asdict, replace
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


_MODULE_NAME = "services._workspace_agents_codex_restored"
_CACHE_PATH = Path(__file__).with_name("workspace_agents.codex-restored-20260715.pyc")
if _CACHE_PATH.exists():
    _LOADER = SourcelessFileLoader(_MODULE_NAME, str(_CACHE_PATH))
    _SPEC = spec_from_loader(_MODULE_NAME, _LOADER)
    if _SPEC is None:
        raise ImportError("cannot load the preserved workspace-agent module")
    _RESTORED = module_from_spec(_SPEC)
    sys.modules[_MODULE_NAME] = _RESTORED
    _LOADER.exec_module(_RESTORED)

    for _name, _value in vars(_RESTORED).items():
        if not _name.startswith("__"):
            globals()[_name] = _value

    __all__ = [name for name in vars(_RESTORED) if not name.startswith("__")]
else:
    # Some handoff packages omitted the historical sourceless cache even though
    # workspace_agents.py still referenced it.  Keep the service bootable with
    # a conservative source fallback; the formal semantic router remains the
    # authority for current task planning.
    @dataclass(frozen=True)
    class AgentTaskSpec:
        kind: str
        topic: str
        count: int = 5
        difficulty: str = "中等"
        detail_level: str = "简明"
        export_formats: tuple[str, ...] = ()
        source_artifact_id: int | None = None
        instruction: str = ""
        question_types: tuple[str, ...] = ()
        language: str = "python"
        should_run: bool = True
        confirmed: bool = False
        requires_confirmation: bool = True

    def capability_system_prompt() -> str:
        return (
            "你可以围绕当前对话生成讲义、练习、代码、思维导图、演示文稿和教学视频。"
            "生成或修改学习产物前必须先向用户确认。"
            "你可以执行权威资料搜索：当用户明确要求搜索、查找、检索权威资料（官方来源、高校课程、论文、前沿研究）时，\n"
            "你会搜索中文官方与高校学术来源，展示可核验的结果，并允许用户选择导入资料库。\n"
            "你可以编排学习图谱：根据学习目标创建或扩展知识节点、依赖关系与学习路径。\n"
            "你还可以读取系统已保存的长期记忆与学习印象（作为上下文提供），并在用户请求时帮助整理。\n"
        )

    def build_confirmation(spec: AgentTaskSpec) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": f"准备围绕《{spec.topic}》生成{spec.kind}，确认后开始。",
            "task": asdict(spec),
        }

    def build_task_failure_message(thread_id: str, kind: str, topic: str) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": f"《{topic}》的{kind}生成失败，可以稍后重试。",
            "thread_id": thread_id,
            "status": "failed",
        }

    def build_thread_artifact_message(
        thread_id: str, kind: str, artifact_id: Any, title: str, summary: str,
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": summary,
            "thread_id": thread_id,
            "artifact": {"id": artifact_id, "kind": kind, "title": title},
        }

    def normalize_agent_result(spec: AgentTaskSpec, result: Any) -> dict[str, Any]:
        payload = dict(result) if isinstance(result, dict) else {"result": result}
        payload.setdefault("kind", spec.kind)
        payload.setdefault("topic", spec.topic)
        payload.setdefault("status", "completed")
        return payload

    async def run_agent_operation(operation: Callable[[], Any]) -> Any:
        value = operation()
        return await value if inspect.isawaitable(value) else value

    def resolve_workspace_task(value: Any) -> AgentTaskSpec | None:
        if isinstance(value, AgentTaskSpec):
            return value
        if not isinstance(value, dict) or not value.get("kind"):
            return None
        allowed = {field.name for field in AgentTaskSpec.__dataclass_fields__.values()}
        return AgentTaskSpec(**{key: item for key, item in value.items() if key in allowed})

    def detect_agent_intent(message: str, active_task: Any = None) -> AgentTaskSpec | None:
        text = str(message or "").strip()
        patterns = (
            ("exam", r"(?:出题|练习|测验|考试)"),
            ("lecture", r"(?:讲义|文档|总结|学习资料)"),
            ("code", r"(?:代码|程序|编程|python|javascript|java|c\+\+)"),
        )
        for kind, pattern in patterns:
            if re.search(pattern, text, re.I) and re.search(r"(?:生成|写|做|给我|帮我|创建)", text):
                return AgentTaskSpec(kind=kind, topic=text[:120], instruction=text)
        return None

    def route_workspace_message(message: str, active_artifact: Any = None) -> dict[str, Any]:
        text = str(message or "").strip()
        if re.search(r"(?:能做什么|有什么功能|支持什么|帮助)", text):
            return {"route": "capability_query", "reply": capability_system_prompt()}
        spec = detect_agent_intent(text)
        if spec is None:
            return {"route": "chat"}
        return {
            "route": f"generate_{spec.kind}",
            "task": asdict(spec),
            "artifact_type": spec.kind,
            "explicit_artifact_request": True,
            "confirmation_required": True,
        }

    __all__ = [
        "AgentTaskSpec", "build_confirmation", "build_task_failure_message",
        "build_thread_artifact_message", "capability_system_prompt",
        "detect_agent_intent", "normalize_agent_result", "resolve_workspace_task",
        "route_workspace_message", "run_agent_operation",
    ]


_restored_detect_agent_intent = detect_agent_intent
_restored_route_workspace_message = route_workspace_message


def _normalise_code_topic(value: str) -> str:
    topic = str(value or "").strip()
    topic = re.sub(r"^(?:给我|帮我|请|看|写|生成|做)(?:看|写|生成|做)?(?:一个|一段|个|段)?\s*", "", topic)
    topic = re.sub(r"\b(?:python|javascript|java|c\+\+)\b", "", topic, flags=re.I)
    topic = re.sub(r"(?:的)?(?:代码)?(?:示例|程序)$", "", topic).strip()
    topic = re.sub(r"\s+", " ", topic)
    return topic or "代码示例"


def detect_agent_intent(message, active_task=None):
    """Preserve the cached implementation while restoring generic code wording."""
    spec = _restored_detect_agent_intent(message)
    if spec is not None and getattr(spec, "kind", None) == "code":
        return replace(spec, topic=_normalise_code_topic(spec.topic))
    text = str(message or "").strip()
    if re.search(r"(?:代码|程序|编程|code|python|java|javascript|c\+\+)", text, re.I) and re.search(
        r"(?:生成|写|给我|帮我|示例|实现)", text,
    ):
        language_match = re.search(r"\b(python|javascript|java|c\+\+)\b", text, re.I)
        language = language_match.group(1).lower() if language_match else "python"
        return AgentTaskSpec(
            kind="code",
            topic=_normalise_code_topic(text),
            language=language,
            instruction=text,
            requires_confirmation=True,
        )
    return spec


def _code_operation_route(message: str, active_artifact: dict):
    text = str(message or "").strip()
    artifact_id = int(active_artifact.get("artifact_id"))
    topic = str(active_artifact.get("topic") or "代码示例")
    language = str(active_artifact.get("language") or "python")
    count_match = re.search(r"([1-9]|1\d|20)\s*道", text)
    count = int(count_match.group(1)) if count_match else 3
    spec = AgentTaskSpec(
        kind="code", topic=topic, language=language, instruction=text,
        source_artifact_id=artifact_id, count=count,
    )
    task = asdict(spec)
    if re.search(r"(?:基于|根据|围绕).{0,12}(?:代码|程序).*?(?:出题|出\s*[0-9一二两三四五六七八九十]+\s*道?题|题目|练习|测验)", text):
        return {"route": "exam_from_code", "task": task, "artifact_type": "exam"}
    if re.search(r"(?:运行|再跑|重跑|执行).{0,8}(?:代码|程序|一遍|一次)?", text):
        return {"route": "rerun_code", "task": task, "artifact_type": "code"}
    if re.search(r"(?:修改|改成|换成|重写|增加注释|优化)", text):
        task["topic"] = _normalise_code_topic(re.sub(r"^(?:修改|改成|换成|重写)", "", text))
        return {"route": "revise_code", "task": task, "artifact_type": "code"}
    if re.search(r"(?:逐行解释|解释(?:一下)?(?:这段)?代码|讲解(?:一下)?(?:这段)?代码)", text):
        return {"route": "explain_code", "task": task, "artifact_type": "code"}
    if re.search(r"(?:为什么|怎么|如何|哪一|什么意思|作用|区别|是否)", text):
        return {"route": "code_qa", "task": task, "artifact_type": "code"}
    return None


def route_workspace_message(message, active_artifact=None):
    if isinstance(active_artifact, dict) and active_artifact.get("kind") == "code" and active_artifact.get("artifact_id"):
        operation = _code_operation_route(message, active_artifact)
        if operation:
            return operation
    routed = _restored_route_workspace_message(message, active_artifact)
    if routed.get("route") == "chat":
        spec = detect_agent_intent(message)
        if spec is not None and spec.kind == "code":
            return {
                "route": "generate_code",
                "task": asdict(spec),
                "artifact_type": "code",
                "explicit_artifact_request": True,
                "confirmation_required": True,
                "reason_code": "explicit_code_request",
                "confidence": 0.9,
            }
    return routed


__all__.extend(["detect_agent_intent", "route_workspace_message"])
