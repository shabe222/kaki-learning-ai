"""Picture book artifacts — the server side of an otherwise browser-only loop.

The 教学绘本 itself is produced in the browser by the storybook web component
(`frontend/public/agent/*`), which owns the storyboard, the illustration calls
and the style-consistency pass. That component has no persistence of its own,
so a finished book used to vanish the moment its overlay closed.

This module accepts that component's own payload, validates it against a
bounded mechanical contract, and stores it through the existing
`GeneratedItem` / `GeneratedItemVersion` authority. It adds no second artifact
store and no second reviewer; like the other study tools, the book carries its
own generation-time contract instead of the canonical teaching-artifact PASS
requirement.

The contract is deliberately mechanical: page/text counts, field ceilings,
image form and total payload size. It does not judge teaching quality, and it
does not invent anything the component did not produce.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem
from services.workspace_artifact_versions import ensure_initial_version

logger = logging.getLogger(__name__)

KIND = "picture_book"
ITEM_TYPE = "study_picture_book"
BOOK_CONTRACT_VERSION = "darya.picture_book.v1"

STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
IMAGE_DIR_NAME = "picture_books"
IMAGE_URL_PREFIX = f"/static/{IMAGE_DIR_NAME}/"

_MIN_PAGES = 1
_MAX_PAGES = 12
_MAX_PAGE_TITLE = 120
_MAX_PAGE_TEXT = 2000
_MAX_PAGE_FACT = 400
_MAX_QUIZ = 12
_MAX_QUIZ_OPTIONS = 6
_MAX_QUIZ_TEXT = 600
_MAX_SHORT_TEXT = 200
# The browser sends illustrations inline (it is the only side that can reach a
# third-party image through the authenticated storybook proxy), so the inbound
# payload is legitimately large.
_MAX_PAYLOAD_BYTES = 12 * 1024 * 1024
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_IMAGES = 16
# After illustrations are written to /static and replaced by short URLs, the
# stored book is only text. The public projection truncates any string value at
# 120,000 characters, so a book that keeps its images inline would be served
# back as invalid JSON; keeping this cap far below that limit is what makes the
# stored book survive the read path.
_MAX_CONTENT_BYTES = 512 * 1024
_MAX_CLIENT_REF = 64

_DATA_URI_RE = re.compile(r"^data:image/(jpeg|jpg|png|webp);base64,(.+)$", re.DOTALL | re.IGNORECASE)
# The declared media type is not proof, and it is not even reliable: the
# component labels some JPEG payloads as image/png. The bytes are the truth, so
# the format is sniffed from the signature and the stored extension follows it.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),
)


class PictureBookError(RuntimeError):
    code = "PICTURE_BOOK_ERROR"


def _clean_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()[:limit]


def _clean_image(value: Any, *, index: int | None = None) -> tuple[str | None, str | None]:
    """Return `(stored_value, drop_reason)`.

    A reason is returned only when a picture we were given had to be dropped.
    A remote https illustration is kept as a link rather than discarded — the
    browser inlines what it can fetch, and losing the whole picture because one
    fetch failed would be worse — so it is counted as remote, not as a failure.
    """
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, "图片字段格式无效"
    candidate = value.strip()
    if not candidate:
        return None, None
    if _DATA_URI_RE.match(candidate):
        return candidate, None
    if candidate.startswith("https://"):
        return candidate[:2000], None
    return None, f"第 {index} 张插图形式不受支持" if index is not None else "插图形式不受支持"


def _clean_page(entry: Any, position: int) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise PictureBookError("绘本页面格式无效。")
    title = _clean_text(entry.get("title"), _MAX_PAGE_TITLE)
    content = _clean_text(entry.get("content") or entry.get("narration"), _MAX_PAGE_TEXT)
    if not title and not content:
        raise PictureBookError("绘本页面既没有标题也没有正文。")
    image, image_reason = _clean_image(entry.get("image_url"), index=position)
    page = {
        "index": position,
        "title": title or f"第 {position} 页",
        "content": content,
        "stage_label": _clean_text(entry.get("stage_label"), _MAX_SHORT_TEXT),
        "fact": _clean_text(entry.get("fact"), _MAX_PAGE_FACT),
        "image_url": image,
    }
    if image_reason:
        page["image_note"] = image_reason
    return page


def _clean_quiz(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    question = _clean_text(entry.get("question"), _MAX_QUIZ_TEXT)
    if not question:
        return None
    raw_options = entry.get("options")
    options = [
        _clean_text(option, _MAX_SHORT_TEXT)
        for option in (raw_options if isinstance(raw_options, (list, tuple)) else [])
    ]
    options = [option for option in options if option][:_MAX_QUIZ_OPTIONS]
    return {
        "question": question,
        "options": options,
        "answer": _clean_text(entry.get("answer"), _MAX_SHORT_TEXT),
        "explanation": _clean_text(entry.get("explanation"), _MAX_QUIZ_TEXT),
    }


def _write_inline_image(value: str, folder: Path, stem: str) -> tuple[str | None, str | None]:
    """Write one inline illustration to /static and return its public URL.

    Mirrors the audio-lesson precedent: real media lives under /static and the
    artifact keeps only a same-origin locator, so the stored book stays small
    and its illustrations do not depend on someone else's CDN.
    """
    match = _DATA_URI_RE.match(value)
    if match is None:
        return None, "插图数据无法识别"
    try:
        payload = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError):
        return None, "插图数据损坏"
    if not payload:
        return None, "插图数据为空"
    if len(payload) > _MAX_IMAGE_BYTES:
        return None, "插图超过单张上限"
    extension = next((name for marker, name in _SIGNATURES if payload.startswith(marker)), "")
    if not extension:
        return None, "插图数据不是有效图片"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.{extension}").write_bytes(payload)
    except OSError as exc:  # noqa: BLE001 - honest failure, no silent fallback
        logger.warning("[PictureBook] 插图写入失败: %s", exc)
        return None, "插图没能保存到服务器"
    return f"{IMAGE_URL_PREFIX}{folder.name}/{stem}.{extension}", None


def _extract_images(book: dict[str, Any]) -> dict[str, Any]:
    """Move every inline illustration out of the book and into /static.

    The book keeps short same-origin URLs; `image_status` is recomputed from
    what actually survived, so a page whose illustration could not be written
    says so instead of showing a silent gap.
    """
    folder = STATIC_ROOT / IMAGE_DIR_NAME / uuid4().hex[:12]
    pages: list[dict[str, Any]] = book["pages"]
    written = 0
    dropped = 0
    notes: list[str] = []

    # Illustrations already refused during validation (an unusable form such as
    # a blob: URL) carry their own reason and must be counted here too, so
    # `missing` means "we were given a picture and could not keep it".
    if book.get("cover_note"):
        dropped += 1
        notes.append(f"封面{book['cover_note']}")
    for position, page in enumerate(pages, start=1):
        if page.get("image_note"):
            dropped += 1
            notes.append(f"第 {position} 页{page['image_note']}")

    cover = book.get("cover_image")
    if isinstance(cover, str) and _DATA_URI_RE.match(cover):
        url, reason = _write_inline_image(cover, folder, "cover")
        book["cover_image"] = url
        if url:
            written += 1
        else:
            dropped += 1
            notes.append(f"封面{reason}")

    for position, page in enumerate(pages, start=1):
        value = page.get("image_url")
        if not (isinstance(value, str) and _DATA_URI_RE.match(value)):
            continue
        url, reason = _write_inline_image(value, folder, f"page-{position:02d}")
        if url:
            page["image_url"] = url
            written += 1
        else:
            page["image_url"] = None
            page["image_note"] = reason
            dropped += 1
            notes.append(f"第 {position} 页{reason}")

    remote = 0
    if str(book.get("cover_image") or "").startswith("https://"):
        remote += 1
        notes.append("封面插图仍是远端链接，原图失效时会显示不出来")
    for position, page in enumerate(pages, start=1):
        if str(page.get("image_url") or "").startswith("https://"):
            remote += 1
            notes.append(f"第 {position} 页插图仍是远端链接，原图失效时会显示不出来")

    if not written:
        # Nothing was kept: do not leave an empty directory behind.
        try:
            folder.rmdir()
        except OSError:
            pass

    book["image_status"] = {
        "stored": written,
        "remote": remote,
        "missing": dropped,
        "notes": notes[:_MAX_PAGES + 2],
    }
    return book


def validate_picture_book(payload: Any) -> dict[str, Any]:
    """Validate the browser component's payload into the stored book shape."""
    if not isinstance(payload, dict):
        raise PictureBookError("绘本数据格式无效。")

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, (list, tuple)) or not raw_pages:
        raise PictureBookError("绘本没有可保存的页面。")
    if len(raw_pages) > _MAX_PAGES:
        raise PictureBookError(f"绘本页数超过上限（{_MAX_PAGES} 页）。")
    if len(raw_pages) < _MIN_PAGES:
        raise PictureBookError("绘本页数不足。")

    pages = [_clean_page(entry, position) for position, entry in enumerate(raw_pages, start=1)]

    raw_quiz = payload.get("quiz")
    quiz = [
        cleaned
        for cleaned in (
            _clean_quiz(entry)
            for entry in (raw_quiz if isinstance(raw_quiz, (list, tuple)) else [])
        )
        if cleaned
    ][:_MAX_QUIZ]

    cover, cover_reason = _clean_image(payload.get("cover_image"))
    title = _clean_text(payload.get("title"), _MAX_PAGE_TITLE) or "教学绘本"

    # Inbound size is bounded before anything touches the artifact table; the
    # stored size is checked after illustrations move to /static, because that
    # is what the read path actually has to serve.
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise PictureBookError("绘本体积超过上限，请减少页数或插图后重试。")

    book: dict[str, Any] = {
        "contract": BOOK_CONTRACT_VERSION,
        "title": title,
        "topic": _clean_text(payload.get("topic"), _MAX_SHORT_TEXT),
        "grade_text": _clean_text(payload.get("grade_text"), _MAX_SHORT_TEXT),
        "style": _clean_text(payload.get("style"), _MAX_SHORT_TEXT),
        "difficulty": _clean_text(payload.get("difficulty"), _MAX_SHORT_TEXT),
        "summary": _clean_text(payload.get("summary"), _MAX_PAGE_TEXT),
        "thinking_question": _clean_text(payload.get("thinking_question"), _MAX_PAGE_TEXT),
        "cover_image": cover,
        "pages": pages,
        "quiz": quiz,
        "storyboard_degraded": bool(payload.get("degraded")),
    }
    if cover_reason:
        book["cover_note"] = cover_reason
    return book


