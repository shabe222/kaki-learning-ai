"""Confirmed presentation-improvement actions for Tide Phase 2.3+.

Analysis creates a proposal and a private candidate only.  The immutable
GeneratedItemVersion is created exclusively after an authenticated confirm.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Awaitable, Callable, Literal, Mapping

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    GeneratedItem,
    GeneratedItemVersion,
    TideArtifactAction,
    TideWorkspaceObject,
)
from services.tide_workspace_object import get_current_workspace_object
from services.tide_shared_perspective import get_latest_snapshot, public_snapshot
from services.tide_artifact_quality_gate import (
    ArtifactReviewer,
    public_quality_summary,
    run_artifact_quality_gate,
)
from services.tide_artifact_review import review_artifact_candidate
from services.workspace_artifact_versions import (
    ArtifactVersionError,
    create_new_version,
    ensure_initial_version,
    get_owned_artifact,
    get_version,
)
from services.workspace_presentations import revise_workspace_presentation


ArtifactActionControl = Literal["confirm", "cancel"]
SUPPORTED_ARTIFACT_TYPES = {"workspace_presentation"}
IMPROVEMENT_SIGNAL = re.compile(
    r"改进|改善|完善|优化|修改|调整|重构|润色|提升|"
    r"更(?:清楚|明确|具体)|不够(?:清楚|明确|具体)|还是(?:太|有点)?(?:泛|笼统|模糊)|"
    r"improve|revise|refine|polish|strengthen|restructure|rewrite|"
    r"still\s+too|too\s+(?:generic|vague|broad)|make.{0,80}(?:clearer|specific|concrete)",
    re.I,
)
PAGE_PATTERN = re.compile(r"(?:第\s*(\d+)\s*页|page\s*(\d+))", re.I)
MEANINGFUL_SLIDE_FIELDS = (
    "title", "subtitle", "body", "items", "narration", "code", "language",
    "layout", "visual_references", "visuals", "interaction",
)
GENERATOR_SLIDE_FIELDS = (
    "title", "subtitle", "body", "items", "narration", "code", "language", "interaction",
)

RevisionGenerator = Callable[..., Awaitable[dict[str, Any]]]
logger = logging.getLogger(__name__)


class ArtifactActionError(ValueError):
    def __init__(self, message: str, *, code: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n")[:limit]


def is_artifact_improvement_request(message: str) -> bool:
    return bool(IMPROVEMENT_SIGNAL.search(str(message or "")))


def _request_digest(user_id: int, thread_id: str, request_key: str) -> str:
    return hashlib.sha256(
        f"tide-artifact-action-v1\0{user_id}\0{thread_id}\0{request_key}".encode("utf-8")
    ).hexdigest()


def _workspace_artifact_reference(value: TideWorkspaceObject) -> dict[str, Any]:
    refs = value.related_artifact_references or []
    first = refs[0] if isinstance(refs, list) and refs else None
    if not isinstance(first, dict) or not first.get("artifact_id"):
        raise ArtifactActionError(
            "当前内容还没有可以直接改进的成稿。",
            code="ARTIFACT_NOT_LINKED",
            status_code=404,
        )
    return dict(first)


def _references_artifact(reference: Any, artifact_id: int) -> bool:
    if not isinstance(reference, dict):
        return False
    try:
        return int(reference.get("artifact_id") or 0) == artifact_id
    except (TypeError, ValueError):
        return False


async def _resolve_source(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    workspace_object: TideWorkspaceObject,
) -> tuple[GeneratedItem, GeneratedItemVersion, dict[str, Any]]:
    reference = _workspace_artifact_reference(workspace_object)
    try:
        artifact_id = int(reference["artifact_id"])
    except (TypeError, ValueError) as exc:
        raise ArtifactActionError("工作对象中的产物引用无效。", code="ARTIFACT_REFERENCE_INVALID") from exc
    try:
        item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
        if item.type not in SUPPORTED_ARTIFACT_TYPES:
            raise ArtifactActionError(
                "DARYA 当前只支持演示文稿改进。",
                code="ARTIFACT_TYPE_NOT_SUPPORTED",
            )
        metadata = dict(item.raw_data or {})
        if metadata.get("thread_id") and str(metadata["thread_id"]) != thread_id:
            raise ArtifactActionError(
                "这个产物不属于当前对话。",
                code="ARTIFACT_THREAD_MISMATCH",
                status_code=404,
            )
        await ensure_initial_version(db, item)
        version_id = reference.get("version_id")
        source = await get_version(
            db,
            item=item,
            version_id=int(version_id) if version_id else item.current_version_id,
        )
        return item, source, reference
    except ArtifactVersionError as exc:
        raise ArtifactActionError(str(exc), code=exc.code, status_code=exc.status_code) from exc


def _page_scope(
    instruction: str,
    slides: list[Mapping[str, Any]],
    *,
    page_hint: int | None = None,
) -> tuple[int | None, Mapping[str, Any] | None]:
    match = PAGE_PATTERN.search(instruction)
    number = int(match.group(1) or match.group(2)) if match else page_hint
    if number is None or number < 1 or number > len(slides):
        return number, None
    return number, slides[number - 1]


def _proposal_page_number(value: TideArtifactAction | None) -> int | None:
    if value is None:
        return None
    proposal = dict(value.proposal or {})
    raw_number = proposal.get("page_number")
    try:
        if raw_number is not None and int(raw_number) > 0:
            return int(raw_number)
    except (TypeError, ValueError):
        pass
    match = PAGE_PATTERN.search(str(proposal.get("scope") or ""))
    return int(match.group(1) or match.group(2)) if match else None


def _slide_signature(value: Mapping[str, Any] | None) -> str:
    slide = dict(value or {})
    return json.dumps(
        {key: slide.get(key) for key in MEANINGFUL_SLIDE_FIELDS if slide.get(key) not in (None, "", [], {})},
        ensure_ascii=False,
        sort_keys=True,
    )


def _generator_slide_signature(value: Mapping[str, Any] | None) -> str:
    slide = dict(value or {})
    return json.dumps(
        {key: slide.get(key) for key in GENERATOR_SLIDE_FIELDS if slide.get(key) not in (None, "", [], {})},
        ensure_ascii=False,
        sort_keys=True,
    )


def _scope_candidate_deck(
    *, existing_deck: Mapping[str, Any], candidate_deck: Mapping[str, Any], page_number: int | None,
) -> dict[str, Any]:
    """Keep untouched pages exact and preserve target metadata the model omitted."""
    candidate = dict(candidate_deck)
    source_slides = [dict(slide) for slide in (existing_deck.get("slides") or []) if isinstance(slide, Mapping)]
    generated_slides = [dict(slide) for slide in (candidate.get("slides") or []) if isinstance(slide, Mapping)]
    if page_number and 1 <= page_number <= len(source_slides):
        index = page_number - 1
        revised = dict(source_slides[index])
        generated_target = generated_slides[index] if index < len(generated_slides) else None
        if generated_target and _generator_slide_signature(generated_target) != _generator_slide_signature(source_slides[index]):
            revised.update(generated_target)
        else:
            alternate_pages = [
                slide for candidate_index, slide in enumerate(generated_slides)
                if (
                    candidate_index >= len(source_slides)
                    or _generator_slide_signature(slide) != _generator_slide_signature(source_slides[candidate_index])
                )
            ]
            if len(alternate_pages) == 1:
                revised.update(alternate_pages[0])
            elif generated_target:
                revised.update(generated_target)
        revised["index"] = source_slides[index].get("index", index)
        scoped = [dict(slide) for slide in source_slides]
        scoped[index] = revised
        candidate["title"] = str(existing_deck.get("title") or candidate.get("title") or "")
        candidate["slides"] = scoped
    else:
        candidate["slides"] = generated_slides
    return candidate


def _has_meaningful_page_change(
    *, existing_deck: Mapping[str, Any], candidate_deck: Mapping[str, Any], page_number: int | None,
) -> bool:
    source_slides = list(existing_deck.get("slides") or [])
    candidate_slides = list(candidate_deck.get("slides") or [])
    if page_number and 1 <= page_number <= len(source_slides) and page_number <= len(candidate_slides):
        index = page_number - 1
        return _slide_signature(source_slides[index]) != _slide_signature(candidate_slides[index])
    return json.dumps(source_slides, ensure_ascii=False, sort_keys=True) != json.dumps(
        candidate_slides, ensure_ascii=False, sort_keys=True,
    )


def _candidate_materially_changed(
    *, left: Mapping[str, Any], right: Mapping[str, Any], page_number: int | None,
) -> bool:
    left_slides = list(left.get("slides") or [])
    right_slides = list(right.get("slides") or [])
    if page_number and 1 <= page_number <= len(left_slides) and page_number <= len(right_slides):
        index = page_number - 1
        return _slide_signature(left_slides[index]) != _slide_signature(right_slides[index])
    return json.dumps(dict(left), ensure_ascii=False, sort_keys=True) != json.dumps(
        dict(right), ensure_ascii=False, sort_keys=True,
    )


def _proposal(
    *,
    workspace_object: TideWorkspaceObject,
    source: GeneratedItemVersion,
    instruction: str,
    page_hint: int | None = None,
) -> tuple[str, dict[str, Any]]:
    private = dict(source.private_data_snapshot or {})
    public = dict(source.public_data_snapshot or {})
    slides = list(private.get("slides") or public.get("slides") or [])
    page_number, slide = _page_scope(instruction, slides, page_hint=page_hint)
    slide_title = _clean((slide or {}).get("title"), 200)
    scope = f"第 {page_number} 页" if page_number else "当前演示文稿"
    observed = f"「{slide_title}」" if slide_title else "现有内容"
    reason = (
        f"{scope}{observed}需要更直接地服务于工作目的「{workspace_object.purpose}」，"
        "同时保留未要求改变的有效内容。"
    )
    changes = [
        f"按当前指令聚焦{scope}",
        f"强化内容与「{workspace_object.purpose}」的连接",
        "保留其他页面和已有有效信息",
    ]
    return reason, {
        "scope": scope,
        "page_number": page_number,
        "instruction": _clean(instruction, 1000),
        "changes": changes,
        "source_version_number": source.version_number,
        "requires_confirmation": True,
    }


async def get_latest_artifact_action(
    db: AsyncSession, *, user_id: int, thread_id: str,
) -> TideArtifactAction | None:
    return (await db.execute(
        select(TideArtifactAction)
        .where(
            TideArtifactAction.user_id == user_id,
            TideArtifactAction.thread_id == thread_id,
        )
        .order_by(desc(TideArtifactAction.created_at), desc(TideArtifactAction.id))
        .limit(1)
    )).scalar_one_or_none()


async def create_artifact_action_proposal(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    instruction: str,
    request_key: str,
    revision_generator: RevisionGenerator = revise_workspace_presentation,
    artifact_reviewer: ArtifactReviewer = review_artifact_candidate,
    max_review_rounds: int = 2,
) -> TideArtifactAction | None:
    if not is_artifact_improvement_request(instruction):
        return None
    workspace_object = await get_current_workspace_object(
        db, user_id=user_id, thread_id=thread_id,
    )
    if workspace_object is None:
        return None

    digest = _request_digest(user_id, thread_id, request_key)
    existing = await db.scalar(select(TideArtifactAction).where(
        TideArtifactAction.request_key_digest == digest,
    ))
    if existing is not None:
        return existing

    item, source, _reference = await _resolve_source(
        db,
        user_id=user_id,
        thread_id=thread_id,
        workspace_object=workspace_object,
    )
    previous = await get_latest_artifact_action(db, user_id=user_id, thread_id=thread_id)
    continued_page = None
    if (
        PAGE_PATTERN.search(instruction) is None
        and previous is not None
        and previous.artifact_id == item.id
        and previous.status == "completed"
        and previous.target_version_id == source.id
    ):
        continued_page = _proposal_page_number(previous)
    reason, proposal = _proposal(
        workspace_object=workspace_object,
        source=source,
        instruction=instruction,
        page_hint=continued_page,
    )
    action = TideArtifactAction(
        user_id=user_id,
        thread_id=thread_id,
        workspace_object_id=workspace_object.id,
        artifact_id=item.id,
        source_version_id=source.id,
        target_version_id=None,
        action_type="improve_presentation",
        reason=reason,
        status="analyzing",
        proposal=proposal,
        request_key_digest=digest,
        revision=1,
    )
    db.add(action)
    await db.flush()

    source_private = dict(source.private_data_snapshot or {})
    source_public = dict(source.public_data_snapshot or {})
    existing_deck = {
        "title": source.title_snapshot,
        "slides": list(source_private.get("slides") or source_public.get("slides") or []),
    }
    if not existing_deck["slides"]:
        action.status = "failed"
        action.reason = "当前演示文稿没有可分析的页面内容，原版本保持不变。"
        action.revision += 1
        await db.flush()
        return action

    try:
        focus_instruction = (
            f"{_clean(instruction, 1000)}。只继续修改第 {proposal['page_number']} 页。"
            if proposal.get("page_number") else _clean(instruction, 1000)
        )
        candidate_deck = await revision_generator(
            existing_deck=existing_deck,
            instruction=(
                f"{focus_instruction}。工作目的：{workspace_object.purpose}。"
                "只修改要求涉及的页面，保留其他有效内容。"
            ),
            delivery_mode="presentation_only",
        )
        candidate_deck = _scope_candidate_deck(
            existing_deck=existing_deck,
            candidate_deck=candidate_deck,
            page_number=proposal.get("page_number"),
        )
        if not _has_meaningful_page_change(
            existing_deck=existing_deck,
            candidate_deck=candidate_deck,
            page_number=proposal.get("page_number"),
        ):
            target_index = int(proposal.get("page_number") or 1) - 1
            target_slides = list(existing_deck.get("slides") or [])
            target_context = json.dumps(
                target_slides[target_index] if 0 <= target_index < len(target_slides) else {},
                ensure_ascii=False,
            )[:4000]
            candidate_deck = await revision_generator(
                existing_deck=existing_deck,
                instruction=(
                    f"{focus_instruction}。上一次尝试没有改变目标页面的可见内容。"
                    f"当前目标页是：{target_context}。"
                    "这次必须实际改写目标页的正文或要点，不能原样返回。"
                    "把价值表达为评委需要看懂、追踪或核查的内容要求；不要声称证据已经存在。"
                    "不得发明来源中没有的指标、用户结果、研究、引文、证据或能力；其他页面保持不变。"
                ),
                delivery_mode="presentation_only",
            )
            candidate_deck = _scope_candidate_deck(
                existing_deck=existing_deck,
                candidate_deck=candidate_deck,
                page_number=proposal.get("page_number"),
            )
        if not _has_meaningful_page_change(
            existing_deck=existing_deck,
            candidate_deck=candidate_deck,
            page_number=proposal.get("page_number"),
        ):
            raise ValueError("presentation revision did not change visible content")

        perspective = await get_latest_snapshot(db, user_id=user_id, thread_id=thread_id)
        review_context = {
            "artifact_kind": item.type,
            "user_request": _clean(instruction, 1000),
            "shared_perspective": public_snapshot(
                perspective,
                thread_id=thread_id,
                workspace_object_id=workspace_object.id,
            ),
            "workspace_object": {
                "id": workspace_object.id,
                "title": workspace_object.title,
                "purpose": workspace_object.purpose,
                "status": workspace_object.status,
                "next_action": workspace_object.next_action,
                "related_decisions": list(workspace_object.related_decisions or []),
            },
            "artifact_metadata": {
                "artifact_id": item.id,
                "type": item.type,
                "title": source.title_snapshot,
                "chapter": source.chapter_snapshot,
                "source_version_id": source.id,
                "source_version_number": source.version_number,
                "focused_page_number": proposal.get("page_number"),
            },
            "evidence_status": {
                "availability": "context_only",
                "available_context": [
                    "user_request", "shared_perspective", "workspace_object", "source_artifact",
                ],
                "external_retrieval": "not_implemented",
                "citations": [],
            },
        }

        async def revise_from_review(
            *, existing_candidate: Mapping[str, Any], constraints: list[str],
            prior_review: Mapping[str, Any], round_number: int,
        ) -> dict[str, Any]:
            feedback = json.dumps({
                "round": round_number,
                "constraints": constraints,
                "claim_statuses": list(prior_review.get("claims") or []),
            }, ensure_ascii=False)[:7000]
            return await revision_generator(
                existing_deck=existing_candidate,
                instruction=(
                    f"{focus_instruction}。工作目的：{workspace_object.purpose}。"
                    f"这是独立审阅后必须落实的约束：{feedback}。"
                    "逐项落实约束并保留仍然有效的原内容。"
                    "UNVERIFIED 声明必须删除，或改成设计意图、需求、假设、待验证项；不得发明引用或证据。"
                    "逐句检查目标页的 title、subtitle、body、items、narration，items 中的功能性名词短语也必须收紧。"
                    "不要只在正文加 aims to，却让要点继续暗示能力已经存在；可用“设计目标：”“待演示：”“需要验证：”明确边界。"
                    "在没有当前证据支持时，避免 ensure、enable、support、provide、can、let、record、show、link、map、traceable 等既成能力动词。"
                    "要点优先改成不含事实断言的核查标签，例如“待演示：目标与产物的关联”“待验证：版本历史的可解释性”。"
                    "仍然只修改指定页面，其他页面保持不变。"
                ),
                delivery_mode="presentation_only",
            )

        def normalize_reviewed_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
            return _scope_candidate_deck(
                existing_deck=existing_deck,
                candidate_deck=value,
                page_number=proposal.get("page_number"),
            )

        def reviewed_candidate_changed(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
            return _candidate_materially_changed(
                left=left,
                right=right,
                page_number=proposal.get("page_number"),
            )

        quality_result = await run_artifact_quality_gate(
            source_candidate=existing_deck,
            initial_candidate=candidate_deck,
            review_context=review_context,
            revise_candidate=revise_from_review,
            normalize_candidate=normalize_reviewed_candidate,
            candidate_changed=reviewed_candidate_changed,
            reviewer=artifact_reviewer,
            max_review_rounds=max_review_rounds,
        )
        quality_summary = public_quality_summary(quality_result)
        proposal = dict(proposal)
        proposal["quality_gate"] = quality_summary
        action.proposal = proposal
        candidate_deck = dict(quality_result.get("candidate") or {})
        quality_audit = {
            "contract_version": 1,
            "original_candidate": quality_result.get("original_candidate"),
            "reviews": quality_result.get("reviews"),
            "final_candidate": candidate_deck,
            "revision_rounds": quality_result.get("revision_rounds", 0),
            "max_review_rounds": quality_result.get("max_review_rounds", 2),
            "materially_changed": quality_result.get("materially_changed", False),
        }
        if quality_result.get("status") != "passed":
            action.status = "blocked"
            action.reason = "这份候选还没有通过独立质量审阅，我先不把它交给你确认，原版本保持不变。"
            action.candidate_payload = {"quality_gate_audit": quality_audit}
            action.revision += 1
            await db.flush()
            return action

        candidate_public = dict(source_public)
        candidate_public.update({
            "kind": "presentation",
            "status": "proposed",
            "slides": list(candidate_deck.get("slides") or []),
            "slide_count": len(candidate_deck.get("slides") or []),
            "outline": [str(slide.get("title") or "") for slide in (candidate_deck.get("slides") or [])],
            "provider": candidate_deck.get("provider", source_public.get("provider", "unknown")),
        })
        candidate_private = dict(source_private)
        candidate_private.update(candidate_public)
        action.candidate_payload = {
            "title": str(candidate_deck.get("title") or source.title_snapshot)[:200],
            "chapter": source.chapter_snapshot,
            "content": json.dumps(candidate_deck, ensure_ascii=False),
            "public_data": candidate_public,
            "private_data": candidate_private,
            "quality_gate_audit": quality_audit,
        }
        action.status = "proposing"
        action.revision += 1
        await db.flush()
        return action
    except Exception:
        logger.exception(
            "artifact proposal generation failed action=%s artifact=%s source_version=%s",
            action.id,
            item.id,
            source.version_number,
        )
        action.status = "failed"
        action.reason = "这次分析没有完成，原版本保持不变，可以稍后重试。"
        action.revision += 1
        await db.flush()
        return action


async def apply_artifact_action_control(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    action_id: int,
    control: ArtifactActionControl,
    expected_revision: int,
) -> tuple[TideArtifactAction, TideWorkspaceObject]:
    action = await db.scalar(select(TideArtifactAction).where(
        TideArtifactAction.id == action_id,
        TideArtifactAction.user_id == user_id,
        TideArtifactAction.thread_id == thread_id,
    ))
    if action is None:
        raise ArtifactActionError("当前改进动作不存在或不可用。", code="ACTION_NOT_FOUND", status_code=404)
    if action.revision != expected_revision:
        raise ArtifactActionError("改进提案已发生变化，请刷新后再操作。", code="REVISION_CONFLICT", status_code=409)
    workspace_object = await get_current_workspace_object(
        db, user_id=user_id, thread_id=thread_id,
    )
    if workspace_object is None or workspace_object.id != action.workspace_object_id:
        raise ArtifactActionError("当前工作对象已改变，请重新分析。", code="WORKSPACE_OBJECT_CHANGED", status_code=409)

    if control == "cancel":
        if action.status != "proposing":
            raise ArtifactActionError("当前动作不能取消。", code="ACTION_STATE_INVALID", status_code=409)
        action.status = "cancelled"
        action.revision += 1
        await db.flush()
        return action, workspace_object
    if control != "confirm":
        raise ArtifactActionError("未知的操作。", code="UNKNOWN_ACTION")
    if action.status != "proposing" or not isinstance(action.candidate_payload, dict):
        raise ArtifactActionError("当前动作还不能创建新版本。", code="ACTION_STATE_INVALID", status_code=409)

    item = await get_owned_artifact(db, user_id=user_id, artifact_id=action.artifact_id)
    await ensure_initial_version(db, item)
    source = await get_version(db, item=item, version_id=action.source_version_id)
    reference = _workspace_artifact_reference(workspace_object)
    try:
        reference_artifact_id = int(reference.get("artifact_id") or 0)
        reference_version_id = int(reference.get("version_id") or 0)
    except (TypeError, ValueError):
        reference_artifact_id = 0
        reference_version_id = 0
    stale_source = (
        reference_artifact_id != action.artifact_id
        or reference_version_id != source.id
        or item.current_version_id != source.id
        or int(item.current_version_number or 0) != source.version_number
    )
    if stale_source:
        action.status = "conflicted"
        action.reason = "产物已经有了更新版本，这份旧提案不再适用。请基于当前版本重新提出改进。"
        action.candidate_payload = None
        action.revision += 1
        conflicted_proposal = dict(action.proposal or {})
        conflicted_proposal["requires_confirmation"] = False
        conflicted_proposal["conflict_detected"] = True
        conflicted_proposal["current_version_number"] = int(item.current_version_number or 0)
        action.proposal = conflicted_proposal
        await db.flush()
        return action, workspace_object
    payload = dict(action.candidate_payload)
    action.status = "creating"
    await db.flush()
    try:
        target, _replayed = await create_new_version(
            db,
            user_id=user_id,
            artifact_id=action.artifact_id,
            expected_version=source.version_number,
            idempotency_key=f"tide-artifact-action:{action.id}",
            title=payload.get("title"),
            chapter=payload.get("chapter"),
            content=payload.get("content"),
            public_data=payload.get("public_data"),
            private_data=payload.get("private_data"),
            status="completed",
        )
    except ArtifactVersionError as exc:
        action.status = "proposing"
        raise ArtifactActionError(str(exc), code=exc.code, status_code=exc.status_code) from exc

    refs = list(workspace_object.related_artifact_references or [])
    replacement = {
        "artifact_id": item.id,
        "version_id": target.id,
        "version_number": target.version_number,
        "kind": "presentation",
        "title": target.title_snapshot,
    }
    workspace_object.related_artifact_references = [replacement, *[
        ref for ref in refs
        if not _references_artifact(ref, item.id)
    ]][:8]
    workspace_object.revision += 1
    action.target_version_id = target.id
    action.status = "completed"
    action.revision += 1
    completed_proposal = dict(action.proposal or {})
    completed_proposal["target_version_number"] = target.version_number
    action.proposal = completed_proposal
    action.candidate_payload = None
    await db.flush()
    return action, workspace_object


def public_artifact_action(value: TideArtifactAction | None) -> dict[str, Any] | None:
    if value is None:
        return None
    proposal = dict(value.proposal or {})
    created_at = value.__dict__.get("created_at")
    updated_at = value.__dict__.get("updated_at")
    return {
        "id": value.id,
        "thread_id": value.thread_id,
        "workspace_object_id": value.workspace_object_id,
        "artifact_id": value.artifact_id,
        "source_version_id": value.source_version_id,
        "source_version_number": proposal.get("source_version_number"),
        "target_version_id": value.target_version_id,
        "action_type": value.action_type,
        "reason": value.reason,
        "status": value.status,
        "proposal": proposal,
        "revision": value.revision,
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def artifact_action_context(value: TideArtifactAction | None) -> dict[str, Any]:
    public = public_artifact_action(value)
    if not public:
        return {}
    return {key: public[key] for key in (
        "id", "artifact_id", "source_version_id", "source_version_number",
        "target_version_id", "action_type", "reason", "status", "proposal",
    )}


def build_artifact_action_directive(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    payload = json.dumps(context, ensure_ascii=False)
    if context.get("status") == "proposing":
        state_rule = "提案尚未执行。简洁说明判断和改进点，并明确等待用户确认；绝不能声称已经修改。"
    elif context.get("status") == "completed":
        state_rule = "新版本已经创建。说明结果并邀请用户继续一起检查，不要夸大改进效果。"
    elif context.get("status") == "conflicted":
        state_rule = "产物已前进到更新版本，旧提案已失效。直接说明需要基于当前版本重新判断，绝不能执行旧候选。"
    elif context.get("status") == "blocked":
        state_rule = "独立质量审阅没有放行候选。简洁说明关键风险和需要补的依据；绝不能邀请用户确认或声称已修改。"
    else:
        state_rule = "原版本保持不变。平静说明当前状态，不要假装动作成功。"
    return (
        "当前线程的 Artifact Action 如下：\n"
        f"{payload}\n{state_rule}\n"
        "保持 DARYA / Tide Partner 语气：简洁、沉着、有判断、温暖；先理解，再判断，再行动。"
    )
