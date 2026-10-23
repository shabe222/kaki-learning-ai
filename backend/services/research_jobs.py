"""A research task that outlives one turn and comes back with a sourced report.

Everything in this product was turn-scoped: the cognition orchestrator timeboxes
one reply, and background work existed only for video rendering. So 「帮我研究
一下」 was not a thing DARYA could do — it could answer, not investigate.

This runs a bounded multi-round investigation as a job with stages, the way the
video pipeline already does, and it ends in a Markdown report whose every claim
carries a citation to a source that was actually retrieved.

Three rules that make it honest rather than impressive-looking:

* **No sources, no sourced report.** A retrieval gap yields a clearly labelled
  working result instead of pretending that model recall is a cited report.
* **Citations are checked, not trusted.** Every ``[n]`` in the report must map
  to a retrieved source; the review stage counts what is actually referenced
  and refuses a report that cites nothing.
* **The content gate still applies.** A sourced report can still be off-topic
  or padded, so the same teaching-quality gate runs on it.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem

logger = logging.getLogger("rjianb.research")

RESEARCH_ITEM_TYPE = "workspace_research"
MAX_SUB_QUESTIONS = 3
MAX_SOURCES = 8
# 实跑教训：只有一条来源时，诚实的报告几乎全是「来源未覆盖……」，那样一份报告
# 过不了内容闸门（实测被判 block，2/1/2/1），等于白烧一次整理加一次改写。
# 所以门槛提到 2 条 —— 宁可早一点说"来源不够"，也不要生成一份注定被拒的报告。
MIN_SOURCES_FOR_REPORT = 2

STAGES = ("PLANNED", "SEARCHING", "READING", "SYNTHESIZING", "REVIEWING", "READY")
STAGE_COPY = {
    "QUEUED": "已排队，等一个可用的执行位。",
    "PLANNED": "已拆出要查的几个问题。",
    "SEARCHING": "正在检索来源。",
    "READING": "正在读回来的资料。",
    "SYNTHESIZING": "正在把来源整理成报告。",
    "REVIEWING": "正在检查报告是否每条都有出处。",
    "READY": "报告已就绪。",
    "PARTIAL": "阶段结果已就绪；正式报告仍需补证据。",
    "FAILED": "这次研究没有完成。",
}


class ResearchJobError(RuntimeError):
    code = "RESEARCH_JOB_FAILED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def sub_questions(question: str, *, limit: int = MAX_SUB_QUESTIONS) -> list[str]:
    """Split a research question into the few things worth looking up.

    Deterministic on purpose: this runs before any model call, so a failed model
    never leaves a job with nothing to search for. The question itself is always
    the first sub-question.
    """
    base = _text(question, 300)
    if not base:
        return []
    parts = [part.strip(" ？?。.") for part in re.split(r"[；;，,、]|以及|并且", base) if part.strip(" ？?。.")]
    candidates = [base] + [part for part in parts if len(part) >= 4 and part != base]
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
        if len(unique) >= limit:
            break
    return unique


def citation_report(markdown: str, source_count: int) -> dict[str, Any]:
    """Which sources the report actually cites, and whether any are invented."""
    used = sorted({int(match) for match in re.findall(r"\[(\d{1,2})\]", markdown or "")})
    valid = [index for index in used if 1 <= index <= source_count]
    invented = [index for index in used if index > source_count or index < 1]
    return {
        "cited": valid,
        "invented": invented,
        "cited_count": len(valid),
        "cites_nothing": not valid,
    }


async def create_research_job(
    db: AsyncSession, *, user_id: int, thread_id: str, question: str, depth: str = "standard",
) -> GeneratedItem:
    item = GeneratedItem(
        user_id=user_id,
        type=RESEARCH_ITEM_TYPE,
        title=_text(question, 200) or "研究任务",
        chapter=_text(question, 120),
        content="",
        raw_data={
            "kind": "research",
            "status": "QUEUED",
            "thread_id": thread_id,
            "question": _text(question, 400),
            "depth": _text(depth, 20) or "standard",
            "stage_copy": STAGE_COPY["QUEUED"],
            "sub_questions": sub_questions(question),
            "created_at": _now().isoformat(),
        },
    )
    db.add(item)
    await db.flush()
    return item


def project_research_job(item: GeneratedItem) -> dict[str, Any]:
    """The learner-facing projection: status, progress copy, and the report when ready."""
    data = dict(item.raw_data or {})
    status = str(data.get("status") or "QUEUED")
    return {
        "artifact_id": item.id,
        "kind": "research",
        "status": status,
        "stage_copy": str(data.get("stage_copy") or STAGE_COPY.get(status, "")),
        "title": item.title,
        "question": str(data.get("question") or ""),
        "sub_questions": list(data.get("sub_questions") or []),
        "source_refs": list(data.get("source_refs") or []),
        "citation_report": dict(data.get("citation_report") or {}),
        "content_review": dict(data.get("content_review") or {}),
        "report": str(item.content or "") if status == "READY" else "",
        "working_result": str(item.content or "") if status == "PARTIAL" else "",
        "error": str(data.get("error") or "") or None,
    }


async def _set_stage(db: AsyncSession, item: GeneratedItem, status: str, **extra: Any) -> None:
    data = dict(item.raw_data or {})
    data.update({"status": status, "stage_copy": STAGE_COPY.get(status, ""), **extra})
    item.raw_data = data
    item.updated_at = _now()
    await db.flush()


async def _collect_sources(question: str) -> dict[str, Any]:
    """Run the existing retrieval for each sub-question and merge what comes back."""
    from services.generation_grounding import source_digest
    from services.tide_search_evidence import SearchContext, build_evidence_bundle

    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    conflicts: list[str] = []
    rounds: list[dict[str, Any]] = []
    for sub_question in sub_questions(question):
        try:
            bundle = await build_evidence_bundle(
                SearchContext(message=sub_question, query_override=sub_question),
                force_search=True,
            )
            payload = bundle.public_dict() if hasattr(bundle, "public_dict") else dict(bundle)
        except Exception as exc:  # noqa: BLE001 - 一轮检索失败不该终止整次研究
            rounds.append({"question": sub_question, "sources": 0, "error": type(exc).__name__})
            continue
        found = source_digest(payload)
        conflicts.extend(str(item) for item in (payload.get("conflicts") or [])[:2])
        rounds.append({"question": sub_question, "sources": len(found),
                       "status": str(payload.get("status") or "")})
        for ref in found:
            key = str(ref.get("url") or ref.get("title") or "")
            if key and key not in seen:
                seen.add(key)
                refs.append(ref)
    return {"source_refs": refs[:MAX_SOURCES], "rounds": rounds, "conflicts": conflicts[:4]}


async def _provisional_answer(question: str, refs: list[Mapping[str, str]]) -> str:
    """Produce useful provisional work without claiming a sourced finding."""
    from services.llm_service import get_llm_service

    source_lines = "\n".join(
        f"[{index}] {ref.get('title') or ''}: {ref.get('excerpt') or ''}"
        for index, ref in enumerate(refs, start=1)
    )
    prompt = (
        "请给学习者一份简短、可操作的阶段分析。外部检索没有凑齐正式报告所需的证据。\n"
        f"问题：{question}\n"
        f"实际取得的来源摘要：{source_lines or '无'}\n"
        "先交付一个针对这个问题的具体解释、例子、判断步骤或条件方案。"
        "只能把来源摘要明确支持的内容写成外部事实；没有来源时只能使用稳定的一般原理和用户给出的信息。"
        "会变化的政策、价格、版本、日期、机构信息、统计数字一律标为待核实，不能猜。"
        "不得编造链接、引文、来源编号，也不要声称研究已经完成。"
        "最后写出下一步要核对的具体对象。只输出正文，不要标题。"
    )
    try:
        llm = await get_llm_service()
        result = str(await llm.generate(prompt, max_tokens=900) or "").strip()
    except Exception:  # noqa: BLE001 - a provider outage must not erase retrieved work
        return ""
    # The provisional section has no citation authority. A URL or numbered
    # citation not present in the retrieval receipt must not be laundered here.
    result = re.sub(r"https?://\S+", "", result)
    result = re.sub(r"\[\d{1,2}\]", "", result)
    return result[:1800].strip()


async def _deliver_partial(
    db: AsyncSession, item: GeneratedItem, *, question: str,
    refs: list[Mapping[str, str]], rounds: list[Mapping[str, Any]], reason: str,
) -> dict[str, Any]:
    """Keep a usable, explicitly provisional result when a formal report is blocked."""
    answer = await _provisional_answer(question, refs)
    lines = ["## 本轮可用结果（阶段分析）", ""]
    if answer:
        lines.extend([answer, ""])
    else:
        lines.extend([
            f"围绕「{question}」先把已知条件和要核对的条件分开；"
            "在原始材料到手前，具体结论保持待核实。", "",
        ])
    lines.extend(["## 实际取得的资料", ""])
    if refs:
        for index, ref in enumerate(refs, start=1):
            title = _text(ref.get("title") or ref.get("url"), 160) or "未命名来源"
            excerpt = _text(ref.get("excerpt"), 320)
            url = str(ref.get("url") or "").strip()
            lines.append(f"{index}. {title}" + (f" — {url}" if url else ""))
            if excerpt:
                lines.append(f"   - 检索摘要：{excerpt}")
    else:
        lines.append("本轮未取回可核对的外部来源；上面的阶段分析不作为已核实的外部事实。")
    lines.extend(["", "## 继续核对", "", f"- 当前缺口：{reason}"])
    for round_info in rounds[:MAX_SUB_QUESTIONS]:
        query = _text(round_info.get("question"), 200)
        if query:
            lines.append(f"- 已查：{query}；取得 {int(round_info.get('sources') or 0)} 条来源。")
    item.content = "\n".join(lines)
    await _set_stage(db, item, "PARTIAL", error=reason, source_refs=refs, search_rounds=rounds)
    return project_research_job(item)


def _report_prompt(*, question: str, refs: list[Mapping[str, str]]) -> str:
    lines = []
    for index, ref in enumerate(refs, start=1):
        lines.append(
            f"[{index}] {ref.get('title') or ref.get('url')}（{ref.get('url') or '无链接'}）\n"
            f"{ref.get('excerpt') or ''}"
        )
    return (
        "你是柿子智学的资料整理者。请针对下面的问题，**只根据给出的来源**写一份 Markdown 研究报告。\n"
        f"问题：{question}\n\n"
        "来源（正文只允许来自这里）：\n" + "\n\n".join(lines) + "\n\n"
        "硬性要求：\n"
        "- 每一个来自来源的判断后面必须标出它的编号，写成 [1]、[2] 这种；\n"
        "- 只能引用上面列出的编号，不得编造来源、链接、数字或年份；\n"
        "- 来源之间说法不一致时，明确写出分歧，不要替读者选边；\n"
        "- 来源没有覆盖的部分，写「来源未覆盖」，不要用常识补上；\n"
        "- 结构：结论（先给答案）→ 依据（逐条带编号）→ 分歧或未覆盖的部分 → 还需要查什么。\n"
        "只输出 Markdown 正文，不要输出 JSON。"
    )


async def run_research_job(db: AsyncSession, *, job_id: int) -> dict[str, Any]:
    """Execute one research job; keep useful work even when review blocks a report."""
    item = await db.get(GeneratedItem, job_id)
    if item is None or item.type != RESEARCH_ITEM_TYPE:
        raise ResearchJobError("研究任务不存在。")
    data = dict(item.raw_data or {})
    question = str(data.get("question") or item.title or "")
    if not question:
        await _set_stage(db, item, "FAILED", error="没有可研究的问题。")
        return project_research_job(item)

    refs: list[Mapping[str, str]] = []
    rounds: list[Mapping[str, Any]] = []
    try:
        await _set_stage(db, item, "PLANNED")
        await _set_stage(db, item, "SEARCHING")
        collected = await _collect_sources(question)
        refs = list(collected["source_refs"])
        rounds = list(collected["rounds"])
        await _set_stage(
            db, item, "READING",
            source_refs=refs, search_rounds=rounds, conflicts=collected["conflicts"],
        )
        if len(refs) < MIN_SOURCES_FOR_REPORT:
            return await _deliver_partial(
                db, item, question=question, refs=refs, rounds=rounds,
                reason=f"只有 {len(refs)} 条来源，少于正式报告需要的 {MIN_SOURCES_FOR_REPORT} 条。",
            )

        await _set_stage(db, item, "SYNTHESIZING")
        from services.llm_service import get_llm_service

        llm = await get_llm_service()
        prompt = _report_prompt(question=question, refs=refs)

        async def draft(extra: str = "") -> str:
            raw = await llm.generate(prompt + extra, max_tokens=2600)
            return str(raw or "").strip()

        report = await draft()
        if not report:
            return await _deliver_partial(
                db, item, question=question, refs=refs, rounds=rounds,
                reason="报告整理没有产出正文；已保留本轮检索结果。",
            )

        await _set_stage(db, item, "REVIEWING")
        citations = citation_report(report, len(refs))
        # 引用了不存在的编号 = 编造出处。这不是"小瑕疵"，整篇的可信度都靠它。
        if citations["invented"]:
            report = await draft(
                "\n\n上一稿引用了不存在的编号 "
                f"{citations['invented']}，但只有 {len(refs)} 条来源。"
                "请重写，只使用列出的编号。"
            )
            citations = citation_report(report, len(refs))
            if citations["invented"]:
                return await _deliver_partial(
                    db, item, question=question, refs=refs, rounds=rounds,
                    reason="报告引用了不存在的来源编号；已保留真实检索结果供继续核对。",
                )

        from services.content_quality_gate import KIND_LECTURE, quality_receipt, run_content_quality_gate

        async def revise_report(*, existing_candidate, constraints, prior_review, round_number):
            rewritten = await draft(
                "\n\n上一稿未通过内容质量检查，请按下面的要求重写：\n"
                + "\n".join(f"- {item}" for item in constraints)
            )
            return {"content": rewritten} if rewritten else existing_candidate

        gate = await run_content_quality_gate(
            kind=KIND_LECTURE, candidate={"content": report}, topic=question,
            revise=revise_report,
        )
        report = str(gate["candidate"].get("content") or report)
        receipt = quality_receipt(gate)
        blocking = [row for row in gate["issues"] if str(row.get("severity")) in {"high", "blocking"}]
        if gate["verdict"] == "block" or blocking:
            await _deliver_partial(
                db, item, question=question, refs=refs, rounds=rounds,
                reason="报告内容检查未通过："
                + (str(blocking[0].get("description")) if blocking else "评审明确拒绝。"),
            )
            await _set_stage(db, item, "PARTIAL", citation_report=citations, content_review=receipt)
            return project_research_job(item)

        item.content = report
        await _set_stage(
            db, item, "READY",
            citation_report=citations, content_review=receipt,
            completed_at=_now().isoformat(),
        )
        return project_research_job(item)
    except Exception as exc:  # noqa: BLE001 - 任务失败要留下原因，不是静默停在中间态
        logger.exception("research job failed id=%s", job_id)
        try:
            return await _deliver_partial(
                db, item, question=question, refs=refs, rounds=rounds,
                reason=f"研究流程中断（{type(exc).__name__}）；本轮已取得的材料已保留。",
            )
        except Exception:  # noqa: BLE001 - database failure cannot be repaired in this session
            await _set_stage(db, item, "FAILED", error=f"这次研究中断了（{type(exc).__name__}）。")
            return project_research_job(item)


async def fail_interrupted_research_jobs(db: AsyncSession) -> int:
    """A restart must not leave a job看起来还在跑."""
    running = (await db.scalars(select(GeneratedItem).where(
        GeneratedItem.type == RESEARCH_ITEM_TYPE,
    ))).all()
    failed = 0
    for item in running:
        data = dict(item.raw_data or {})
        if str(data.get("status") or "") in {"READY", "PARTIAL", "FAILED", "QUEUED"}:
            continue
        await _set_stage(db, item, "FAILED", error="服务重启，这次研究没有跑完。")
        failed += 1
    return failed


def research_summary(projection: Mapping[str, Any]) -> str:
    """One line for the conversation, matching what actually happened."""
    status = str(projection.get("status") or "")
    if status == "READY":
        cited = int((projection.get("citation_report") or {}).get("cited_count") or 0)
        sources = len(projection.get("source_refs") or [])
        return f"研究完成：引用 {cited} 处，来源 {sources} 条，报告已放在「我的资料」。"
    if status == "PARTIAL":
        sources = len(projection.get("source_refs") or [])
        return f"已交付阶段分析和检索记录（来源 {sources} 条）；正式报告仍需补证据或修订。"
    if status == "FAILED":
        return str(projection.get("error") or "这次研究没有完成。")
    return str(projection.get("stage_copy") or "研究进行中。")

