"""Bring a web page in as source material.

The product could ingest documents the learner happened to have as files, and
nothing else. 「把这个网页当资料讲给我」 was not possible — which is odd for a
tutor, because the page the learner is reading *is* the material.

A fetched article is turned into Markdown and handed to the existing
user-context ingestion path, so it inherits everything that already works:
parsing, source-addressable chunking, content-digest deduplication, and
appearance in the learner's material list. No second store is created.

Two things this deliberately does not do:

* it does not fetch arbitrary hosts — private, loopback and link-local
  addresses are refused, because a server-side fetcher that will dial anything
  is a request-forgery tool, not an ingestion feature;
* it does not invent structure. If the page yields too little readable text,
  that is reported as a failure instead of being stored as a "material" that
  would silently poison every answer generated from it.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

MAX_BYTES = 3_000_000
MIN_USABLE_CHARS = 200
FETCH_TIMEOUT_SECONDS = 20.0

_ALLOWED_SCHEMES = {"http", "https"}
# 这些标签里的文字不是正文；留着它们会让"资料"变成一堆导航和脚本。
# 两个刻意的例外，都是被真实网页教出来的：
# * head 不在里面 —— <title> 是这份资料最有用的元数据之一，而 script/style
#   已经各自被丢掉，整段跳过 head 会把标题一起扔掉；
# * form 不在里面 —— 老式 CMS（ASP.NET WebForms 那一类）会把**整页正文**包在
#   一个 <form> 里，整段跳过它，抓回来就只剩导航菜单。表单控件（input/select/
#   button）仍然各自被丢，所以表单外壳不会混进正文。
_DROP_TAGS = {
    "script", "style", "noscript", "template", "svg", "nav", "footer",
    "iframe", "aside", "button", "select", "option", "textarea",
}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "br", "li", "tr", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "td",
}
_HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####"}
# 主内容容器，按优先级找；找不到就退回整页正文。
_MAIN_HINTS = ("article", "main", "post-content", "article-content", "entry-content", "content")


class WebIngestionError(RuntimeError):
    code = "WEB_INGESTION_FAILED"


def _is_public_host(host: str) -> bool:
    """Refuse anything that resolves inside the machine or the local network."""
    if not host:
        return False
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
        except (socket.gaierror, ValueError, OSError):
            return False
    for address in addresses:
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            return False
    return True


def validate_url(url: str) -> str:
    """Return a normalised URL, or raise with the reason it was refused."""
    text = str(url or "").strip()
    if not text:
        raise WebIngestionError("请给出要读取的网页地址。")
    parsed = urlparse(text if "://" in text else f"https://{text}")
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise WebIngestionError("只支持 http 或 https 的网页地址。")
    if not parsed.netloc:
        raise WebIngestionError("这个地址看不出主机名。")
    if not _is_public_host(parsed.hostname or ""):
        raise WebIngestionError("这个地址指向本机或内网，不能作为资料来源读取。")
    return parsed.geturl()


class _ArticleExtractor(HTMLParser):
    """Small readability pass: keep block text, headings and list items.

    Not a full readability implementation — deliberately. It keeps the largest
    text-bearing container rather than guessing at prose quality, because a
    wrong guess here becomes "source material" for every later answer.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._depth_drop = 0
        self._in_title = False
        self._containers: list[list[str]] = [[]]
        # 每个推入过的容器都留一份记录，弹出之后也还在：最后要靠它挑出正文，
        # 而不是把整页（含导航菜单）当成"资料"。
        self._all: list[dict[str, Any]] = [{"text": self._containers[0], "main": False}]
        self._pending_main = False
        self._current_tag: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._depth_drop += 1
            return
        if self._depth_drop:
            return
        if tag == "title":
            self._in_title = True
        # 站点自己标出的正文容器（article / main / class 里带 content 的那些）优先。
        attrs_map = {str(key).lower(): str(value or "") for key, value in attrs}
        marker = f"{attrs_map.get('class', '')} {attrs_map.get('id', '')}".lower()
        if tag in {"article", "main"} or any(hint in marker for hint in _MAIN_HINTS):
            self._pending_main = True
        if tag in _BLOCK_TAGS or tag in _HEADING_TAGS:
            fresh: list[str] = []
            self._containers.append(fresh)
            self._all.append({"text": fresh, "main": self._pending_main})
            self._pending_main = False
        self._current_tag.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._depth_drop = max(0, self._depth_drop - 1)
            return
        if self._depth_drop:
            return
        if tag == "title":
            self._in_title = False
        if self._current_tag and self._current_tag[-1] == tag:
            self._current_tag.pop()
        if (tag in _BLOCK_TAGS or tag in _HEADING_TAGS) and len(self._containers) > 1:
            block = self._containers.pop()
            parent = self._containers[-1]
            text = " ".join(block).strip()
            if text:
                prefix = _HEADING_TAGS.get(tag, "")
                parent.append(f"{prefix} {text}".strip() if prefix else text)
                parent.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._depth_drop:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
            return
        self._containers[-1].append(text)

    def markdown(self) -> str:
        parts = ["".join(entry["text"]).strip() for entry in self._all]
        # 站点自己标出的正文容器优先。它比"最长的那一块"更可信 —— 导航菜单
        # 经常比正文还长，而菜单不是资料。
        main = [
            part for entry, part in zip(self._all, parts)
            if entry["main"] and len(part) >= MIN_USABLE_CHARS
        ]
        body = max(main, key=len) if main else (max(parts, key=len) if parts else "")
        if len(body) < MIN_USABLE_CHARS:
            # 退一步：所有文本按出现顺序拼起来，仍然太短就认输。
            body = "\n\n".join(part for part in parts if part).strip()
        body = re.sub(r"\n{3,}", "\n\n", body)
        return body.strip()


