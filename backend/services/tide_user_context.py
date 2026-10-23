"""Bounded document ingestion and confirmed user-context selection for DARYA."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ChatSession,
    TideUserContextClaim,
    WorkspaceConversationMaterial,
    WorkspaceMaterial,
    WorkspaceMaterialChunk,
)
from services.workspace_materials import (
    MaterialError,
    attach_workspace_material,
    create_workspace_material,
    validate_material_payload,
)


MAX_USER_CONTEXT_BYTES = 10 * 1024 * 1024
ALLOWED_USER_CONTEXT_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown"}
CLAIM_KINDS = {
    "education", "experience", "project", "skill", "achievement",
    "goal", "constraint", "preference", "other",
}
CLAIM_STATUSES = {"USER_PROVIDED", "INFERRED", "CONFIRMED", "REJECTED"}


class UserContextError(ValueError):
    pass


class UserContextRevisionConflict(UserContextError):
    pass


@dataclass(frozen=True)
class ExtractedClaimDraft:
    kind: str
    value: str
    status: str
    confidence: float
    source_reference: dict


class UserContextExtractor(Protocol):
    name: str

    def extract(
        self,
        material: WorkspaceMaterial,
        chunks: Sequence[WorkspaceMaterialChunk],
        extracted_at: str,
    ) -> list[ExtractedClaimDraft]: ...


_SENSITIVE = re.compile(
    r"politic|religio|ethnic|race|medical|diagnos|health condition|政治|宗教|民族|种族|病史|诊断|健康状况",
    re.I,
)
_CONTACT = re.compile(r"(?:\b\+?\d[\d\s-]{7,}\b)|(?:\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b)|(?:地址|address)\s*[:：]", re.I)
_TECH = re.compile(
    r"\b(?:React|TypeScript|JavaScript|FastAPI|Python|Java|C\+\+|PostgreSQL|MySQL|Redis|Docker|Git|Vue|Node(?:\.js)?)\b",
    re.I,
)


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip(" \t-•|：:")[:420]


def _kind_for(line: str, heading: str) -> str | None:
    text = f"{heading} {line}"
    # Achievement markers win before education: Chinese award names often
    # contain "大学生", which must not turn a competition result into a degree.
    if re.search(r"award|competition|prize|honou?r|achievement|获奖|奖项|竞赛|比赛|一等奖|二等奖|冠军", text, re.I):
        return "achievement"
    if re.search(r"education|university|college|bachelor|master|degree|教育|大学|学院|本科|硕士|博士|专业", text, re.I):
        return "education"
    if re.search(r"project|项目|作品|系统|platform", text, re.I):
        return "project"
    if re.search(r"experience|intern|employment|worked at|engineer|经历|实习|任职|工作经验|公司", text, re.I):
        return "experience"
    if re.search(r"skills?|technology|tech stack|技能|技术栈|熟悉|掌握", text, re.I) or len(_TECH.findall(line)) >= 2:
        return "skill"
    if re.search(r"objective|career goal|目标|方向", text, re.I):
        return "goal"
    if re.search(r"constraint|available only|限制|只能|时间约束", text, re.I):
        return "constraint"
    if re.search(r"prefer|preference|偏好|更喜欢", text, re.I):
        return "preference"
    if re.search(r"volunteer|community service|志愿|公益", text, re.I):
        return "other"
    return None


class RuleBasedUserContextExtractor:
    """Conservative V1 extractor; replaceable without changing claim storage."""

    name = "bounded-rule-v1"

    def extract(
        self,
        material: WorkspaceMaterial,
        chunks: Sequence[WorkspaceMaterialChunk],
        extracted_at: str,
    ) -> list[ExtractedClaimDraft]:
        drafts: list[ExtractedClaimDraft] = []
        seen: set[tuple[str, str]] = set()
        technology_sources: list[tuple[str, dict]] = []
        technologies: list[str] = []

        for chunk in chunks:
            heading = _clean_line(chunk.heading or "正文")
            cursor = 0
            for paragraph_index, raw_line in enumerate(str(chunk.content or "").splitlines()):
                line = _clean_line(raw_line)
                if len(line) < 4 or _SENSITIVE.search(line) or _CONTACT.search(line):
                    cursor += len(raw_line) + 1
                    continue
                kind = _kind_for(line, heading)
                techs = [match.group(0) for match in _TECH.finditer(line)]
                for tech in techs:
                    canonical = tech.strip()
                    if canonical.casefold() not in {item.casefold() for item in technologies}:
                        technologies.append(canonical)
                source_reference = {
                    "document_identity": material.material_id,
                    "document_name": material.file_name,
                    "document_title": Path(material.file_name).stem,
                    "chunk_index": chunk.chunk_index,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section": chunk.heading,
                    "paragraph_index": paragraph_index,
                    "span_start": cursor,
                    "span_end": cursor + len(raw_line),
                    "supporting_text": line,
                    "extracted_at": extracted_at,
                    "extractor": self.name,
                    "original_status": "USER_PROVIDED",
                }
                if techs:
                    technology_sources.append((line, source_reference))
                cursor += len(raw_line) + 1
                if not kind:
                    continue
                fingerprint = (kind, line.casefold())
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                drafts.append(ExtractedClaimDraft(
                    kind=kind,
                    value=line,
                    status="USER_PROVIDED",
                    confidence=1.0,
                    source_reference=source_reference,
                ))
                if len(drafts) >= 12:
                    break
            if len(drafts) >= 12:
                break

        # A single bounded professional inference is allowed only when both
        # frontend and backend evidence occur in the same uploaded document.
        technology_keys = {item.casefold() for item in technologies}
        has_frontend = bool(technology_keys & {"react", "typescript", "javascript", "vue"})
        has_backend = bool(technology_keys & {"fastapi", "python", "java", "node.js"})
        if has_frontend and has_backend and technology_sources:
            supporting_text, base_reference = technology_sources[0]
            reference = {
                **base_reference,
                "supporting_text": supporting_text,
                "observed_technologies": technologies[:8],
                "original_status": "INFERRED",
                "inference_rule": "frontend_and_backend_technology_evidence",
            }
            drafts.append(ExtractedClaimDraft(
                kind="experience",
                value="具备一定的前后端一体化项目经验",
                status="INFERRED",
                confidence=0.72,
                source_reference=reference,
            ))
        return drafts[:14]


def _fingerprint(kind: str, value: str) -> tuple[str, str]:
    return kind, re.sub(r"\W+", "", value.casefold())


async def _assert_thread(db: AsyncSession, user_id: int, thread_id: str) -> None:
    exists = await db.scalar(select(ChatSession.id).where(
        ChatSession.user_id == user_id,
        ChatSession.thread_id == thread_id,
    ))
    if exists is None:
        raise UserContextError("当前对话不存在或无权访问。")


async def ingest_user_context_document(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    file_name: str,
    content_type: str,
    payload: bytes,
    extractor: UserContextExtractor | None = None,
    storage_root: Path | None = None,
) -> dict:
    await _assert_thread(db, user_id, thread_id)
    clean_name = Path(str(file_name or "").replace("\x00", "")).name.strip()
    suffix = Path(clean_name).suffix.lower()
    if suffix not in ALLOWED_USER_CONTEXT_SUFFIXES:
        raise UserContextError("个人背景文档仅支持 PDF、DOCX、TXT 或 Markdown。")
    if len(payload) > MAX_USER_CONTEXT_BYTES:
        raise UserContextError("个人背景文档不能超过 10 MB。")
    validate_material_payload(payload, clean_name)
    digest = hashlib.sha256(payload).hexdigest()

    materials = list((await db.execute(select(WorkspaceMaterial).where(
        WorkspaceMaterial.user_id == user_id,
    ))).scalars().all())
    material = next((item for item in materials if (item.parse_metadata or {}).get("content_sha256") == digest), None)
    if material is None:
        material = await create_workspace_material(
            db,
            user_id=user_id,
            thread_id=thread_id,
            file_name=clean_name,
            content_type=content_type,
            payload=payload,
            storage_root=storage_root,
        )
        metadata = dict(material.parse_metadata or {})
        metadata.update({
            "content_sha256": digest,
            "user_context_ingestion": True,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        })
        material.parse_metadata = metadata
        await db.commit()
        await db.refresh(material)
    else:
        if material.parse_status != "ready":
            raise UserContextError("这份文档此前未能可靠解析，请检查文件后重试。")
        await attach_workspace_material(db, user_id, thread_id, material.material_id)

    existing = list((await db.execute(select(TideUserContextClaim).where(
        TideUserContextClaim.user_id == user_id,
        TideUserContextClaim.thread_id == thread_id,
        TideUserContextClaim.material_id == material.material_id,
    ))).scalars().all())
    if not existing:
        chunks = list((await db.execute(select(WorkspaceMaterialChunk).where(
            WorkspaceMaterialChunk.user_id == user_id,
            WorkspaceMaterialChunk.material_id == material.material_id,
        ).order_by(WorkspaceMaterialChunk.chunk_index))).scalars().all())
        extracted_at = datetime.now(timezone.utc).isoformat()
        drafts = (extractor or RuleBasedUserContextExtractor()).extract(material, chunks, extracted_at)
        rejected = list((await db.execute(select(TideUserContextClaim).where(
            TideUserContextClaim.user_id == user_id,
            TideUserContextClaim.thread_id == thread_id,
            TideUserContextClaim.status == "REJECTED",
        ))).scalars().all())
        rejected_fingerprints = {_fingerprint(item.kind, item.value) for item in rejected}
        for draft in drafts:
            if _fingerprint(draft.kind, draft.value) in rejected_fingerprints:
                continue
            db.add(TideUserContextClaim(
                user_id=user_id,
                thread_id=thread_id,
                material_id=material.material_id,
                kind=draft.kind,
                value=draft.value,
                status=draft.status,
                confidence=draft.confidence,
                source_reference=draft.source_reference,
                scope="thread",
            ))
        await db.commit()
    return await get_user_context_snapshot(db, user_id=user_id, thread_id=thread_id)


def public_claim(claim: TideUserContextClaim) -> dict:
    reference = dict(claim.source_reference or {})
    return {
        "id": claim.id,
        "kind": claim.kind,
        "value": claim.value,
        "status": claim.status,
        "origin_status": reference.get("original_status", claim.status),
        "confidence": claim.confidence,
        "source_reference": reference,
        "scope": claim.scope,
        "revision": claim.revision,
        "created_at": str(claim.created_at) if claim.created_at else None,
        "updated_at": str(claim.updated_at) if claim.updated_at else None,
    }


async def get_user_context_snapshot(db: AsyncSession, *, user_id: int, thread_id: str) -> dict:
    await _assert_thread(db, user_id, thread_id)
    claims = list((await db.execute(select(TideUserContextClaim).where(
        TideUserContextClaim.user_id == user_id,
        TideUserContextClaim.thread_id == thread_id,
    ).order_by(TideUserContextClaim.created_at, TideUserContextClaim.id))).scalars().all())
    bindings = list((await db.execute(
        select(WorkspaceMaterial)
        .join(WorkspaceConversationMaterial, WorkspaceConversationMaterial.material_id == WorkspaceMaterial.material_id)
        .where(
            WorkspaceConversationMaterial.user_id == user_id,
            WorkspaceConversationMaterial.thread_id == thread_id,
            WorkspaceConversationMaterial.is_active.is_(True),
            WorkspaceMaterial.user_id == user_id,
        )
        .order_by(WorkspaceConversationMaterial.attached_at.desc())
    )).scalars().all())
    documents = []
    for item in bindings:
        metadata = dict(item.parse_metadata or {})
        if not metadata.get("user_context_ingestion"):
            continue
        documents.append({
            "material_id": item.material_id,
            "file_name": item.file_name,
            "title": Path(item.file_name).stem,
            "file_type": item.file_type,
            "size_bytes": item.size_bytes,
            "parse_status": item.parse_status,
            "page_count": item.page_count,
            "section_count": item.section_count,
            "ingested_at": metadata.get("ingested_at"),
        })
    active = [item for item in claims if item.status != "REJECTED"]
    return {
        "thread_id": thread_id,
        "scope": "thread",
        "memory_boundary": "NOT_LONG_TERM_MEMORY",
        "documents": documents,
        "claims": [public_claim(item) for item in claims],
        "summary": [item.value for item in active[:5]],
        "confirmed_count": sum(item.status == "CONFIRMED" for item in claims),
        "rejected_count": sum(item.status == "REJECTED" for item in claims),
    }


async def apply_user_context_control(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    claim_id: int,
    action: str,
    expected_revision: int,
    value: str | None = None,
) -> dict:
    claim = (await db.execute(select(TideUserContextClaim).where(
        TideUserContextClaim.id == claim_id,
        TideUserContextClaim.user_id == user_id,
        TideUserContextClaim.thread_id == thread_id,
    ))).scalar_one_or_none()
    if claim is None:
        raise UserContextError("这条理解不存在或无权访问。")
    if claim.revision != expected_revision:
        raise UserContextRevisionConflict("这条理解已经更新，请刷新后再操作。")
    if action == "confirm":
        claim.status = "CONFIRMED"
    elif action == "correct":
        corrected = _clean_line(value or "")
        if len(corrected) < 2:
            raise UserContextError("修正内容不能为空。")
        reference = dict(claim.source_reference or {})
        reference["user_correction"] = {
            "previous_value": claim.value,
            "corrected_at": datetime.now(timezone.utc).isoformat(),
        }
        claim.value = corrected
        claim.status = "CONFIRMED"
        claim.confidence = 1.0
        claim.source_reference = reference
    elif action == "reject":
        claim.status = "REJECTED"
    else:
        raise UserContextError("不支持的用户控制操作。")
    claim.revision += 1
    await db.commit()
    await db.refresh(claim)
    return await get_user_context_snapshot(db, user_id=user_id, thread_id=thread_id)


_TECHNICAL_QUERY = re.compile(r"技术|方向|项目|工程|开发|前端|后端|全栈|技能|求职|岗位|technical|career|project|skill|frontend|backend", re.I)
_EDUCATION_QUERY = re.compile(r"学校|大学|专业|学历|教育|school|university|education|degree", re.I)
_ACHIEVEMENT_QUERY = re.compile(r"比赛|竞赛|奖|成果|achievement|award|competition", re.I)
_GOAL_QUERY = re.compile(r"目标|计划|下一步|选择|goal|plan|next", re.I)


def _relevant_kind_weights(message: str) -> dict[str, int]:
    weights: dict[str, int] = {}
    if _TECHNICAL_QUERY.search(message):
        weights.update({"project": 8, "skill": 8, "experience": 6, "achievement": 3, "education": 2})
    if _EDUCATION_QUERY.search(message):
        weights["education"] = max(weights.get("education", 0), 8)
    if _ACHIEVEMENT_QUERY.search(message):
        weights["achievement"] = max(weights.get("achievement", 0), 8)
    if _GOAL_QUERY.search(message):
        weights.update({"goal": 8, "constraint": 6, "preference": 4})
    return weights


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]{2,}|[\u4e00-\u9fff]{2,}", value.casefold()))


async def select_relevant_confirmed_context(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    message: str,
    limit: int = 4,
) -> list[TideUserContextClaim]:
    claims = list((await db.execute(select(TideUserContextClaim).where(
        TideUserContextClaim.user_id == user_id,
        TideUserContextClaim.thread_id == thread_id,
        TideUserContextClaim.status == "CONFIRMED",
    ))).scalars().all())
    if not claims:
        return []
    weights = _relevant_kind_weights(message)
    query_terms = _terms(message)

    def score(claim: TideUserContextClaim) -> int:
        overlap = len(query_terms & _terms(claim.value))
        return weights.get(claim.kind, 0) + overlap * 2

    ranked = sorted(claims, key=lambda item: (-score(item), item.id))
    selected = [item for item in ranked if score(item) > 0]
    if not selected:
        selected = [item for item in ranked if item.kind in {"goal", "constraint", "preference"}]
    return selected[:limit]


def build_user_context_directive(claims: Sequence[TideUserContextClaim]) -> str:
    if not claims:
        return ""
    rows = [{"kind": item.kind, "value": item.value} for item in claims]
    import json
    return (
        "以下是用户在当前对话范围内亲自确认、且与本轮问题相关的背景：\n"
        + json.dumps(rows, ensure_ascii=False)
        + "\n这些是 USER_CONFIRMED_CONTEXT，不是外部 VERIFIED 事实，也不是长期 Memory。"
        "直接利用其中已有信息给出具体判断，不要让用户重复学校、项目或技能。"
        "不要注入或复述未列出的文档内容，也不要用‘根据你的简历’代替真正个性化的建议。"
    )
