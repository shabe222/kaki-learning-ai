"""Strict tree generation for persisted AI workspace mind maps."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from services.llm_service_real import extract_llm_text, get_llm_service
from services.skill_library import skill_prompt_prefix


MAX_MINDMAP_NODES = 80
MAX_MINDMAP_DEPTH = 3
MAX_SOURCE_CONTEXT_CHARS = 6000


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict) and "content" not in raw:
        value = raw
    else:
        try:
            text = extract_llm_text(raw)
        except TypeError:
            text = str(raw or "")
        text = text.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
        if fenced:
            text = fenced.group(1).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise ValueError("mindmap agent returned no JSON object")
            value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("mindmap agent returned invalid JSON")
    return value


def normalize_mindmap_tree(raw: Any) -> Dict[str, Any]:
    """Validate and copy one nested tree; arbitrary graph edges are forbidden."""
    value = _json_object(raw) if not isinstance(raw, dict) else raw
    root = value.get("tree") if isinstance(value.get("tree"), dict) else value
    seen_ids: set[str] = set()
    seen_objects: set[int] = set()
    node_count = 0

    def visit(node: Any, depth: int) -> Dict[str, Any]:
        nonlocal node_count
        if not isinstance(node, dict):
            raise ValueError("mindmap node must be an object")
        if depth > MAX_MINDMAP_DEPTH:
            raise ValueError("mindmap tree is too deep")
        object_id = id(node)
        if object_id in seen_objects:
            raise ValueError("mindmap tree contains a cycle")
        seen_objects.add(object_id)

        node_id = _text(node.get("id"), 80)
        label = _text(node.get("label"), 100)
        if not node_id or not label:
            raise ValueError("mindmap nodes require non-empty id and label")
        if node_id in seen_ids:
            raise ValueError("mindmap node ids must be unique")
        seen_ids.add(node_id)
        node_count += 1
        if node_count > MAX_MINDMAP_NODES:
            raise ValueError("mindmap contains too many nodes")

        children = node.get("children", [])
        if children is None:
            children = []
        if not isinstance(children, list):
            raise ValueError("mindmap children must be an array")
        result: Dict[str, Any] = {
            "id": node_id,
            "label": label,
            "children": [visit(child, depth + 1) for child in children],
        }
        note = _text(node.get("note"), 500)
        if note:
            result["note"] = note
        source_refs = node.get("source_refs")
        if source_refs:
            if not isinstance(source_refs, list):
                raise ValueError("mindmap source_refs must be an array")
            result["source_refs"] = [
                _text(item, 160) for item in source_refs[:8] if _text(item, 160)
            ]
        seen_objects.remove(object_id)
        return result

    return visit(root, 0)


def validate_mindmap_for_darya(tree_data: Dict[str, Any], topic: str) -> int:
    """Validate the content that DARYA will actually show before issuing PASS."""
    from services.content_quality_gate import KIND_LECTURE, prescan

    tree = normalize_mindmap_tree(tree_data)
    nodes: list[dict] = []
    pending = [tree]
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(node.get("children") or [])
    if len(nodes) < 4 or len(tree.get("children") or []) < 2:
        raise ValueError("MINDMAP_REVIEW_STRUCTURE_FAILED")
    node_text = "\n".join(
        str(node.get("label") or "") + " " + str(node.get("note") or "") for node in nodes
    )
    issues = prescan(KIND_LECTURE, {"content": node_text}, topic)
    # A mind map is a tree, so lecture-only heading rules do not apply here.
    blocking = [
        issue for issue in issues
        if issue.get("category") in {"placeholder", "topic_fidelity"}
        and str(issue.get("severity")) in {"high", "blocking"}
    ]
    if blocking:
        raise ValueError("MINDMAP_REVIEW_CONTENT_FAILED: " + str(blocking[0].get("description") or ""))
    return len(nodes)


def _result(raw: Any, fallback_title: str) -> Dict[str, Any]:
    payload = _json_object(raw)
    tree = normalize_mindmap_tree(payload)
    title = _text(payload.get("title"), 160) or _text(tree.get("label"), 160) or fallback_title
    summary = _text(payload.get("summary"), 400)
    return {
        "title": title,
        "summary": summary or f"已生成《{title}》学习思维导图。",
        "tree_data": tree,
    }


async def _call_llm(provider: Any, prompt: str) -> Any:
    generate = getattr(provider, "generate", None)
    if callable(generate):
        return await generate(prompt, max_tokens=3200)
    chat = getattr(provider, "chat", None)
    if callable(chat):
        return await chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=3200,
        )
    raise TypeError("LLM provider exposes neither generate nor chat")


async def generate_workspace_mindmap(
    *,
    topic: str = "",
    source_context: str = "",
    source_scope: str = "",
    instruction: str = "",
    llm=None,
) -> Dict[str, Any]:
    if not _text(topic, 160) and not _text(source_context, 100):
        raise ValueError("mindmap requires a topic or source material")
    prompt = f"""{skill_prompt_prefix("mindmap-authoring", max_chars_per_skill=1600)}You generate a learning mind map as one strict nested JSON tree.
Return JSON only:
{{"title":"title","summary":"short summary","tree":{{"id":"root","label":"root label","children":[{{"id":"unique-id","label":"node label","note":"optional","source_refs":["optional source location"],"children":[]}}]}}}}

Rules:
- Output one root and nested children only. Never output graph edges, coordinates, or cross-node links.
- Every id must be globally unique. Every label must be useful and concise.
- Prefer 4-7 first-level learning branches and 2-3 levels total.
- Organize concepts, principles, steps, applications, examples, and pitfalls; merge repeated details.
- For a large source, create a useful overview instead of copying every heading.
- Do not invent facts beyond the supplied source when source material is present.

Topic: {_text(topic, 160) or 'derive from the supplied source'}
Source scope: {_text(source_scope, 200) or 'all relevant supplied excerpts'}
User instruction: {_text(instruction, 2000) or 'make it suitable for learning'}
Source excerpts:
{str(source_context or '')[:MAX_SOURCE_CONTEXT_CHARS] or '(none)'}
"""
    provider = llm or await get_llm_service()
    raw = await _call_llm(provider, prompt)
    return _result(raw, _text(topic, 160) or "资料学习导图")


async def revise_workspace_mindmap(
    *,
    existing_tree: Dict[str, Any],
    instruction: str,
    source_context: str = "",
    llm=None,
) -> Dict[str, Any]:
    tree = normalize_mindmap_tree(existing_tree)
    prompt = f"""{skill_prompt_prefix("mindmap-authoring", max_chars_per_skill=1600)}Revise the existing learning mind map according to the instruction.
Return JSON only with title, summary, and one strict nested tree using the same schema.
Keep unchanged branches stable where possible. Use unique ids, no graph edges, no coordinates,
no cross-node links, at most {MAX_MINDMAP_NODES} nodes and depth at most {MAX_MINDMAP_DEPTH} below the root.

Instruction: {_text(instruction, 500)}
Existing tree: {json.dumps(tree, ensure_ascii=False, separators=(',', ':'))}
Relevant source excerpts:
{str(source_context or '')[:MAX_SOURCE_CONTEXT_CHARS] or '(none)'}
"""
    provider = llm or await get_llm_service()
    raw = await _call_llm(provider, prompt)
    return _result(raw, tree["label"])