def _assert_storable(book: dict[str, Any]) -> None:
    """Refuse a book the public projection could not serve back intact.

    The read path truncates any string value at 120,000 characters. A book
    whose illustrations are still inline would be stored as megabytes of base64
    and come back as invalid JSON, so this bound is what keeps "saved" honest.
    """
    size = len(json.dumps(book, ensure_ascii=False).encode("utf-8"))
    if size > _MAX_CONTENT_BYTES:
        raise PictureBookError("绘本正文超过可保存上限，请减少页数后重试。")


def _client_reference(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return _clean_text(payload.get("artifact_id") or payload.get("client_ref"), _MAX_CLIENT_REF)


async def save_picture_book(
    db: AsyncSession, *, user_id: int, payload: Any, thread_id: str | None = None,
) -> GeneratedItem:
    """Store one generated book as an owned artifact version.

    Re-submitting the same component-side book returns the stored artifact
    instead of stacking duplicates, so a repeated event cannot quietly create
    two copies of one book.
    """
    book = validate_picture_book(payload)
    client_ref = _client_reference(payload)

    if client_ref:
        # Only the metadata columns are read: a stored book's content can be
        # large, and the dedupe only needs the client reference.
        rows = (await db.execute(select(GeneratedItem.id, GeneratedItem.raw_data).where(
            GeneratedItem.user_id == int(user_id),
            GeneratedItem.type == ITEM_TYPE,
        ))).all()
        for row_id, raw in rows:
            if str(dict(raw or {}).get("client_ref") or "") == client_ref:
                stored = await db.get(GeneratedItem, row_id)
                if stored is not None:
                    return stored

    # Illustrations leave the book before it is stored: the read path truncates
    # long strings, and a book that keeps its pictures inline would come back as
    # unparseable JSON. Failures here are recorded per illustration, not raised.
    book = _extract_images(book)
    _assert_storable(book)

    now = datetime.now(timezone.utc)
    page_count = len(book["pages"])
    style = str(book.get("style") or "")
    digest = hashlib.sha256(
        json.dumps(book, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    item = GeneratedItem(
        user_id=int(user_id),
        type=ITEM_TYPE,
        title=book["title"][:200],
        # The list read model surfaces `chapter` as the row summary; page count
        # and style are what a learner actually needs to recognise a book.
        chapter=(f"{page_count} 页 · {style}" if style else f"{page_count} 页")[:120],
        content=json.dumps(book, ensure_ascii=False),
        raw_data={
            "kind": KIND,
            "status": "ready",
            "thread_id": str(thread_id or ""),
            "client_ref": client_ref or None,
            "book_digest": digest,
            "generation_provenance": {
                "mode": "browser_storybook_component",
                "component": "darya-storybook",
                "contract": BOOK_CONTRACT_VERSION,
            },
            "created_at": now.isoformat(),
        },
    )
    db.add(item)
    await db.flush()
    await ensure_initial_version(db, item)
    await db.commit()
    await db.refresh(item)
    return item
