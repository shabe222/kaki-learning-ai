import argparse
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

SEARCH_URL = "https://duckduckgo.com/html/?q={query}"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
LAST_SEARCH_DIAGNOSTICS: List[str] = []

SOURCE_CONFIG = {
    "bilibili": {"label": "Bilibili", "domain": "bilibili.com/video", "kind": "video", "search": "https://search.bilibili.com/all?keyword={query}"},
    "youtube": {"label": "YouTube", "domain": "youtube.com/watch", "kind": "video", "search": "https://www.youtube.com/results?search_query={query}"},
    "coursera": {"label": "Coursera", "domain": "coursera.org", "kind": "course", "search": "https://www.coursera.org/search?query={query}"},
    "edx": {"label": "edX", "domain": "edx.org", "kind": "course", "search": "https://www.edx.org/search?q={query}"},
    "udemy": {"label": "Udemy", "domain": "udemy.com/course", "kind": "course", "search": "https://www.udemy.com/courses/search/?q={query}"},
    "khan": {"label": "Khan Academy", "domain": "khanacademy.org", "kind": "course", "search": "https://www.khanacademy.org/search?page_search_query={query}"},
    "ted": {"label": "TED", "domain": "ted.com/talks", "kind": "video", "search": "https://www.ted.com/search?q={query}"},
    "general": {"label": "General Web", "domain": "", "kind": "mixed", "search": "https://duckduckgo.com/?q={query}"},
}

DEFAULT_SOURCES = ["bilibili", "youtube", "coursera", "edx", "udemy"]

PLATFORM_DOMAINS = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "bilibili.com": "Bilibili",
    "coursera.org": "Coursera",
    "edx.org": "edX",
    "udemy.com": "Udemy",
    "khanacademy.org": "Khan Academy",
    "ted.com": "TED",
}

TOPIC_HINTS = {
    "java": "Java programming beginner tutorial",
    "python": "Python programming beginner tutorial",
    "js": "JavaScript programming beginner tutorial",
    "javascript": "JavaScript programming beginner tutorial",
    "react": "React frontend beginner tutorial",
    "vue": "Vue frontend beginner tutorial",
    "sql": "SQL database beginner tutorial",
}


@dataclass
class VideoResult:
    title: str
    platform: str
    url: str
    cover: Optional[str]
    images: List[str]
    snippet: Optional[str] = None
    image_status: str = "unknown"
    image_note: str = "Image status was not evaluated."


def http_get(url: str, timeout: int = 8) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def http_post_json(url: str, payload: dict, headers: Dict[str, str], timeout: int = 12) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_duckduckgo_url(url: str) -> str:
    parsed = urlparse(html.unescape(url))
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        raw = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(raw) if raw else url
    return html.unescape(url)


def infer_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for domain, name in PLATFORM_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "Unknown"


def extract_search_results(page_html: str) -> List[Tuple[str, str, str]]:
    results: List[Tuple[str, str, str]] = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.I | re.S,
    )
    snippet_pattern = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.I | re.S)
    snippets = [clean_text(m.group("snippet")) for m in snippet_pattern.finditer(page_html)]

    for index, match in enumerate(pattern.finditer(page_html)):
        url = normalize_duckduckgo_url(match.group("url"))
        title = clean_text(match.group("title"))
        if not title or not url.startswith(("http://", "https://")):
            continue
        if "duckduckgo.com/y.js" in url or "bing.com/aclick" in url:
            continue
        snippet = snippets[index] if index < len(snippets) else ""
        results.append((title, url, snippet))
    return results


def extract_meta(html_text: str, base_url: str) -> Tuple[Optional[str], List[str]]:
    images: List[str] = []
    for match in re.finditer(r"<meta\b[^>]*>", html_text, re.I):
        attrs = parse_attrs(match.group(0))
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        content = attrs.get("content")
        if content and key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src", "image", "thumbnailurl"}:
            images.append(resolve_url(base_url, html.unescape(content)))

    unique = dedupe(images)
    return (unique[0] if unique else None, unique[:8])


