"""Teaching-script generation through KAKI's existing LLM governance."""
from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .contracts import VideoGenerationRequest


class ScriptGenerationError(RuntimeError):
    code = "SCRIPT_GENERATION_FAILED"


class TeachingScript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str = Field(min_length=1, max_length=200)
    script: str = Field(min_length=20, max_length=8000)
    key_points: list[str] = Field(min_length=2, max_length=6)
    model: str = Field(min_length=1, max_length=120)
    # 这一段讲稿的质量闸门实测结果（主题落点、专家深度、结构、具体度）。
    # 有它，下游的"审阅通过"才是量出来的，不是一个写死的分数。
    content_review: dict[str, Any] | None = None

    @field_validator("title", "script", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("key_points", mode="before")
    @classmethod
    def clean_points(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [str(item or "").strip()[:160] for item in value if str(item or "").strip()][:6]


def build_local_teaching_script(request: VideoGenerationRequest) -> TeachingScript:
    """Topic-derived short talk used only when cloud avatar credentials are blocked."""
    topic = str(request.topic or "").strip()[:80] or "今天的知识点"
    seconds = max(15, min(int(request.duration_sec or 45), 600))
    evidence = request.evidence_context
    points: list[str] = []
    for candidate in (
        evidence.learning_goal, evidence.graph_node, evidence.next_action, request.instruction,
        f"先抓住「{topic}」要解决什么问题", "用一个生活例子把关系讲清楚",
    ):
        cleaned = str(candidate or "").strip()[:80]
        if cleaned and cleaned not in points:
            points.append(cleaned)
        if len(points) >= 4:
            break
    if len(points) < 2:
        points = [f"抓住「{topic}」的核心概念", "把关键步骤在脑子里过一遍"]
    script = (
        f"同学你好，我是 DARYA。接下来用大约{seconds}秒，带你认识「{topic}」。"
        f"第一，先把问题看清楚。第二，{points[0]}。第三，{points[1]}。"
        "跟我一起把关键步骤过一遍，下课后再做一道同类型的题巩固。"
        "我是 DARYA，我们下次接着练。"
    )
    return TeachingScript(
        title=f"{topic}短讲", script=script, key_points=points[:4], model="local-script",
    )


def spoken_char_budget(duration_sec: int) -> int:
    """Chinese characters a neural voice reads inside ``duration_sec``.

    Edge TTS reads Mandarin at roughly 5.2 characters per second. The budget
    matters because the avatar provider only accepts audio under 19 s: without
    it a "15 秒" request produces a 40 s narration and silently degrades to the
    slide renderer instead of the lip-synced teacher.
    """
    return max(40, min(1200, int(round(max(15, int(duration_sec or 45)) * 5.2))))


def build_script_prompt(request: VideoGenerationRequest) -> str:
    style = request.style_context
    evidence = request.evidence_context
    budget = spoken_char_budget(request.duration_sec)
    style_lines = [
        f"- 语气：{style.tone}" if style.tone else "",
        f"- 节奏：{style.pace}" if style.pace else "",
        f"- 举例偏好：{style.example_preference}" if style.example_preference else "",
        f"- 视觉偏好：{style.visual_preference}" if style.visual_preference else "",
    ]
    evidence_lines = [
        f"- 当前学习目标：{evidence.learning_goal}" if evidence.learning_goal else "",
        f"- 当前图谱节点：{evidence.graph_node}" if evidence.graph_node else "",
        f"- 已验证理解偏差：{'；'.join(evidence.verified_weaknesses)}" if evidence.verified_weaknesses else "",
        f"- 下一学习动作：{evidence.next_action}" if evidence.next_action else "",
    ]
    source = request.source_context.strip()
    return "\n".join(filter(None, [
        f"为主题《{request.topic}》编写约 {request.duration_sec} 秒的中文短讲视频讲稿。",
        "听众多为备考的中学生：先给结论再举例，口语自然，不堆术语；关键数字与适用条件必须与可信参考资料一致，不得编造。",
        f"硬性时长约束：script 的汉字总数必须不超过 {budget} 字，宁短不长；这段讲稿会被逐字朗读。",
        "表达偏好（只影响表达方式）：",
        *style_lines,
        "可信教学证据（只引用这里明确给出的事实）：",
        *evidence_lines,
        f"补充要求：{request.instruction}" if request.instruction else "",
        f"可信参考资料：\n{source}" if source else "",
        "输出 title、script、key_points；key_points 为 2-6 条简短要点。不得编造学生能力结论。",
    ]))


_FIELD_ALIASES = {
    "title": ("title", "标题"),
    "script": ("script", "讲稿", "正文"),
    "key_points": ("key_points", "keyPoints", "要点", "points"),
}


def _unwrap_payload(value: Any) -> Any:
    if isinstance(value, dict) and "content" in value and not (
        {"title", "script", "key_points"} & set(value)
    ):
        return _unwrap_payload(value["content"])
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(text)
    return value


def _looks_like_json_schema(value: Mapping[str, Any]) -> bool:
    return str(value.get("type") or "") == "object" and isinstance(value.get("properties"), dict)


def _normalize_script_payload(value: Any) -> dict[str, Any]:
    raw = _unwrap_payload(value)
    if not isinstance(raw, dict):
        raise ValueError("script payload is not an object")
    data = {str(key): item for key, item in raw.items() if key != "_meta"}
    if _looks_like_json_schema(data):
        raise ValueError("model echoed the JSON schema instead of a teaching script")
    normalized: dict[str, Any] = {}
    for field, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if alias in data and data[alias] not in (None, ""):
                normalized[field] = data[alias]
                break
    points = normalized.get("key_points")
    if isinstance(points, str):
        normalized["key_points"] = [
            part.strip()[:160] for part in points.replace("；", "\n").replace(";", "\n").splitlines()
            if part.strip()
        ]
    elif isinstance(points, list):
        cleaned: list[str] = []
        for item in points:
            if isinstance(item, dict):
                item = item.get("text") or item.get("point") or item.get("title") or ""
            text = str(item or "").strip()
            if text and text not in cleaned:
                cleaned.append(text[:160])
        normalized["key_points"] = cleaned
    return normalized


async def generate_teaching_script(
    request: VideoGenerationRequest, llm: Any, *, model_identifier: str,
    quality_reviewer: Any = None,
) -> TeachingScript:
    prompt = build_script_prompt(request)
    budget = spoken_char_budget(request.duration_sec)
    from services.skill_library import skill_prompt_prefix
    system_prompt = (
        skill_prompt_prefix("resource-art-direction", "video-script-authoring", max_chars_per_skill=1600)
        + "你是柿子智学的教学讲稿生成器。严格使用后端提供的可信教学证据；"
        "画像只控制表达风格。按示例填入真实讲稿内容，不要回显 JSON Schema 或类型定义。"
        f"script 必须是可口播的完整短讲，不少于 40 个汉字且不超过 {budget} 个汉字；key_points 至少 2 条。"
    )
    try:
        raw = await llm.structured_output(
            [{"role": "user", "content": prompt}],
            {
                "title": "短讲标题",
                "script": "可直接口播的完整中文讲稿",
                "key_points": ["要点一", "要点二"],
            },
            system_prompt=system_prompt,
            cache_user_id=request.user_id,
        )
        payload = _normalize_script_payload(raw)
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ScriptGenerationError("讲稿模型返回了无效结构，未生成固定兜底内容。") from exc
    except ScriptGenerationError:
        raise
    except Exception as exc:
        raise ScriptGenerationError("讲稿生成服务当前不可用，未创建假讲稿。") from exc

    # 讲稿是整条流水线的上游：一段跑题或全是套话的讲稿，后面配音、渲染、导出
    # 都在为它付费。所以闸门放在这里，而不是等视频渲染完了再检查。
    from services.content_quality_gate import (
        KIND_VIDEO_SCRIPT,
        quality_receipt,
        run_content_quality_gate,
    )

    async def revise_script(*, existing_candidate, constraints, prior_review, round_number):
        rewrite_prompt = (
            f"{prompt}\n\n上一稿未通过内容质量检查，请按下面的要求重写：\n"
            + "\n".join(f"- {item}" for item in constraints)
            + f"\n\n上一稿：\n{json.dumps(dict(existing_candidate), ensure_ascii=False)[:2000]}"
            + "\n只输出 title、script、key_points 三个字段的 JSON。"
        )
        rewritten = await llm.structured_output(
            [{"role": "user", "content": rewrite_prompt}],
            {"title": "短讲标题", "script": "可直接口播的完整中文讲稿", "key_points": ["要点一", "要点二"]},
            system_prompt=system_prompt,
            cache_user_id=request.user_id,
        )
        return _normalize_script_payload(rewritten)

    gate_kwargs: dict[str, Any] = {
        "kind": KIND_VIDEO_SCRIPT,
        "candidate": payload,
        "topic": str(request.topic or ""),
        "learning_goal": str(getattr(request.evidence_context, "learning_goal", "") or ""),
        "revise": revise_script,
    }
    if quality_reviewer is not None:
        gate_kwargs["reviewer"] = quality_reviewer
    gate = await run_content_quality_gate(**gate_kwargs)
    # 改写一轮之后仍然带着高严重度问题（跑题、占位符、缺要点），就不能拿它去配音和
    # 渲染：那会让一段不该存在的讲稿变成一段看起来很正式的视频。低/中等问题只记进
    # 回执，不拦 —— "还能更好"和"这是错的"必须区别对待。
    blocking = [issue for issue in gate["issues"] if issue.get("severity") in {"high", "blocking"}]
    if gate["verdict"] == "block" or blocking:
        reason = blocking[0].get("description") if blocking else "评审明确拒绝。"
        raise ScriptGenerationError(f"讲稿未通过内容质量检查（{reason}），已放弃这一版，不生成假讲稿。")
    payload = dict(gate["candidate"])
    payload["content_review"] = quality_receipt(gate)
    payload["model"] = str(model_identifier or "unknown")
    try:
        return TeachingScript.model_validate(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ScriptGenerationError("讲稿模型返回了无效结构，未生成固定兜底内容。") from exc
