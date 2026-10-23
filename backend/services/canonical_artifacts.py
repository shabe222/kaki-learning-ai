"""Canonical Artifact V1 adapters, review authority, and semantic lineage."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ArtifactLineageEdge,
    ArtifactReview,
    GeneratedItem,
    GeneratedItemVersion,
)
from schemas.canonical_artifacts import (
    ArtifactBlockV1,
    ArtifactLineageV1,
    ArtifactReviewV1,
    ArtifactStatusV1,
    CanonicalArtifactV1,
    CreateArtifactReviewRequest,
    CreateCanonicalHandoutRequest,
    CreatePracticeTransformRequest,
    CreateReviewedRevisionRequest,
)

TRUSTED_REVIEW_AUTHORITY = "TRUSTED_REVIEWER"
OWNER_REVIEW_AUTHORITY = "OWNER_REQUEST"
TRUSTED_REVIEWER_ROLES = {"PLATFORM_REVIEWER", "INDEPENDENT_REVIEWER"}
from services.workspace_artifact_versions import (
    ArtifactOperationConflict,
    ArtifactVersionConflict,
    ArtifactVersionNotFound,
    _version_content_digest,
    create_new_version,
    get_owned_artifact,
    get_version,
    _safe_value,
)


class CanonicalArtifactError(ValueError):
    code = "CANONICAL_ARTIFACT_ERROR"
    status_code = 409


class CanonicalArtifactAccessError(CanonicalArtifactError):
    code = "CANONICAL_ARTIFACT_NOT_FOUND"
    status_code = 404


class CanonicalArtifactValidationError(CanonicalArtifactError):
    code = "CANONICAL_ARTIFACT_INVALID"
    status_code = 422


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: str) -> str:
    return str(value or "").removeprefix("sha256:").lower()


def _iso(value: Any) -> str:
    return value.isoformat() if value else ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_status(generation_status: str | None, review_verdict: str | None = None) -> str:
    """Map historical generation strings without treating them as formal review truth."""
    verdict = str(review_verdict or "").upper()
    if verdict == "PASS":
        return "PASSED"
    if verdict == "REVISE":
        return "REVISION_REQUIRED"
    if verdict in {"REJECT", "BLOCKED"}:
        return "BLOCKED"
    raw = str(generation_status or "generated").strip().lower()
    if raw in {"planned", "queued", "pending", "draft"}:
        return "PLANNED"
    if raw in {
        "generating", "running", "claimed", "generating_script", "generating_audio",
        "generating_avatar", "composing",
    }:
        return "GENERATING"
    if raw in {"in_review", "reviewing"}:
        return "IN_REVIEW"
    if raw in {"revision_required", "revise"}:
        return "REVISION_REQUIRED"
    if raw in {"failed", "rejected", "error"}:
        return "FAILED"
    if raw == "blocked":
        return "BLOCKED"
    # Historical passed/quality_status=passed is generation compatibility only.
    return "GENERATED"


def _block_id(block_type: str, ordinal: int, visible: str) -> str:
    return f"blk_{ordinal:03d}_{_sha(block_type + chr(0) + visible)[:14]}"


def _block(
    block_type: str, ordinal: int, text: str, *,
    knowledge_refs: Iterable[str] = (), source_refs: Iterable[str] = (),
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    visible = str(text or "").strip()
    payload = {"text": visible}
    if data:
        payload.update(dict(data))
    return ArtifactBlockV1(
        block_id=_block_id(block_type, ordinal, visible),
        type=block_type,
        data=payload,
        knowledge_refs=list(knowledge_refs),
        source_refs=list(source_refs),
        accessibility={"text_equivalent": visible[:4000]},
    ).dict()


def _document_blocks(content: str, source_refs: list[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        text = "\n".join(paragraph).strip()
        paragraph.clear()
        if not text:
            return
        lowered = text.lower()
        kind = "EXAMPLE" if lowered.startswith(("例", "example")) else "TEXT"
        blocks.append(_block(kind, len(blocks) + 1, text, source_refs=source_refs))

    for raw_line in str(content or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush()
            blocks.append(_block(
                "HEADING", len(blocks) + 1, heading.group(2).strip(), source_refs=source_refs,
                data={"level": len(heading.group(1))},
            ))
        elif line.lstrip().startswith(">"):
            flush()
            blocks.append(_block(
                "CALLOUT", len(blocks) + 1, line.lstrip()[1:].strip(), source_refs=source_refs,
            ))
        elif re.match(r"^(来源|参考|source)\s*[:：]", line.strip(), re.I):
            flush()
            blocks.append(_block("SOURCE", len(blocks) + 1, line.strip(), source_refs=source_refs))
        else:
            paragraph.append(line)
    flush()
    if not blocks:
        blocks.append(_block("TEXT", 1, "内容暂不可用。", source_refs=source_refs))
    return blocks


def adapt_blocks(item: GeneratedItem, version: GeneratedItemVersion) -> list[dict[str, Any]]:
    data = dict(version.public_data_snapshot or {})
    supplied = data.get("canonical_blocks") or data.get("blocks")
    if isinstance(supplied, list) and supplied:
        return [ArtifactBlockV1.parse_obj(value).dict() for value in supplied]
    refs = [str(value) for value in list(data.get("source_refs") or []) if isinstance(value, str)]
    kind = str(data.get("kind") or item.type or "").replace("workspace_", "").lower()
    if kind in {"lecture", "document"} or item.type == "personalized_handout":
        return _document_blocks(version.content_snapshot or "", refs)
    if kind == "code":
        return [_block(
            "RUNNABLE_CODE", 1, version.content_snapshot or "", source_refs=refs,
            data={"language": str(data.get("language") or "python"), "code": version.content_snapshot or ""},
        )]
    if kind in {"question", "interactive_lesson"}:
        questions = list(data.get("questions") or [])
        result = []
        for question in questions[:200]:
            text = str(question.get("content") or question.get("question") or question.get("title") or "问题") if isinstance(question, Mapping) else str(question)
            result.append(_block("QUESTION", len(result) + 1, text, source_refs=refs, data=dict(question) if isinstance(question, Mapping) else {}))
        return result or [_block("QUESTION", 1, version.content_snapshot or "练习内容暂不可用。", source_refs=refs)]
    if kind == "mindmap":
        return [_block(
            "DIAGRAM", 1, str(data.get("summary") or version.title_snapshot), source_refs=refs,
            data={"tree": data.get("tree_data") or data.get("mindmap") or data.get("tree"), "text_fallback": version.content_snapshot or version.title_snapshot},
        )]
    if kind == "presentation":
        result = []
        for slide in list(data.get("slides") or [])[:200]:
            if not isinstance(slide, Mapping):
                continue
            title = str(slide.get("title") or f"第 {len(result) + 1} 页")
            result.append(_block("HEADING", len(result) + 1, title, source_refs=refs, data={"level": 2}))
            body = str(slide.get("body") or "\n".join(map(str, slide.get("items") or []))).strip()
            if body:
                result.append(_block("TEXT", len(result) + 1, body, source_refs=refs))
        return result or [_block("TEXT", 1, version.content_snapshot or version.title_snapshot, source_refs=refs)]
    if kind == "video":
        return [_block(
            "VIDEO", 1, str(data.get("summary") or version.title_snapshot), source_refs=refs,
            data={
                "video_url": data.get("video_url"),
                "transcript": data.get("transcript"),
                "duration_sec": data.get("duration_sec"),
            },
        )]
    return [_block("TEXT", 1, version.content_snapshot or version.title_snapshot, source_refs=refs)]


def _public_review(row: ArtifactReview | None) -> dict[str, Any]:
    if row is None:
        return {"latest_verdict": None, "review_ref": None}
    return {
        "latest_verdict": row.verdict,
        "review_ref": f"artifact-review:{row.review_id}",
        "round": row.round,
        "safe_summary": row.safe_summary,
        "scores": dict(row.scores or {}),
        "issues": list(row.issues_json or []),
        "preserve": list(row.preserve_json or []),
        "reviewer_role": row.reviewer_role,
        "authority": "FORMAL" if row.authority_kind == TRUSTED_REVIEW_AUTHORITY else "OWNER_REQUEST",
        "provider": row.provider,
        "model": row.model,
    }


def _public_lineage(row: ArtifactLineageEdge, source_available: bool) -> dict[str, Any]:
    source = {
        "availability": "AVAILABLE" if source_available else "TOMBSTONED",
        "artifact_id": row.source_artifact_id if source_available else None,
        "version_id": row.source_version_id if source_available else None,
        "version_digest": f"sha256:{row.source_version_digest}",
        "block_refs": list(row.source_block_refs or []),
    }
    return {
        "contract": "darya.artifact-lineage.v1",
        "lineage_edge_id": row.lineage_edge_id,
        "source": source,
        "target_artifact_id": row.target_artifact_id,
        "target_version_id": row.target_version_id,
        "transform_type": row.transform_type,
        "transform_contract_version": row.transform_contract_version,
        "knowledge_refs": list(row.knowledge_refs or []),
        "source_refs": list(row.source_refs or []),
        "learner_context_ref": row.learner_context_ref,
        "safe_operation_summary": row.safe_operation_summary,
        "generator_provenance": dict(row.generator_provenance or {}),
        "created_at": _iso(row.created_at),
    }


async def build_canonical_artifact(
    db: AsyncSession, *, item: GeneratedItem, version: GeneratedItemVersion,
) -> dict[str, Any]:
    review = await db.scalar(select(ArtifactReview).where(
        ArtifactReview.user_id == item.user_id,
        ArtifactReview.artifact_id == item.id,
        ArtifactReview.artifact_version_id == version.id,
        ArtifactReview.version_digest == version.content_digest,
        ArtifactReview.authority_kind == TRUSTED_REVIEW_AUTHORITY,
    ).order_by(desc(ArtifactReview.round), desc(ArtifactReview.created_at)))
    edges = list((await db.execute(select(ArtifactLineageEdge).where(
        ArtifactLineageEdge.user_id == item.user_id,
        ArtifactLineageEdge.target_artifact_id == item.id,
        ArtifactLineageEdge.target_version_id == version.id,
    ).order_by(ArtifactLineageEdge.created_at))).scalars())
    source_ids = {edge.source_artifact_id for edge in edges}
    available_ids = set()
    if source_ids:
        available_ids = set((await db.execute(select(GeneratedItem.id).where(
            GeneratedItem.user_id == item.user_id,
            GeneratedItem.id.in_(source_ids),
        ))).scalars())
    data = dict(version.public_data_snapshot or {})
    # 刚生成的资料，canonical_* 是写在 item.raw_data 上的，不在版本快照里。
    #
    # prepare_conversation_capability 生成讲义/实操指南后，把 canonical_blocks /
    # canonical_purpose / canonical_source_refs 写进 item.raw_data 就提交了，没有
    # 再开一个版本；而这里只读 version.public_data_snapshot。结果是 2026-09-23 实测的
    # 那个现象：第一次生成的资料 source_refs 为空 → 检查报 REQUIRED_FIELDS_MISSING →
    # 用户看到「暂时未完成、打不开」，必须自己点一次「重试准备」拿到第二版才可读。
    # 库里 12 个最近产物里，凡是新生成的都是 refs=0（FAIL），凡是由重试/修订产生的
    # 都是 refs=1（PASS）——第一次交付永远是坏的。
    #
    # 只在「这个版本就是当前版本」时回退读 item，历史版本仍只认自己的快照，
    # 这样既修好首次交付，也不让历史被后来的元数据改写。
    if version.id == item.current_version_id:
        item_data = item.raw_data if isinstance(item.raw_data, Mapping) else {}
        for key in ("canonical_blocks", "canonical_purpose", "canonical_personalization", "canonical_source_refs"):
            if not data.get(key) and item_data.get(key):
                data[key] = item_data[key]
    blocks = adapt_blocks(item, version)
    purpose = dict(data.get("canonical_purpose") or data.get("purpose") or {})
    purpose.setdefault("learning_goal", str(data.get("learning_goal") or version.chapter_snapshot or version.title_snapshot))
    purpose.setdefault("knowledge_refs", sorted({ref for block in blocks for ref in block.get("knowledge_refs", [])}))
    purpose.setdefault("success_criteria", [])
    purpose.setdefault("experience", "PERSONALIZED_HANDOUT" if item.type in {"personalized_handout", "workspace_lecture"} else item.type.upper())
    generation_status = normalize_status(version.status)
    canonical_status = normalize_status(version.status, review.verdict if review else None)
    envelope = CanonicalArtifactV1(
        artifact_id=item.id,
        artifact_type=str(purpose.get("experience") or item.type).upper(),
        version={
            "version_id": version.id,
            "number": version.version_number,
            "parent_version_id": version.parent_version_id,
            "status": canonical_status,
            "content_digest": f"sha256:{version.content_digest}",
            "created_by_operation": version.created_by_operation,
            "created_at": _iso(version.created_at),
        },
        purpose=purpose,
        personalization=dict(data.get("canonical_personalization") or data.get("personalization") or {}),
        blocks=blocks,
        source_refs=list(data.get("canonical_source_refs") or data.get("source_citations") or []),
        lineage=[_public_lineage(edge, edge.source_artifact_id in available_ids) for edge in edges],
        review=_public_review(review),
        status=ArtifactStatusV1(
            generation_status=generation_status,
            review_verdict=review.verdict if review else None,
            learning_evaluation=None,
            canonical_status=canonical_status,
        ),
        capabilities={
            "views": ["READ", "QUICK_REVIEW", "SOURCE"],
            "transforms": ["HANDOUT_TO_PRACTICE"] if str(purpose.get("experience")) == "PERSONALIZED_HANDOUT" else [],
            "exports": list(data.get("export_formats") or []),
        },
        accessibility=dict(data.get("accessibility") or {"reading_order": [block["block_id"] for block in blocks]}),
    )
    return envelope.dict()


async def create_canonical_handout(
    db: AsyncSession, *, user_id: int, request: CreateCanonicalHandoutRequest,
) -> tuple[GeneratedItem, GeneratedItemVersion, bool]:
    request_body = _safe_value(request.dict())
    operation = _sha(f"canonical-artifact-create-v1\0{user_id}\0{request.idempotency_key}")
    guard = _sha(_canonical(request_body))
    existing = await db.scalar(select(GeneratedItemVersion).where(
        GeneratedItemVersion.created_by_operation == operation,
    ))
    if existing is not None:
        if existing.operation_guard != guard:
            raise ArtifactOperationConflict("相同幂等身份对应了不同产物内容")
        item = await get_owned_artifact(db, user_id=user_id, artifact_id=existing.artifact_id)
        return item, existing, True
    supplied_blocks = [value.dict() for value in request.blocks] if request.blocks else _document_blocks(request.content or "", [])
    blocks = [ArtifactBlockV1.parse_obj(_safe_value(value)).dict() for value in supplied_blocks]
    content = str(_safe_value(request.content or "\n\n".join(str(block["data"].get("text") or "") for block in blocks)) or "")
    safe_purpose = dict(_safe_value(dict(request.purpose or {})) or {})
    safe_personalization = dict(_safe_value(dict(request.personalization or {})) or {})
    safe_source_refs = list(_safe_value(list(request.source_refs or [])) or [])
    safe_quality = dict(_safe_value(dict(request.content_quality or {})) or {})
    public_data = {
        "kind": "document",
        "status": "generated",
        "thread_id": request.thread_id,
        "canonical_blocks": blocks,
        "canonical_purpose": {
            "learning_goal": request.topic or request.title,
            "success_criteria": [],
            "knowledge_refs": sorted({ref for block in blocks for ref in block.get("knowledge_refs", [])}),
            "experience": request.experience,
            **safe_purpose,
        },
        "canonical_personalization": safe_personalization,
        "canonical_source_refs": safe_source_refs,
        "export_formats": ["pdf", "docx"],
    }
    item = GeneratedItem(
        user_id=user_id,
        type="personalized_handout" if request.experience == "PERSONALIZED_HANDOUT" else "practical_guide",
        title=request.title.strip(),
        chapter=(request.topic or "")[:120],
        content=content,
        raw_data=dict(public_data),
    )
    db.add(item)
    await db.flush()
    version = GeneratedItemVersion(
        artifact_id=item.id,
        version_number=1,
        parent_version_id=None,
        title_snapshot=item.title,
        chapter_snapshot=item.chapter,
        content_snapshot=content,
        public_data_snapshot=public_data,
        private_data_snapshot={
            "contract": "darya.artifact.v1",
            **({"content_quality": safe_quality} if safe_quality else {}),
        },
        content_digest=_version_content_digest(item.title, item.chapter, content, public_data),
        status="generated",
        created_by_operation=operation,
        operation_guard=guard,
    )
    db.add(version)
    await db.flush()
    item.current_version_id = version.id
    item.current_version_number = 1
    item.raw_data = {**public_data, "version": 1, "version_id": version.id, "current_version_id": version.id}
    item.updated_at = _utcnow()
    await db.flush()
    return item, version, False


async def create_review(
    db: AsyncSession, *, user_id: int, artifact_id: int, request: CreateArtifactReviewRequest,
    authority_kind: str = OWNER_REVIEW_AUTHORITY,
    trusted_reviewer_role: str | None = None,
    trusted_provider: str | None = None,
    trusted_model: str | None = None,
) -> tuple[ArtifactReview, bool]:
    item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    version = await get_version(db, item=item, version_id=request.artifact_version_id)
    if _digest(request.version_digest) != version.content_digest:
        raise ArtifactVersionConflict("审阅目标摘要已变化，请重新读取精确版本")
    is_trusted = authority_kind == TRUSTED_REVIEW_AUTHORITY
    if not is_trusted and request.verdict == "PASS":
        raise CanonicalArtifactValidationError("产物所有者不能签发 PASS；正式通过只能由受信审阅服务写入")
    role = (trusted_reviewer_role or "").strip().upper() if is_trusted else "OWNER_REQUEST"
    if is_trusted and role not in TRUSTED_REVIEWER_ROLES:
        raise CanonicalArtifactValidationError("受信审阅角色无效")
    blocks = {block["block_id"] for block in adapt_blocks(item, version)}
    issue_refs = {issue.block_id for issue in request.issues}
    preserve_refs = set(request.preserve)
    if not issue_refs.issubset(blocks) or not preserve_refs.issubset(blocks):
        raise CanonicalArtifactValidationError("审阅 issue/preserve 必须绑定当前版本的真实 block")
    validated = ArtifactReviewV1(
        artifact_id=artifact_id,
        artifact_version_id=version.id,
        version_digest=version.content_digest,
        review_contract_version=request.review_contract_version,
        round=request.round,
        reviewer_role=role,
        provider=trusted_provider if is_trusted else None,
        model=trusted_model if is_trusted else None,
        verdict=request.verdict,
        scores=request.scores,
        safe_summary=request.safe_summary,
        issues=request.issues,
        preserve=request.preserve,
    )
    operation = _sha(f"artifact-review-v1\0{authority_kind}\0{user_id}\0{request.idempotency_key}")
    guard = _sha(_canonical(validated.dict()))
    existing = await db.scalar(select(ArtifactReview).where(
        ArtifactReview.user_id == user_id,
        ArtifactReview.operation_key == operation,
    ))
    if existing is not None:
        if existing.operation_guard != guard:
            raise ArtifactOperationConflict("相同审阅幂等身份对应了不同事实")
        return existing, True
    same_round = await db.scalar(select(ArtifactReview).where(
        ArtifactReview.artifact_version_id == version.id,
        ArtifactReview.round == request.round,
        ArtifactReview.reviewer_role == role,
    ))
    if same_round is not None:
        raise ArtifactOperationConflict("该版本、轮次和审阅角色已经产生正式审阅事实")
    row = ArtifactReview(
        review_id="arv_" + operation[:32],
        user_id=user_id,
        artifact_id=artifact_id,
        artifact_version_id=version.id,
        version_digest=version.content_digest,
        review_contract_version=request.review_contract_version,
        round=request.round,
        reviewer_role=role,
        authority_kind=authority_kind,
        provider=trusted_provider if is_trusted else None,
        model=trusted_model if is_trusted else None,
        verdict=request.verdict,
        scores=dict(request.scores or {}),
        safe_summary=request.safe_summary,
        issues_json=[issue.dict() for issue in request.issues],
        preserve_json=list(request.preserve),
        operation_key=operation,
        operation_guard=guard,
    )
    db.add(row)
    await db.flush()
    return row, False


async def create_trusted_review(
    db: AsyncSession, *, user_id: int, artifact_id: int, request: CreateArtifactReviewRequest,
    reviewer_role: str = "PLATFORM_REVIEWER", provider: str = "darya-review-service",
    model: str | None = None,
) -> tuple[ArtifactReview, bool]:
    """Server-internal review entrypoint; not reachable from the learner API."""
    return await create_review(
        db, user_id=user_id, artifact_id=artifact_id, request=request,
        authority_kind=TRUSTED_REVIEW_AUTHORITY,
        trusted_reviewer_role=reviewer_role,
        trusted_provider=provider,
        trusted_model=model,
    )


async def create_reviewed_revision(
    db: AsyncSession, *, user_id: int, artifact_id: int, request: CreateReviewedRevisionRequest,
) -> tuple[GeneratedItemVersion, bool]:
    item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    source = await get_version(db, item=item, version_id=request.source_version_id)
    if source.version_number != request.expected_version or _digest(request.source_version_digest) != source.content_digest:
        raise ArtifactVersionConflict("修订基线已变化")
    review = await db.scalar(select(ArtifactReview).where(
        ArtifactReview.user_id == user_id,
        ArtifactReview.artifact_id == artifact_id,
        ArtifactReview.artifact_version_id == source.id,
        ArtifactReview.version_digest == source.content_digest,
        ArtifactReview.authority_kind == TRUSTED_REVIEW_AUTHORITY,
    ).order_by(desc(ArtifactReview.round), desc(ArtifactReview.created_at)))
    if review is None or review.verdict != "REVISE":
        raise CanonicalArtifactValidationError("精确源版本没有可执行的 REVISE 审阅")
    old_blocks = {block["block_id"]: block for block in adapt_blocks(item, source)}
    new_blocks = {block.block_id: block.dict() for block in request.blocks}
    for block_id in list(review.preserve_json or []):
        if block_id not in new_blocks or new_blocks[block_id] != old_blocks.get(block_id):
            raise CanonicalArtifactValidationError(f"必须原样保留审阅指定 block: {block_id}")
    data = dict(source.public_data_snapshot or {})
    data.update({"canonical_blocks": list(new_blocks.values()), "status": "generated"})
    content = request.content
    if content is None:
        content = "\n\n".join(str(block.data.get("text") or "") for block in request.blocks)
    return await create_new_version(
        db,
        user_id=user_id,
        artifact_id=artifact_id,
        expected_version=request.expected_version,
        idempotency_key=request.idempotency_key,
        title=request.title,
        content=content,
        public_data=data,
        status="generated",
    )


async def create_practice_transform(
    db: AsyncSession, *, user_id: int, source_artifact_id: int,
    request: CreatePracticeTransformRequest,
) -> tuple[GeneratedItem, GeneratedItemVersion, ArtifactLineageEdge, bool]:
    operation = _sha(f"artifact-transform-v1\0{user_id}\0{request.idempotency_key}")
    validated_guard = _sha(_canonical(request.dict()))
    existing = await db.scalar(select(ArtifactLineageEdge).where(
        ArtifactLineageEdge.user_id == user_id,
        ArtifactLineageEdge.operation_key == operation,
    ))
    if existing is not None:
        if existing.operation_guard != validated_guard:
            raise ArtifactOperationConflict("相同 transform 幂等身份对应了不同内容")
        target = await get_owned_artifact(db, user_id=user_id, artifact_id=existing.target_artifact_id)
        version = await get_version(db, item=target, version_id=existing.target_version_id)
        return target, version, existing, True
    source_item = await get_owned_artifact(db, user_id=user_id, artifact_id=source_artifact_id)
    source_version = await get_version(db, item=source_item, version_id=request.source_version_id)
    if _digest(request.source_version_digest) != source_version.content_digest:
        raise ArtifactVersionConflict("transform 源版本摘要已变化")
    source_blocks = {block["block_id"] for block in adapt_blocks(source_item, source_version)}
    if not set(request.source_block_refs).issubset(source_blocks):
        raise CanonicalArtifactValidationError("transform source_block_refs 不属于精确源版本")
    ArtifactLineageV1(
        source_artifact_id=source_item.id,
        source_version_id=source_version.id,
        source_version_digest=source_version.content_digest,
        source_block_refs=request.source_block_refs,
        target_artifact_id=1,
        target_version_id=1,
        transform_type="HANDOUT_TO_PRACTICE",
        knowledge_refs=request.knowledge_refs,
        source_refs=request.source_refs,
        learner_context_ref=request.learner_context_ref,
        safe_operation_summary=request.safe_operation_summary,
        generator_provenance=request.generator_provenance,
    )
    blocks = [block.dict() for block in request.blocks]
    content = "\n\n".join(str(block["data"].get("text") or "") for block in blocks)
    public_data = {
        "kind": "question",
        "status": "generated",
        "canonical_blocks": blocks,
        "canonical_purpose": {
            "learning_goal": source_version.chapter_snapshot or source_version.title_snapshot,
            "success_criteria": [],
            "knowledge_refs": list(request.knowledge_refs),
            "experience": "PRACTICE",
        },
        "canonical_source_refs": list(request.source_refs),
    }
    target = GeneratedItem(
        user_id=user_id,
        type="personalized_practice",
        title=request.title,
        chapter=source_version.chapter_snapshot,
        content=content,
        raw_data=dict(public_data),
    )
    db.add(target)
    await db.flush()
    create_operation = _sha(f"artifact-transform-target-v1\0{operation}\0{target.id}")
    target_version = GeneratedItemVersion(
        artifact_id=target.id,
        version_number=1,
        title_snapshot=target.title,
        chapter_snapshot=target.chapter,
        content_snapshot=content,
        public_data_snapshot=public_data,
        private_data_snapshot={"contract": "darya.artifact.v1"},
        content_digest=_version_content_digest(target.title, target.chapter, content, public_data),
        status="generated",
        created_by_operation=create_operation,
        operation_guard=validated_guard,
    )
    db.add(target_version)
    await db.flush()
    target.current_version_id = target_version.id
    target.current_version_number = 1
    target.raw_data = {**public_data, "version": 1, "version_id": target_version.id, "current_version_id": target_version.id}
    edge = ArtifactLineageEdge(
        lineage_edge_id="ale_" + operation[:32],
        user_id=user_id,
        source_artifact_id=source_item.id,
        source_version_id=source_version.id,
        source_version_digest=source_version.content_digest,
        source_block_refs=list(request.source_block_refs),
        target_artifact_id=target.id,
        target_version_id=target_version.id,
        transform_type="HANDOUT_TO_PRACTICE",
        transform_contract_version="darya.artifact-transform.v1",
        knowledge_refs=list(request.knowledge_refs),
        source_refs=list(request.source_refs),
        learner_context_ref=request.learner_context_ref,
        safe_operation_summary=request.safe_operation_summary,
        generator_provenance=dict(request.generator_provenance or {}),
        operation_key=operation,
        operation_guard=validated_guard,
    )
    db.add(edge)
    await db.flush()
    return target, target_version, edge, False


async def list_reviews(db: AsyncSession, *, user_id: int, artifact_id: int) -> list[dict[str, Any]]:
    await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    rows = list((await db.execute(select(ArtifactReview).where(
        ArtifactReview.user_id == user_id,
        ArtifactReview.artifact_id == artifact_id,
    ).order_by(ArtifactReview.created_at))).scalars())
    return [{
        "review_id": row.review_id,
        "artifact_version_id": row.artifact_version_id,
        "version_digest": f"sha256:{row.version_digest}",
        "round": row.round,
        "reviewer_role": row.reviewer_role,
        "authority": "FORMAL" if row.authority_kind == TRUSTED_REVIEW_AUTHORITY else "OWNER_REQUEST",
        "provider": row.provider,
        "model": row.model,
        "verdict": row.verdict,
        "scores": dict(row.scores or {}),
        "safe_summary": row.safe_summary,
        "issues": list(row.issues_json or []),
        "preserve": list(row.preserve_json or []),
        "created_at": _iso(row.created_at),
    } for row in rows]


async def list_lineage(db: AsyncSession, *, user_id: int, artifact_id: int) -> list[dict[str, Any]]:
    await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    rows = list((await db.execute(select(ArtifactLineageEdge).where(
        ArtifactLineageEdge.user_id == user_id,
        ArtifactLineageEdge.target_artifact_id == artifact_id,
    ).order_by(ArtifactLineageEdge.created_at))).scalars())
    source_ids = {row.source_artifact_id for row in rows}
    available = set()
    if source_ids:
        available = set((await db.execute(select(GeneratedItem.id).where(
            GeneratedItem.user_id == user_id,
            GeneratedItem.id.in_(source_ids),
        ))).scalars())
    return [_public_lineage(row, row.source_artifact_id in available) for row in rows]