def parse_attrs(tag: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for match in re.finditer(r"([a-zA-Z_:.-]+)\s*=\s*(['\"])(.*?)\2", tag, re.S):
        attrs[match.group(1).lower()] = match.group(3)
    return attrs


def resolve_url(base_url: str, value: str) -> str:
    value = value.strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith(("http://", "https://")):
        return value
    parsed = urlparse(base_url)
    if value.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{value}"
    prefix = base_url.rsplit("/", 1)[0]
    return f"{prefix}/{value}"


def dedupe(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    output: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def normalize_topic(topic: str) -> str:
    topic = clean_text(topic)
    return TOPIC_HINTS.get(topic.lower(), topic)


def compact_topic(topic: str) -> str:
    topic = clean_text(topic)
    key = topic.lower()
    if key in TOPIC_HINTS:
        return topic
    return topic


def rewrite_topic_with_llm(topic: str) -> Optional[str]:
    if os.getenv("VIDEO_AGENT_USE_LLM", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return None

    key = os.getenv("VIDEO_AGENT_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        return None

    base_url = os.getenv("VIDEO_AGENT_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com"
    model = os.getenv("VIDEO_AGENT_MODEL") or "deepseek-v4.1-flash"
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "把用户学习目标改写成适合搜索教学视频或课程的短查询词。只输出查询词。",
            },
            {"role": "user", "content": topic},
        ],
        "temperature": 0.2,
        "max_tokens": 40,
    }
    try:
        timeout = int(os.getenv("VIDEO_AGENT_LLM_TIMEOUT", "4"))
        data = http_post_json(url, payload, {"Authorization": f"Bearer {key}"}, timeout=timeout)
        return clean_text(data["choices"][0]["message"]["content"]).strip('"')
    except Exception:
        return None


def parse_sources(sources: Optional[Iterable[str]]) -> List[str]:
    if not sources:
        return list(DEFAULT_SOURCES)
    output = [source for source in sources if source in SOURCE_CONFIG]
    return output or list(DEFAULT_SOURCES)


def build_queries(topic: str, sources: Iterable[str]) -> List[Tuple[str, str]]:
    seed = compact_topic(topic)
    queries: List[Tuple[str, str]] = []
    for source in sources:
        config = SOURCE_CONFIG[source]
        domain = config["domain"]
        if source == "bilibili":
            query = f"{seed} tutorial site:{domain}"
        elif source == "youtube":
            query = f"{seed} tutorial site:{domain}"
        elif source == "general":
            query = f"{seed} tutorial course video"
        else:
            query = f"{seed} course site:{domain}"
        queries.append((source, query))
    return queries


def search_videos(
    topic: str,
    limit: int = 12,
    enrich: bool = False,
    search_timeout: int = 6,
    max_queries: int = 8,
    fetch_covers: bool = True,
    sources: Optional[Iterable[str]] = None,
    total_timeout: int = 10,
    **_: object,
) -> List[VideoResult]:
    LAST_SEARCH_DIAGNOSTICS.clear()
    selected_sources = parse_sources(sources)
    tavily_key = os.getenv("TAVILY_API_KEY") or os.getenv("VIDEO_AGENT_SEARCH_API_KEY")
    if tavily_key:
        results = search_with_tavily(
            topic=topic,
            sources=selected_sources,
            limit=limit,
            timeout=total_timeout,
            api_key=tavily_key,
            fetch_covers=fetch_covers,
        )
        if results:
            return results
        LAST_SEARCH_DIAGNOSTICS.append("Tavily API did not return usable results. Check whether TAVILY_API_KEY is a valid Tavily key.")
        if os.getenv("VIDEO_AGENT_DISABLE_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}:
            return []

    rewritten = rewrite_topic_with_llm(topic)
    retrieval_topic = rewritten or topic
    queries = build_queries(retrieval_topic, selected_sources)[:max(1, max_queries)]
    collected: List[VideoResult] = []
    seen_urls: Set[str] = set()

    deadline = time.time() + max(1, total_timeout)

    executor = ThreadPoolExecutor(max_workers=min(len(queries), 6))
    try:
        futures = {
            executor.submit(http_get, SEARCH_URL.format(query=quote_plus(query)), search_timeout): source
            for source, query in queries
        }

        try:
            completed = as_completed(futures, timeout=max(1, total_timeout))
            for future in completed:
                if time.time() > deadline:
                    break
                if len(collected) >= limit:
                    break
                try:
                    page = future.result()
                except (HTTPError, URLError, TimeoutError, OSError):
                    continue

                for title, url, snippet in extract_search_results(page):
                    if len(collected) >= limit:
                        break
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    if not looks_relevant(topic, url, title, snippet):
                        continue

                    cover = platform_cover(url) if fetch_covers else None
                    images: List[str] = [cover] if cover else []
                    image_status, image_note = image_feedback(fetch_covers, cover, images, url)
                    if enrich and fetch_covers and not cover:
                        try:
                            detail_html = http_get(url, timeout=3)
                            cover, images = extract_meta(detail_html, url)
                            image_status, image_note = image_feedback(fetch_covers, cover, images, url)
                            time.sleep(0.03)
                        except Exception:
                            image_status = "unavailable"
                            image_note = "Tried to open the result page, but image extraction failed or timed out."
                            pass

                    collected.append(
                        VideoResult(
                            title=title,
                            platform=infer_platform(url),
                            url=url,
                            cover=cover,
                            images=images,
                            snippet=snippet or None,
                            image_status=image_status,
                            image_note=image_note,
                        )
                    )
        except FuturesTimeout:
            pass
    finally:
        executor.shutdown(wait=False)

    ranked = sorted(collected, key=lambda item: relevance_score(topic, item), reverse=True)[:limit]
    if ranked:
        return ranked

    if os.getenv("VIDEO_AGENT_LLM_FALLBACK", "1").strip().lower() not in {"0", "false", "no", "off"}:
        LAST_SEARCH_DIAGNOSTICS.append("Live extraction returned no usable results, so platform search entry links were generated.")
        return build_platform_search_entries(retrieval_topic, selected_sources, limit)
    return []


def build_platform_search_entries(topic: str, sources: Iterable[str], limit: int) -> List[VideoResult]:
    entries: List[VideoResult] = []
    for source in sources:
        config = SOURCE_CONFIG[source]
        query = platform_query(topic, source)
        url = config["search"].format(query=quote_plus(query))
        entries.append(
            VideoResult(
                title=f"{config['label']} search: {query}",
                platform=config["label"],
                url=url,
                cover=None,
                images=[],
                snippet="实时结果提取暂不可用。打开这个平台搜索入口，可以直接查看当前最新的视频或课程。",
                image_status="not_applicable",
                image_note="这是平台搜索入口，不是具体视频页，因此没有视频封面。",
            )
        )
        if len(entries) >= limit:
            break
    return entries


def platform_query(topic: str, source: str) -> str:
    base = compact_topic(topic)
    if source in {"bilibili"}:
        return f"{base} 教程 视频"
    if source in {"youtube"}:
        return f"{base} tutorial video"
    if source in {"coursera", "edx", "udemy", "khan"}:
        return f"{base} course"
    if source == "ted":
        return f"{base} talk"
    return f"{base} tutorial course video"


def search_with_tavily(
    topic: str,
    sources: Iterable[str],
    limit: int,
    timeout: int,
    api_key: str,
    fetch_covers: bool = True,
) -> List[VideoResult]:
    domains = [SOURCE_CONFIG[source]["domain"].split("/")[0] for source in sources if SOURCE_CONFIG[source]["domain"]]
    payload = {
        "query": f"{normalize_topic(topic)} teaching video course tutorial",
        "search_depth": "fast",
        "topic": "general",
        "max_results": min(max(limit, 5), 20),
        "include_images": fetch_covers,
        "include_image_descriptions": False,
        "include_answer": False,
        "include_raw_content": False,
    }
    if domains:
        payload["include_domains"] = dedupe(domains)

    try:
        data = http_post_json(
            TAVILY_SEARCH_URL,
            payload,
            {"Authorization": f"Bearer {api_key}"},
            timeout=max(3, timeout),
        )
    except Exception:
        LAST_SEARCH_DIAGNOSTICS.append("Tavily request failed.")
        return []

    shared_images = image_urls(data.get("images") or [])
    output: List[VideoResult] = []
    for row in data.get("results") or []:
        url = row.get("url") or ""
        title = clean_text(row.get("title"))
        snippet = clean_text(row.get("content")) or None
        if not url or not title:
            continue
        if not looks_relevant(topic, url, title, snippet or ""):
            continue
        row_images = image_urls(row.get("images") or [])
        cover = platform_cover(url)
        images = dedupe(([cover] if cover else []) + row_images + shared_images[:2])
        image_status, image_note = image_feedback(fetch_covers, images[0] if images else None, images, url)
        output.append(
            VideoResult(
                title=title,
                platform=infer_platform(url),
                url=url,
                cover=images[0] if images else None,
                images=images[:8],
                snippet=snippet,
                image_status=image_status,
                image_note=image_note,
            )
        )
        if len(output) >= limit:
            break
    return sorted(output, key=lambda item: relevance_score(topic, item), reverse=True)[:limit]


def image_urls(values: Iterable[object]) -> List[str]:
    urls: List[str] = []
    for value in values:
        if isinstance(value, str):
            urls.append(value)
        elif isinstance(value, dict) and value.get("url"):
            urls.append(str(value["url"]))
    return dedupe(urls)


def image_feedback(fetch_covers: bool, cover: Optional[str], images: List[str], url: str) -> Tuple[str, str]:
    if not fetch_covers:
        return "disabled", "为了提高搜索速度，当前关闭了封面提取。"
    if cover or images:
        return "available", "已获取或推断出封面图片。"
    if extract_youtube_id(url):
        return "unavailable", "检测到 YouTube 视频，但未能生成缩略图链接。"
    platform = infer_platform(url)
    if platform in {"Coursera", "edX", "Udemy", "Khan Academy", "TED"}:
        return "missing", "搜索结果没有提供封面图，这类课程页有时不会在搜索摘要里暴露图片。"
    return "unavailable", "没有找到封面图。可能是平台隐藏图片、阻止提取，或该页面没有公开缩略图。"


def platform_cover(url: str) -> Optional[str]:
    youtube_id = extract_youtube_id(url)
    if youtube_id:
        return f"https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg"
    return None


def extract_youtube_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        return parsed.path.strip("/") or None
    if "youtube.com" in host:
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if query_id:
            return query_id
        match = re.search(r"/watch/([0-9A-Za-z_-]{6,})", parsed.path)
        if match:
            return match.group(1)
        match = re.search(r"/(?:embed|shorts)/([0-9A-Za-z_-]{6,})", parsed.path)
        if match:
            return match.group(1)
    return None


def topic_tokens(topic: str) -> List[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]*", topic)]


def looks_relevant(topic: str, url: str, title: str, snippet: str) -> bool:
    text = f"{url} {title} {snippet}".lower()
    tokens = topic_tokens(topic)
    if tokens and not any(token in text for token in tokens):
        return False
    if tokens and any(token in text for token in tokens):
        return not any(word in text for word in ["/playlist?", "login", "signup", "privacy", "terms", "ads", "aclick"])

    positive = [
        "video",
        "watch",
        "course",
        "tutorial",
        "lesson",
        "training",
        "programming",
        "bilibili.com/video",
        "youtube.com/watch",
    ]
    negative = ["/playlist?", "login", "signup", "privacy", "terms", "ads", "aclick"]
    return any(word in text for word in positive) and not any(word in text for word in negative)


def relevance_score(topic: str, item: VideoResult) -> int:
    text = f"{item.title} {item.snippet or ''} {item.url}".lower()
    normalized = normalize_topic(topic).lower()
    tokens = topic_tokens(topic) + topic_tokens(normalized)
    score = sum(5 for token in set(tokens) if token and token in text)
    for word in ["tutorial", "course", "lesson", "training", "programming", "教程", "教学", "课程", "入门", "零基础"]:
        if word in text:
            score += 1
    if item.cover:
        score += 1
    return score


def main() -> int:
    parser = argparse.ArgumentParser(description="Search teaching videos and courses by topic.")
    parser.add_argument("topic", help="想学习的主题，例如：Java 或 Python 数据分析")
    parser.add_argument("--limit", type=int, default=12, help="返回条数，默认 12")
    parser.add_argument("--sources", default=",".join(DEFAULT_SOURCES), help="平台 ID，用逗号分隔")
    parser.add_argument("--enrich", action="store_true", help="访问详情页提取封面，速度较慢")
    parser.add_argument("--search-timeout", type=int, default=6, help="单个平台搜索超时秒数，默认 6")
    parser.add_argument("--total-timeout", type=int, default=10, help="整体搜索预算秒数，默认 10")
    parser.add_argument("--max-queries", type=int, default=8, help="最多并发搜索查询数，默认 8")
    parser.add_argument("--no-covers", action="store_true", help="不生成可推断封面，速度更快")
    args = parser.parse_args()

    results = search_videos(
        args.topic,
        limit=args.limit,
        enrich=args.enrich,
        search_timeout=args.search_timeout,
        max_queries=args.max_queries,
        fetch_covers=not args.no_covers,
        sources=[item.strip() for item in args.sources.split(",") if item.strip()],
        total_timeout=args.total_timeout,
    )
    print(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
