"""Ground artifact generation in retrieved sources instead of model recall.

The retrieval stack was real and already wired into the answer path, and the
artifact generators never touched it: a deck about "为什么天空是蓝色的" was
written from whatever the model happened to remember, and the content-quality
gate could only ask whether the result *looked* expert. It cannot ask whether it
is true.

This module closes that: before generating, fetch sources for the requested
topic and hand them to the generator as its only admissible facts, with the
sources carried back on the artifact so every claim can be traced to where it
came from.

Two honesty rules, both deliberate:

* a failed or empty retrieval is reported as ``searched: False`` — the generator
  still runs, but the artifact says it was written without sources instead of
  implying a grounding that does not exist;
* nothing here invents a citation. The reference list is exactly what came back.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# 交给生成器的来源正文上限：再长就只是在烧上下文，而不是在提供依据。
MAX_SOURCE_CHARS = 6000
MAX_SOURCES = 5


def _text(value: Any, limit: int) -> str:
    import re

    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def source_digest(bundle: Mapping[str, Any]) -> list[dict[str, str]]:
    """The reference list an artifact carries: where each fact could come from."""
    refs: list[dict[str, str]] = []
    for source in bundle.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        title = _text(source.get("title"), 200)
        url = _text(source.get("url"), 500)
        if not title and not url:
            continue
        refs.append({
            "title": title or url,
            "url": url,
            "publisher": _text(source.get("source") or source.get("publisher"), 120),
            "source_type": _text(source.get("source_type"), 40),
            "trust_reason": _text(source.get("trust_reason"), 200),
            "excerpt": _text(source.get("excerpt") or source.get("snippet"), 400),
        })
        if len(refs) >= MAX_SOURCES:
            break
    return refs


def source_context_text(refs: list[Mapping[str, str]]) -> str:
    lines: list[str] = []
    for index, ref in enumerate(refs, start=1):
        head = ref.get("title") or ref.get("url")
        where = f"（{ref['url']}）" if ref.get("url") else ""
        excerpt = ref.get("excerpt") or ""
        lines.append(f"[{index}] {head}{where}\n{excerpt}".strip())
    return "\n\n".join(lines)[:MAX_SOURCE_CHARS]


async def grounding_for_topic(
    topic: str, *, learning_goal: str = "", max_sources: int = MAX_SOURCES,
) -> dict[str, Any]:
    """Fetch sources for a topic. Never raises; a failure is a reported fact.

    Returns ``{searched, source_context, source_refs, note}`` — ``searched``
    false means the artifact was written without sources, and that is what the
    artifact will say.
    """
    query = _text(topic, 200)
    if not query:
        return {"searched": False, "source_context": "", "source_refs": [],
                "note": "没有可检索的主题。"}
    try:
        from services.tide_search_evidence import SearchContext, build_evidence_bundle
    except Exception as exc:  # noqa: BLE001
        return {"searched": False, "source_context": "", "source_refs": [],
                "note": f"检索未完成（{type(exc).__name__}），这一版没有外部来源可依。"}

    # 一次查询往往不够：「用黑板讲讲 TCP 三次握手为什么不能是两次」这样的原话，
    # 带着说话方式，检索命中率明显低于把它还原成主题词。所以按几种形态各试一次，
    # 合并去重 —— 这是把"检索覆盖不均"变成"多试几种问法"的最省事办法。
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    status = ""
    sufficient = False
    conflicts: list[str] = []
    attempted: list[str] = []
    decisions: list[str] = []
    plans: list[dict[str, str]] = []
    from services.darya_search_grounding import decide_search_plan

    for shape in query_shapes(query, learning_goal=learning_goal):
        attempted.append(shape)
        # 走生产决策，不用 force_search：后者是 QA 专用旁路，它自己的说明写着
        # 「验收显式绕过生产搜索决策」。产物生成属于"未安装 Domain Pack 的新学习
        # 主题"，正是 decide_search_plan 里 OPEN_TOPIC_GROUNDING 那一条要检索的情形。
        plan = decide_search_plan(
            message=shape, domain_status="UNSUPPORTED", installed_domain_id=None,
        )
        if not plan.should_search:
            plans.append({"query": shape, "decision": plan.decision, "trigger": plan.trigger})
            continue
        try:
            bundle = await build_evidence_bundle(
                SearchContext(message=f"{shape} {_text(learning_goal, 120)}".strip(), query_override=shape),
                search_intent=(True, plan.reason),
            )
            payload = bundle.public_dict() if hasattr(bundle, "public_dict") else dict(bundle)
        except Exception:  # noqa: BLE001 - 一种问法失败不该终止整次取源
            continue
        decisions.append(str(payload.get("search_decision") or ""))
        status = status or str(payload.get("status") or "")
        sufficient = sufficient or bool(payload.get("sufficient"))
        conflicts.extend(str(item) for item in (payload.get("conflicts") or [])[:2])
        for ref in source_digest(payload):
            key = str(ref.get("url") or ref.get("title") or "")
            if key and key not in seen:
                seen.add(key)
                refs.append(ref)
        if len(refs) >= max_sources:
            break

    refs = refs[:max_sources]
    if not refs:
        return {"searched": False, "source_context": "", "source_refs": [],
                "queries_tried": attempted, "search_decisions": decisions,
                "plan_decisions": plans,
                "note": "换了 " + str(len(attempted)) + " 种问法都没有检索到可用来源，这一版没有外部来源可依。"}
    return {
        "searched": True,
        "source_context": source_context_text(refs),
        "source_refs": refs,
        "queries_tried": attempted,
        "search_decisions": decisions,
        "status": status,
        "sufficient": sufficient,
        "conflicts": conflicts[:4],
        "note": f"已按 {len(refs)} 条检索来源生成；来源没有覆盖的内容不得写成既成事实。",
    }


# 说话方式不是检索词：带上它们，搜索引擎只会去匹配"用黑板讲讲"。
_INSTRUCTION_NOISE = (
    "用黑板讲讲", "用黑板讲", "黑板", "讲讲", "讲一讲", "说说", "解释一下", "解释",
    "帮我", "请你", "请", "为什么", "怎么", "如何", "是什么",
)

# 口语里的连接、判断和语气词。它们只当**分隔符**用，见 _keyword_shape。
_SPOKEN_FILLER = (
    "任何", "所有", "每一个", "每个", "到底", "其实", "真的", "老是", "总是", "一直",
    "应该", "要不要", "能不能", "会不会", "是不是", "可不可以", "哪些", "哪个",
    "怎么办", "怎么样", "怎样", "怎么", "如何", "为什么", "是什么",
    "也就是说", "就是说", "而且", "但是", "不过", "然后", "所以", "因为", "如果",
    "要是", "还有", "以及", "我们", "我的", "你的", "他的", "这个", "那个",
    "这些", "那些", "自己", "一下",
)


def _keyword_shape(text: str) -> str:
    """把一句话砍成关键词串：留最长的两三段内容，其余当分隔符丢掉。

    只把填充词当分隔符，**不在片段内部替换任何字**——「光的折射」里的「的」是术语的
    一部分，逐字删会把它毁成「光折射」。所以先切开，再按长度取前两段。
    """
    parts: list[str] = []
    buffer = ""
    index = 0
    while index < len(text):
        matched = next((word for word in _SPOKEN_FILLER if text.startswith(word, index)), "")
        if matched:
            if len(buffer) >= 2:
                parts.append(buffer)
            buffer = ""
            index += len(matched)
            continue
        character = text[index]
        if character in " ，。；：、！？!?,;:.（）()【】[]“”\"'":
            if len(buffer) >= 2:
                parts.append(buffer)
            buffer = ""
        else:
            buffer += character
        index += 1
    if len(buffer) >= 2:
        parts.append(buffer)
    parts.sort(key=len, reverse=True)
    return " ".join(parts[:2]).strip()


def query_shapes(topic: str, *, learning_goal: str = "", limit: int = 3) -> list[str]:
    """A few ways of asking the same thing, cheapest first.

    The raw words stay first because they are what the learner actually asked;
    the later shapes exist because a question phrased for a teacher is a poor
    search query, and one empty round should not decide that a topic has no
    sources at all.
    """
    base = _text(topic, 200)
    if not base:
        return []
    stripped = base
    for noise in _INSTRUCTION_NOISE:
        stripped = stripped.replace(noise, " ")
    stripped = _text(stripped.replace("？", " ").replace("?", " ").replace("：", " "), 160)
    shapes = [base]
    if stripped and stripped != base:
        shapes.append(stripped)
    # 去掉提问外壳之后，句子往往还是句子。2026-09-21 实测：「动量守恒在任何碰撞里都
    # 成立」原样检索 1 种问法 0 命中，而同一件事的关键词「动量守恒 碰撞」立刻有来源。
    keyworded = _keyword_shape(stripped or base)
    if keyworded and keyworded not in shapes:
        shapes.append(keyworded)
    goal = _text(learning_goal, 120)
    if goal and goal not in shapes:
        shapes.append(goal)
    unique: list[str] = []
    for shape in shapes:
        if len(shape) >= 4 and shape not in unique:
            unique.append(shape)
    return unique[:limit]


def grounding_prompt_rule(grounding: Mapping[str, Any]) -> str:
    """The line the generator gets, matching whether sources actually arrived."""
    if grounding.get("searched"):
        return (
            "只使用来源资料中明确给出的事实；来源没有覆盖的内容不要写成结论，"
            "更不要补造数字、年份或引用。"
        )
    return (
        "这一轮没有可用的外部来源：只写你有把握的机制与条件，"
        "不要编造具体数字、年份、研究或引用。"
    )