def extract_markdown(html: str, *, url: str = "") -> tuple[str, str]:
    """Return ``(title, markdown)`` for a fetched page."""
    parser = _ArticleExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - 坏 HTML 不该让整个导入失败
        raise WebIngestionError("这个网页的结构读不出来。") from exc
    title = re.sub(r"\s+", " ", parser.title).strip()[:200]
    body = parser.markdown()
    if not title:
        title = urlparse(url).netloc or "网页资料"
    if len(body) < MIN_USABLE_CHARS:
        raise WebIngestionError("这个网页的正文太短或需要登录，读不到可用内容。")
    return title, body


async def fetch_page(url: str) -> tuple[str, str]:
    """Fetch a public page and return ``(final_url, html)``."""
    import httpx

    clean = validate_url(url)
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": "DARYA-material-fetch/1.0 (+teaching material import)"},
        ) as client:
            response = await client.get(clean)
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").lower()
            if content_type and "html" not in content_type and "text" not in content_type:
                raise WebIngestionError(f"这个地址返回的是 {content_type.split(';')[0]}，不是网页。")
            body = response.content[:MAX_BYTES]
    except WebIngestionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise WebIngestionError(f"这个网页没能读到（{type(exc).__name__}）。") from exc
    return str(response.url), body.decode(response.encoding or "utf-8", errors="replace")


def as_markdown_document(*, title: str, url: str, body: str, retrieved_at: str) -> str:
    """The payload handed to ingestion:正文在前，出处写清楚。"""
    header = (
        f"# {title}\n\n"
        f"> 来源：{url}\n"
        f"> 抓取时间：{retrieved_at}\n\n"
    )
    return header + body.strip() + "\n"


async def ingest_url_as_material(
    db: Any, *, user_id: int, thread_id: str, url: str,
) -> dict[str, Any]:
    """Fetch a page and store it through the existing user-context ingestion path."""
    from datetime import datetime, timezone

    from services.tide_user_context import ingest_user_context_document

    final_url, html = await fetch_page(url)
    title, body = extract_markdown(html, url=final_url)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    document = as_markdown_document(title=title, url=final_url, body=body, retrieved_at=retrieved_at)
    safe_name = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", title).strip("-")[:60] or "web-page"
    snapshot = await ingest_user_context_document(
        db,
        user_id=user_id,
        thread_id=thread_id,
        file_name=f"{safe_name}.md",
        content_type="text/markdown",
        payload=document.encode("utf-8"),
    )
    return {
        "snapshot": snapshot,
        "source": {
            "title": title,
            "url": final_url,
            "retrieved_at": retrieved_at,
            "char_count": len(body),
        },
    }
