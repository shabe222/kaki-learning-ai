"""Real authoritative search, durable result sets, and material import."""
from __future__ import annotations

import hashlib
import html
import json
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ChatSession, GeneratedItem, WorkspaceMaterial
from services.workspace_materials import attach_workspace_material, create_workspace_material


class SearchError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderResult:
    title: str
    summary: str
    url: str
    source_name: str
    source_kind: str
    provider: str
    published_at: str = ""


@dataclass(frozen=True)
class FetchedDocument:
    file_name: str
    content_type: str
    payload: bytes


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int) -> list[ProviderResult]: ...


_TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}
_OFFICIAL_SUFFIXES = (".gov.cn", ".edu.cn", ".ac.cn")
_MAX_FETCH_BYTES = 8 * 1024 * 1024
_SUPPORTED_OFFICIAL_CONTENT_TYPES = {"text/html", "text/plain", "application/pdf"}


def _is_official_host(host: str) -> bool:
    normalized = str(host or "").lower().rstrip(".")
    return any(normalized == suffix[1:] or normalized.endswith(suffix) for suffix in _OFFICIAL_SUFFIXES)
_ACADEMIC_HINTS = ("论文", "研究", "文献", "前沿", "paper", "research", "survey", "arxiv")


def canonicalize_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SearchError("搜索结果链接无效")
    query = urlencode([(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
                       if key.lower() not in _TRACKING_KEYS])
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def _source_priority(item: dict) -> tuple[int, str]:
    return ({"government": 0, "university": 1, "academic": 2}.get(item["source_kind"], 3), item["title"])


def normalize_search_results(values: Iterable[ProviderResult], *, query: str, limit: int) -> list[dict]:
    seen: set[str] = set()
    normalized: list[dict] = []
    for value in values:
        try:
            url = canonicalize_url(value.url)
        except SearchError:
            continue
        if url in seen:
            continue
        title = " ".join(str(value.title or "").split()).strip()
        summary = " ".join(html.unescape(str(value.summary or "")).split()).strip()
        if not title or not summary:
            continue
        seen.add(url)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        normalized.append({
            "result_id": "result_" + digest,
            "title": title[:300],
            "summary": summary[:1200],
            "source_name": " ".join(str(value.source_name or "未知来源").split())[:160],
            "source_kind": value.source_kind if value.source_kind in {"government", "university", "academic"} else "web",
            "provider": str(value.provider or "unknown")[:40],
            "published_at": str(value.published_at or "")[:40],
            "url": url,
            "query": str(query or "")[:240],
            "content_kind": "abstract_index" if value.source_kind == "academic" else "source_page",
        })
    normalized.sort(key=_source_priority)
    return normalized[: max(1, min(int(limit), 10))]


class OfficialWebProvider:
    """Key-free real web discovery through the public Bing RSS surface.

    This is isolated behind the provider contract because public search surfaces
    can change; failure simply allows the academic provider or ordinary chat to continue.
    """

    endpoint = "https://www.bing.com/search"

    async def search(self, query: str, limit: int) -> list[ProviderResult]:
        scoped = f"{query} (site:gov.cn OR site:edu.cn OR site:ac.cn)"
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(self.endpoint, params={"q": scoped, "format": "rss", "setlang": "zh-Hans"})
            response.raise_for_status()
        root = ET.fromstring(response.content)
        results: list[ProviderResult] = []
        for node in root.findall("./channel/item")[:limit]:
            url = (node.findtext("link") or "").strip()
            host = (urlparse(url).hostname or "").lower()
            if not _is_official_host(host):
                continue
            kind = "government" if host.endswith(".gov.cn") else "university"
            results.append(ProviderResult(
                title=node.findtext("title") or "",
                summary=node.findtext("description") or "",
                url=url,
                source_name=host,
                source_kind=kind,
                provider="official_web",
                published_at=node.findtext("pubDate") or "",
            ))
        return results


class ArxivProvider:
    endpoint = "https://export.arxiv.org/api/query"
    atom = {"a": "http://www.w3.org/2005/Atom"}

    async def search(self, query: str, limit: int) -> list[ProviderResult]:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(self.endpoint, params={
                "search_query": f"all:{query}", "start": 0,
                "max_results": max(1, min(limit, 10)), "sortBy": "relevance",
            })
            response.raise_for_status()
        root = ET.fromstring(response.content)
        results: list[ProviderResult] = []
        for entry in root.findall("a:entry", self.atom):
            url = (entry.findtext("a:id", default="", namespaces=self.atom) or "").strip()
            results.append(ProviderResult(
                title=entry.findtext("a:title", default="", namespaces=self.atom) or "",
                summary=entry.findtext("a:summary", default="", namespaces=self.atom) or "",
                url=url,
                source_name="arXiv",
                source_kind="academic",
                provider="arxiv",
                published_at=entry.findtext("a:published", default="", namespaces=self.atom) or "",
            ))
        return results


async def search_authoritative(
    query: str,
    *,
    source_scope: str = "official_first",
    limit: int = 6,
    providers: Optional[dict[str, SearchProvider]] = None,
) -> list[dict]:
    clean_query = " ".join(str(query or "").split()).strip()
    if not clean_query:
        raise SearchError("请输入要搜索的主题")
    limit = max(1, min(int(limit), 10))
    registry = providers or {"official": OfficialWebProvider(), "academic": ArxivProvider()}
    academic_requested = source_scope in {"academic", "all"} or any(word in clean_query.lower() for word in _ACADEMIC_HINTS)
    gathered: list[ProviderResult] = []
    attempted = 0
    provider_errors: list[str] = []
    if source_scope != "academic" and registry.get("official"):
        attempted += 1
        try:
            gathered.extend(await registry["official"].search(clean_query, limit))
        except Exception as exc:
            provider_errors.append(f"official:{type(exc).__name__}")
    if academic_requested or source_scope == "all" or len(gathered) < min(3, limit):
        if registry.get("academic"):
            attempted += 1
            try:
                gathered.extend(await registry["academic"].search(clean_query, limit))
            except Exception as exc:
                provider_errors.append(f"academic:{type(exc).__name__}")
    results = normalize_search_results(gathered, query=clean_query, limit=limit)
    if results:
        return results
    if attempted and len(provider_errors) == attempted:
        raise SearchError("当前无法连接权威资料来源，请稍后重试")
    return []


async def persist_search_results(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    query: str,
    results: list[dict],
    source_scope: str = "official_first",
) -> dict:
    if not thread_id or len(thread_id) > 100:
        raise SearchError("会话 ID 无效")
    search_id = "search_" + str(uuid.uuid4())
    payload = {
        "search_id": search_id,
        "thread_id": thread_id,
        "query": str(query or "")[:240],
        "source_scope": source_scope if source_scope in {"official_first", "academic", "all"} else "official_first",
        "results": list(results),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    item = GeneratedItem(
        user_id=user_id,
        type="workspace_search_results",
        title=f"权威资料搜索：{payload['query'][:80]}",
        chapter="工作区搜索",
        content=json.dumps(payload, ensure_ascii=False),
        raw_data=payload,
    )
    db.add(item)
    await db.flush()
    payload["artifact_id"] = item.id
    item.raw_data = dict(payload)
    item.content = json.dumps(payload, ensure_ascii=False)
    await db.commit()
    return payload


async def get_search_result_set(db: AsyncSession, *, user_id: int, search_id: str) -> Optional[dict]:
    row = await _get_search_result_item(db, user_id=user_id, search_id=search_id)
    return dict(row.raw_data or {}) if row is not None else None


async def _get_search_result_item(
    db: AsyncSession, *, user_id: int, search_id: str,
) -> Optional[GeneratedItem]:
    rows = (await db.execute(select(GeneratedItem).where(
        GeneratedItem.user_id == user_id,
        GeneratedItem.type == "workspace_search_results",
    ).order_by(GeneratedItem.created_at.desc(), GeneratedItem.id.desc()))).scalars().all()
    for row in rows:
        data = dict(row.raw_data or {})
        if data.get("search_id") == search_id:
            return row
    return None


async def _mark_search_result_imported(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    search_id: str,
    result_id: str,
    material_id: str,
) -> None:
    item = await _get_search_result_item(db, user_id=user_id, search_id=search_id)
    if item is None:
        return
    payload = dict(item.raw_data or {})
    updated_results: list[dict] = []
    for value in payload.get("results") or []:
        result = dict(value)
        if result.get("result_id") == result_id:
            result["imported_material_id"] = material_id
            result["import_status"] = "ready"
        updated_results.append(result)
    payload["results"] = updated_results
    item.raw_data = payload
    item.content = json.dumps(payload, ensure_ascii=False)

    session = (await db.execute(select(ChatSession).where(
        ChatSession.user_id == user_id,
        ChatSession.thread_id == thread_id,
    ))).scalar_one_or_none()
    if session is not None:
        messages: list[dict] = []
        for value in session.messages or []:
            message = dict(value)
            search = dict(message.get("search") or {})
            if search.get("search_id") == search_id:
                search["results"] = [dict(result) for result in updated_results]
                message["search"] = search
            messages.append(message)
        session.messages = messages
        session.message_count = len(messages)


async def append_search_result_message(
    db: AsyncSession, *, user_id: int, thread_id: str, search: dict,
) -> None:
    session = (await db.execute(select(ChatSession).where(
        ChatSession.user_id == user_id,
        ChatSession.thread_id == thread_id,
    ))).scalar_one_or_none()
    if session is None:
        session = ChatSession(user_id=user_id, thread_id=thread_id, messages=[], message_count=0)
        db.add(session)
        await db.flush()
    rows = list(session.messages or [])
    rows.append({
        "role": "assistant",
        "content": f"找到 {len(search.get('results') or [])} 条可核验资料。你可以查看原始来源并选择导入资料库。",
        "search": search,
    })
    session.messages = rows
    session.message_count = len(rows)
    session.last_message_at = datetime.utcnow()
    await db.commit()


def _clean_filename(value: str) -> str:
    clean = re.sub(r"[\\/:*?\"<>|\r\n]+", "-", str(value or "资料")).strip(" .-")
    return (clean or "搜索资料")[:100]


async def fetch_search_document(result: dict) -> FetchedDocument:
    url = canonicalize_url(result.get("url", ""))
    host = (urlparse(url).hostname or "").lower()
    # Academic providers expose indexed metadata/abstracts only. Preserve that
    # boundary instead of pretending the abstract is the full paper.
    if result.get("source_kind") == "academic":
        text = (
            f"# {result['title']}\n\n"
            "内容类型：学术摘要/索引（非论文全文）\n\n"
            f"{result['summary']}\n\n来源：{url}\n"
        )
        return FetchedDocument(_clean_filename(result["title"]) + ".md", "text/markdown", text.encode("utf-8"))
    if not _is_official_host(host):
        raise SearchError("当前只允许导入已验证的官方、高校或学术来源")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": "KakiLearning/1.0"}) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                final_url = canonicalize_url(str(response.url))
                final_host = (urlparse(final_url).hostname or "").lower()
                if not _is_official_host(final_host):
                    raise SearchError("来源跳转后离开允许的官方或高校域名，已停止导入")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in _SUPPORTED_OFFICIAL_CONTENT_TYPES and not urlparse(final_url).path.lower().endswith(".pdf"):
                    raise SearchError("该来源的内容类型暂不支持导入")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > _MAX_FETCH_BYTES:
                    raise SearchError("该来源内容过大，已停止导入")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_FETCH_BYTES:
                        raise SearchError("该来源内容过大，已停止导入")
                    chunks.append(chunk)
                payload = b"".join(chunks)
    except SearchError:
        raise
    except httpx.HTTPError as exc:
        raise SearchError("当前无法读取该来源，请稍后重试") from exc
    if content_type == "application/pdf" or urlparse(final_url).path.lower().endswith(".pdf"):
        return FetchedDocument(_clean_filename(result["title"]) + ".pdf", "application/pdf", payload)
    decoded = payload.decode(response.encoding or "utf-8", errors="replace")
    decoded = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", decoded)
    decoded = re.sub(r"(?i)</?(p|div|section|article|h[1-6]|li|br|tr)[^>]*>", "\n", decoded)
    decoded = re.sub(r"(?s)<[^>]+>", " ", decoded)
    body = "\n".join(line.strip() for line in html.unescape(decoded).splitlines() if line.strip())
    body = body[:300_000]
    if len(body) < 120:
        body = str(result.get("summary") or "")
    if len(body) < 40:
        raise SearchError("该来源没有可导入的正文")
    text = f"# {result['title']}\n\n{body}\n\n来源：{url}\n"
    return FetchedDocument(_clean_filename(result["title"]) + ".md", "text/markdown", text.encode("utf-8"))


async def import_search_result(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    search_id: str,
    result_id: str,
    fetcher: Callable[[dict], Awaitable[FetchedDocument]] = fetch_search_document,
    storage_root: Optional[Path] = None,
) -> WorkspaceMaterial:
    saved = await get_search_result_set(db, user_id=user_id, search_id=search_id)
    if saved is None or saved.get("thread_id") != thread_id:
        raise SearchError("搜索记录不存在或不属于当前会话")
    result = next((item for item in saved.get("results", []) if item.get("result_id") == result_id), None)
    if result is None:
        raise SearchError("搜索结果不存在")
    existing_material_id = str(result.get("imported_material_id") or "")
    if existing_material_id:
        existing = (await db.execute(select(WorkspaceMaterial).where(
            WorkspaceMaterial.user_id == user_id,
            WorkspaceMaterial.material_id == existing_material_id,
        ))).scalar_one_or_none()
        if existing is not None:
            await attach_workspace_material(db, user_id, thread_id, existing.material_id)
            return existing
    document = await fetcher(result)
    material = await create_workspace_material(
        db,
        user_id=user_id,
        thread_id=thread_id,
        file_name=document.file_name,
        content_type=document.content_type,
        payload=document.payload,
        storage_root=storage_root,
    )
    material.parse_metadata = {
        **dict(material.parse_metadata or {}),
        "origin": "search_import",
        "source_url": result["url"],
        "source_name": result.get("source_name", ""),
        "source_kind": result.get("source_kind", "web"),
        "provider": result.get("provider", ""),
        "source_content_kind": result.get("content_kind", "abstract_index" if result.get("source_kind") == "academic" else "source_page"),
        "search_id": search_id,
        "result_id": result_id,
        "search_query": saved.get("query", ""),
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    await _mark_search_result_imported(
        db,
        user_id=user_id,
        thread_id=thread_id,
        search_id=search_id,
        result_id=result_id,
        material_id=material.material_id,
    )
    await db.commit()
    await db.refresh(material)
    return material
