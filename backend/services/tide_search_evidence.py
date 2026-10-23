"""Bounded Phase 2.7 external search, evidence and trust foundation.

The layer intentionally stores no page bodies and no long-term memory.  Its
input accepts current conversation/workspace context and its provider is
replaceable.  Network failure is represented in the returned bundle.
"""
from __future__ import annotations

import html
import asyncio
import ipaddress
import json
import os
import re
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx


SourceType = Literal["primary", "authoritative", "secondary", "community", "unknown"]


# 检索用的共享 HTTP 连接池。
#
# 每个提供方以前每问一次就新建一个 httpx.AsyncClient：一次新的 TCP + TLS 握手发到境外站点。
# 在空跑进程里这只要 0.2s 左右，看不出来；但 live 服务进程里同时在跑规划器的模型流、
# 资料生成和别的请求，握手要和这些连接抢，于是同一件事从 2.5s 变成 6s 都回不来。
# 2026-09-23 实测就是这个形状：干净进程里连问 5 次都成功（2.1～3.1s），
# live 里每一轮都是 `duckduckgo-html TIMEOUT n=0 6000ms`，而它正是唯一给出
# 中文对题页面的提供方——被掐掉就等于这一轮 0 来源。
#
# 复用同一个 client 就没有重复握手，连接保持温热。它是进程级的长期资源，
# 随进程结束回收，服务本来就是长期进程。
_http_client: httpx.AsyncClient | None = None


def _search_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            follow_redirects=True,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            headers={"User-Agent": "Mozilla/5.0 (compatible; DARYA-Evidence/1.0)"},
            proxy=_search_proxy_argument(),
            trust_env=_search_trust_env(),
        )
    return _http_client
ClaimType = Literal["FACT", "INFERENCE", "OPINION", "EXPERIENCE", "UNKNOWN"]
EvidenceStatus = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "CONFLICTING", "UNVERIFIED"]
SourceRelationship = Literal["DIFFERENT_LAYER", "CONFLICTING", "NOT_COMPARABLE"]
SearchDecision = Literal["SEARCH", "SKIP", "QA_FORCED_SEARCH"]
EvidenceDisposition = Literal["ANSWER", "FURTHER_RETRIEVAL", "CALIBRATED_FALLBACK", "NO_SEARCH"]


@dataclass
class SearchContext:
    message: str
    query_override: str = ""
    # 模型在计划里写好的后续检索查询（research_task.questions[].query）。
    #
    # 2026-09-23 实测：模型为「婚礼上弹一首歌」写了三条像样的检索式
    # （「告白气球 吉他谱 简化版 C调 和弦 新手」等），管道一条都没用——
    # 查询词由 query_override（另一个规划器）或对用户原话的正则改写决定，
    # 实际发出去的是「在县城摆早点摊 卖包子豆浆 一天流水大概六七百块」，
    # 于是 0 来源、证据 UNVERIFIED、模型只能标「待核实」。
    # 检索式该由计划它的人给，这里逐轮消费。
    planned_queries: list[str] = field(default_factory=list)
    shared_perspective: dict = field(default_factory=dict)
    workspace_object: dict = field(default_factory=dict)
    artifact_context: dict = field(default_factory=dict)
    # Trusted provider bootstrap locators selected by DARYA's search planner.
    # These are fetched live for this turn; they are not Domain Packs or memory.
    preferred_urls: list[str] = field(default_factory=list)


@dataclass
class SearchHit:
    title: str
    url: str
    excerpt: str
    source: str
    published_at: str | None = None


@dataclass
class EvidenceSource:
    title: str
    url: str
    source: str
    excerpt: str
    source_type: SourceType
    trust_reason: str
    retrieved_at: str
    published_at: str | None = None
    claim_type: ClaimType = "FACT"


@dataclass
class RetrievalRound:
    round_number: int
    query: str
    strategy: str
    reason: str
    new_source_count: int
    cumulative_source_count: int
    disposition: EvidenceDisposition
    sufficient: bool
    missing_evidence: list[str]
    error: str | None = None
    provider: str | None = None


@dataclass
class ProviderAttempt:
    provider: str
    query: str
    outcome: str
    result_count: int
    elapsed_ms: int
    error: str | None = None


def _search_proxy_setting() -> str:
    """检索出口配置的取值。

    先看应用设置（`.env` → pydantic-settings），再看进程环境。
    两者都要看：应用的 `.env` 走 pydantic-settings，**不会**写进 os.environ，
    只看环境变量会读不到 `.env` 里配的值。
    """
    try:
        from config import settings

        value = str(getattr(settings, "SEARCH_PROXY", "") or "").strip()
        if value:
            return value
    except Exception:
        pass
    return str(os.environ.get("SEARCH_PROXY") or "").strip()


def _search_route() -> str:
    """检索请求实际走哪条出口，以及这个选择是谁做的。

    这台机器装了本地代理（用户级 127.0.0.1:7897，出口在境外），而产品原来**只在调模型时**
    显式用它（`.env` 的 OPENAI_COMPATIBLE_PROXY）；检索请求没有任何代理配置，
    走哪条路完全取决于进程环境——看不见，也不可控。

    为什么必须可控：2026-09-24 实测，**同一个进程外的调用能搜到、服务进程里搜不到**，
    两者报出来的 route 都是 `direct-or-transparent`。把出口变成可显式指定的一项之后
    一次就问清了：同一条查询，强制直连 → `duckduckgo-html EMPTY n=0 10000ms`（完整复现
    服务进程的症状）；显式走 7897 → `SUCCESS n=8 1953ms`。**直连 DuckDuckGo 在这条线路上
    不通，而它是唯一给出中文对题页面的提供方。** 服务进程没有配代理，等于直连，所以永远 0 来源。
    """
    explicit = _search_proxy_setting()
    if explicit:
        lowered = explicit.lower()
        if lowered in {"direct", "none", "off"}:
            return "forced-direct"
        return f"explicit:{explicit}"
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(name)
        if value:
            return f"env:{name}={value}"
    return "direct-or-transparent"


def _search_proxy_argument() -> Any | None:
    """给 httpx 的 proxy 参数；None 表示不指定（交给 trust_env 决定）。"""
    explicit = _search_proxy_setting()
    if not explicit or explicit.lower() in {"direct", "none", "off"}:
        return None
    return explicit


def _search_trust_env() -> bool:
    """显式指定了「直连」时，连环境变量里的代理也不要采信。"""
    return _search_proxy_setting().lower() not in {"direct", "none", "off"}


@dataclass
class SearchBudgetTelemetry:
    max_requests: int = 4
    per_provider_timeout_seconds: float = 6.0
    overall_timeout_seconds: float = 20.0
    requests_used: int = 0
    elapsed_ms: int = 0
    early_stop_reason: str | None = None
    route: str = field(default_factory=_search_route)
    _started: float = field(default_factory=time.monotonic, repr=False)

    def remaining_seconds(self) -> float:
        return max(0.0, self.overall_timeout_seconds - (time.monotonic() - self._started))

    def can_request(self) -> bool:
        return self.requests_used < self.max_requests and self.remaining_seconds() > 0.05

    def consume(self) -> bool:
        if not self.can_request():
            return False
        self.requests_used += 1
        return True

    def public_dict(self) -> dict:
        self.elapsed_ms = int((time.monotonic() - self._started) * 1000)
        return {
            "max_requests": self.max_requests,
            "per_provider_timeout_seconds": self.per_provider_timeout_seconds,
            "overall_timeout_seconds": self.overall_timeout_seconds,
            "requests_used": self.requests_used,
            "elapsed_ms": self.elapsed_ms,
            "early_stop_reason": self.early_stop_reason,
            "route": self.route,
        }


@dataclass
class EvidenceBundle:
    query: str
    search_decision: SearchDecision
    search_executed: bool
    intent_reason: str
    status: EvidenceStatus
    source_relationship: SourceRelationship
    relationship_notes: list[str]
    conclusion_hint: str
    sources: list[EvidenceSource]
    conflicts: list[str]
    retrieved_at: str
    provider: str
    sufficient: bool
    review_disposition: EvidenceDisposition
    missing_evidence: list[str]
    retrieval_rounds: list[RetrievalRound]
    bounded_exhausted: bool
    error: str | None = None
    provider_attempts: list[ProviderAttempt] = field(default_factory=list)
    search_budget: dict = field(default_factory=dict)

    def public_dict(self) -> dict:
        return asdict(self)


class SearchProvider(Protocol):
    name: str
    async def search(self, query: str, limit: int = 8) -> list[SearchHit]: ...


class DuckDuckGoHtmlProvider:
    """Small keyless provider; replaceable without changing evidence contracts.

    HTML 和 Lite 是同一家的两个视图，HTML 被限流时 Lite 往往还答得出来。
    这段注释一直写着「主视图被挡不该阻止适配器去试它的小号回退」，
    但代码只是把 body 置空就返回了——回退从来没实现。
    2026-09-23 实测：live 那一轮这个提供方返回 0 条，而它是三个提供方里唯一
    对中文查询给出正确结果的（iloveguitar.cn 的《告白气球》C调谱、
    guangdong.chinatax.gov.cn 的定期定额申报须知），另外两个只会给跑题页面。
    所以它一旦哑掉，整轮证据就是 0 来源、UNVERIFIED。
    """

    name = "duckduckgo-html"
    endpoint = "https://html.duckduckgo.com/html/"
    lite_endpoint = "https://lite.duckduckgo.com/lite/"
    # 两个视图各自的超时必须加起来仍然小于链子的 chain_timeout_seconds，
    # 否则回退还没跑完整个提供方就被掐掉。
    #
    # 2026-09-23 实测：这个值设 2.5s 时，干净进程里一次请求要 1.9～2.0s，勉强够；
    # 但 live 服务进程里同时在跑规划器的模型流和其他请求，同一个请求稳定超过 2.5s，
    # 两个视图双双超时，整轮记录成 `duckduckgo-html EMPTY n=0 5030ms`（正好 2×2.5s）。
    # 而它是唯一给出中文对题页面的提供方，被掐掉就等于这一轮 0 来源。
    # 空跑量出来的余量不能当 live 的余量用。
    view_timeout_seconds = 5.0
    # 这个提供方自己声明的最坏情况：会依次试两个视图。链子用它算 per-plugin 超时，
    # 不再让一个固定的外层常数把它掐在半路。
    worst_case_seconds = 2 * view_timeout_seconds

    async def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; DARYA-Evidence/1.0)"}
        # 两个视图的结构不同：HTML 用 result__a / result__snippet，
        # Lite 用 result-link / result-snippet。分别解析，共用同一个命中构造。
        #
        # Lite 放前面。实测 live 服务进程里 HTML 视图经常要 4s 以上（被限流），
        # 而它是唯一给出中文对题页面的提供方，一旦被超时切断，这一轮就只剩
        # 两个 Bing 系提供方的跑题页，最终 0 来源。Lite 页更轻、通常更快回来。
        views = (
            (self.lite_endpoint, r'<a[^>]+href="([^"]+)"[^>]*class=[\'"]?result-link[\'"]?[^>]*>(.*?)</a>',
             r'<td[^>]+class=[\'"]?result-snippet[\'"]?[^>]*>(.*?)</td>'),
            (self.endpoint, r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
             r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>'),
        )
        for endpoint, anchor_pattern, snippet_pattern in views:
            body = ""
            try:
                response = await _search_client().get(
                    endpoint, params={"q": query}, timeout=self.view_timeout_seconds,
                )
                response.raise_for_status()
                body = response.text
            except httpx.HTTPError:
                continue
            anchors = list(re.finditer(anchor_pattern, body, re.I | re.S))
            snippets = re.findall(snippet_pattern, body, re.I | re.S)
            hits: list[SearchHit] = []
            for index, match in enumerate(anchors[:limit]):
                raw_url = html.unescape(match.group(1))
                parsed = urlparse(raw_url)
                if parsed.netloc.endswith("duckduckgo.com"):
                    raw_url = unquote(parse_qs(parsed.query).get("uddg", [raw_url])[0])
                title = _plain(match.group(2))
                excerpt = _plain(snippets[index]) if index < len(snippets) else ""
                host = urlparse(raw_url).netloc.lower().removeprefix("www.")
                if title and raw_url.startswith(("http://", "https://")):
                    hits.append(SearchHit(title, raw_url, excerpt[:700], host))
            if hits:
                return hits
        return []


class BingRssSearchProvider:
    """Independent keyless fallback using Bing's public RSS result format.

    This adapter is deliberately separate from the retired paid Bing Search
    API.  It consumes the XML representation of the public search surface and
    feeds the same normalization/trust pipeline as every other provider.
    """

    name = "bing-rss"
    endpoint = "https://www.bing.com/search"

    async def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        response = await _search_client().get(
            self.endpoint, params={"format": "rss", "q": query}, timeout=4,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        hits: list[SearchHit] = []
        for item in root.findall(".//item")[:limit]:
            title = _plain(item.findtext("title") or "")
            url = (item.findtext("link") or "").strip()
            excerpt = _plain(item.findtext("description") or "")[:700]
            published = (item.findtext("pubDate") or "").strip() or None
            host = urlparse(url).netloc.lower().removeprefix("www.")
            if title and url.startswith(("http://", "https://")):
                hits.append(SearchHit(title, url, excerpt, host, published))
        return hits


class TrustedSiteSearchProvider:
    """可信来源检索：cn.bing 限定权威站点，供需要高可靠来源的场合优先使用。

    默认链里的 DuckDuckGo 在部分网络环境不可达；这个 provider 使用可达的
    cn.bing.com HTML 结果，并把结果限制在站点白名单内——自由度来自白名单的
    可扩展性，可靠性来自只收录权威站点。
    """

    name = "trusted-site-bing"
    endpoint = "https://cn.bing.com/search"
    TRUSTED_SITES = (
        # 学习与科普
        "openstax.org",
        "docs.python.org",
        "python.org",
        "khanacademy.org",
        "phet.colorado.edu",
        "phys.libretexts.org",
        "britannica.com",
        "nasa.gov",
        "pep.com.cn",
        "kepu.net",
        "baike.baidu.com",
        "edu.cn",
        # 现实里的事：报税、参保、考证、登记、查企业。
        # 2026-09-23 之前这份白名单只有上面那一段，于是任何不落在教育口径里的目标
        # ——「摆摊怎么报税」「这个证去哪考」「这家公司是不是真的」——都拿不到一条
        # 可采信来源，只能以「暂未核实」作答。那不是诚实，是工具不够用。
        "gov.cn",
        "chinatax.gov.cn",
        "mohrss.gov.cn",
        "osta.org.cn",
        "cpta.com.cn",
        "samr.gov.cn",
        "gsxt.gov.cn",
        "nhc.gov.cn",
        "nmpa.gov.cn",
        "npc.gov.cn",
        "moe.gov.cn",
        "neea.edu.cn",
    )

    async def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        # cn.bing 会忽略 site: 组合运算符，所以信任边界由返回侧的白名单 host 过滤保证，
        # 而不是靠查询语法；自由度来自白名单可扩展，可靠性来自只收录权威站点。
        body = ""
        try:
            response = await _search_client().get(
                self.endpoint,
                params={"q": query, "count": str(max(limit, 8))},
                timeout=6,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
            )
            response.raise_for_status()
            body = response.text
        except httpx.HTTPError:
            return []
        anchors = list(re.finditer(
            r'<h2[^>]*><a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            body, re.I | re.S,
        ))
        snippets = re.findall(
            r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>',
            body, re.I | re.S,
        )
        hits: list[SearchHit] = []
        allowed = tuple(self.TRUSTED_SITES)
        for index, match in enumerate(anchors[: limit * 2]):
            raw_url = html.unescape(match.group(1))
            host = urlparse(raw_url).netloc.lower().removeprefix("www.")
            if not _host_matches_trusted_site(host, allowed):
                continue
            title = _plain(match.group(2))
            excerpt = _plain(snippets[index]) if index < len(snippets) else ""
            if title and raw_url.startswith(("http://", "https://")):
                hits.append(SearchHit(title, raw_url, excerpt[:700], host))
            if len(hits) >= limit:
                break
        return hits


class SearchProviderChain:
    name = "trusted-site-bing->bing-rss->duckduckgo"
    # 一轮检索里，三个提供方同时发出后最多等这么久。
    #
    # 它必须大于「单个提供方自己的最坏情况」，否则就是把那个提供方按在地上：
    # DuckDuckGo 会依次试 Lite 和 HTML 两个视图，各 2.5s，最坏 5s。
    # 2026-09-23 实测：这里设 4.0s 时，live 服务进程里每一轮都是
    # `duckduckgo-html TIMEOUT n=0 4000ms` ——正好卡在这个上限上，而它恰恰是
    # 三个提供方里唯一给出中文对题页面的那个；结果是 0 来源、UNVERIFIED，
    # 而同一个调用在干净进程里 2.5s 就能返回。我把「速度」做成了砍掉准确率。
    #
    # 现在设 11.0s：它必须大于 DuckDuckGo 自己的最坏情况（两个视图 × 5s = 10s），
    # 否则就是在掐那个唯一可用的提供方。健康时三个提供方 1～3s 就都回来了，
    # 等待会提前结束——这个数字限制的是最坏情况，不是正常耗时。
    chain_timeout_seconds = 11.0

    def __init__(
        self,
        providers: list[SearchProvider] | None = None,
        *,
        budget: SearchBudgetTelemetry | None = None,
    ):
        self.providers = providers or [
            TrustedSiteSearchProvider(),
            BingRssSearchProvider(),
            DuckDuckGoHtmlProvider(),
        ]
        self.budget = budget or SearchBudgetTelemetry()
        self.attempts: list[ProviderAttempt] = []
        self._seen: set[tuple[str, str]] = set()
        if providers:
            self.name = "->".join(item.name for item in providers)

    def has_untried(self, query: str) -> bool:
        """Whether any provider has not yet answered this exact query."""
        return any((provider.name, query) not in self._seen for provider in self.providers)

    async def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        # 逐个问是两重损失：串行地把延迟加起来，而且中间任何一个卡住，后面那个
        # ——往往才是唯一给出正确结果的那个——就再也轮不到。
        # 2026-09-23 实测：`bing-rss` 超时那一轮，`duckduckgo-html` 一次都没跑，
        # 该轮只剩 2 条命中、0 条直接来源。改成同时问、各自带上限、结果按 URL 去重合并：
        # 墙钟时间从「三者之和」变成「最慢的那个」。路由顺序仍然决定合并顺序。
        selected: list[SearchProvider] = []
        for provider in self.providers:
            key = (provider.name, query)
            if key in self._seen:
                self.attempts.append(ProviderAttempt(provider.name, query, "SKIPPED_DUPLICATE", 0, 0))
                continue
            self._seen.add(key)
            if not self.budget.consume():
                self.budget.early_stop_reason = "REQUEST_OR_TIME_BUDGET_EXHAUSTED"
                break
            selected.append(provider)
        if not selected:
            return []

        async def _call(provider: SearchProvider) -> tuple[SearchProvider, list[SearchHit], int, Exception | None]:
            started = time.monotonic()
            # per-provider 超时要尊重提供方自己声明的最坏情况，而不是一个固定常数。
            # 2026-09-23 这一天里，同一类错误咬了我三次：外层上限比内层预算小，
            # 于是那个唯一给出中文对题页面的提供方每次都在半路被掐掉，症状还每次换一个
            # （4000ms TIMEOUT / 5030ms EMPTY / 6016ms EMPTY）。
            # 声明式：提供方说它最坏要多久，链子按它给。
            budget_seconds = max(
                self.budget.per_provider_timeout_seconds,
                float(getattr(provider, "worst_case_seconds", 0.0) or 0.0),
            )
            timeout = min(budget_seconds, self.budget.remaining_seconds())
            try:
                hits = await asyncio.wait_for(provider.search(query, limit), timeout=max(0.05, timeout))
                return provider, hits, int((time.monotonic() - started) * 1000), None
            except Exception as exc:
                return provider, [], int((time.monotonic() - started) * 1000), exc

        last_error: Exception | None = None
        merged: list[SearchHit] = []
        seen_urls: set[str] = set()
        # 整条链的墙钟上限：同时发出，最多等这么久，到点就把还没回来的丢掉。
        # 这样「最快的那个提供方」决定结果、「最慢的」不再决定用户等多久——
        # 而没跑完的那个已经被发出去了，不占后面轮次的机会。
        tasks = [asyncio.ensure_future(_call(item)) for item in selected]
        done, pending = await asyncio.wait(tasks, timeout=self.chain_timeout_seconds)
        for task in pending:
            task.cancel()
        # 按路由顺序取结果，让合并顺序稳定、可复现。
        by_provider = {}
        for task in done:
            try:
                provider, hits, elapsed, exc = task.result()
            except Exception:
                continue
            by_provider[provider.name] = (provider, hits, elapsed, exc)
        for provider in selected:
            if provider.name not in by_provider:
                self.attempts.append(ProviderAttempt(
                    provider.name, query, "TIMEOUT", 0,
                    int(self.chain_timeout_seconds * 1000), "TimeoutError",
                ))
                continue
            provider, hits, elapsed, exc = by_provider[provider.name]
            if exc is None:
                self.attempts.append(ProviderAttempt(
                    provider.name, query, "SUCCESS" if hits else "EMPTY", len(hits), elapsed,
                ))
                for hit in hits:
                    if hit.url not in seen_urls:
                        seen_urls.add(hit.url)
                        merged.append(hit)
            else:
                outcome = "TIMEOUT" if isinstance(exc, asyncio.TimeoutError) else "ERROR"
                self.attempts.append(ProviderAttempt(
                    provider.name, query, outcome, 0, elapsed, type(exc).__name__,
                ))
                last_error = exc
        if merged:
            return merged
        if last_error is not None:
            raise last_error
        return []


_CURRENT = re.compile(
    r"最新|现在|目前|今年|今天|近期|政策|规则|官网|官方|公司|比赛|竞赛|招聘|岗位|要求|版本|价格|研究|证据|核实|真假|是否属实|current|latest|today|policy|official|company|competition|research|evidence",
    re.I,
)
# A short clause naming only a time or an occasion: it says when, not what.
_SITUATION_CLAUSE = re.compile(
    r"^(?:我|我们)?(?:下|这|本|上|明|去)(?:周|个月|学期|年)"
    r"|^(?:下周|明天|后天|今天|昨天|这周|最近|马上|快)"
    r"|(?:月考|期中|期末|考试|测验)$"
)
_COMMUNITY = re.compile(r"reddit|zhihu|知乎|quora|stack(?:overflow|exchange)|论坛|forum|community|小红书|微博", re.I)
_AUTHORITATIVE = re.compile(r"\.gov(?:\.|$)|\.edu(?:\.|$)|\.ac\.|\.europa\.eu$|who\.int$|un\.org$|iso\.org$|w3\.org$|ietf\.org$", re.I)
_SECONDARY = re.compile(r"reuters|apnews|bbc|nytimes|ft\.com|theguardian|caixin|财新|nature\.com", re.I)
# Chinese official institutions that live on commercial-looking domains.
#
# `_AUTHORITATIVE` recognises `.gov` and `.edu`, so it sees moe.gov.cn and any
# `.edu.cn` university, but it cannot see the Ministry of Education's own
# service platforms, because those sit on `.com.cn`. Measured consequence:
# 阳光高考 (gaokao.chsi.com.cn) was fetched for a 高考 question and then
# classified as an untrustworthy unknown, so the turn reported "缺少直接权威
# 来源" while holding the ministry's own page.
#
# A blanket ".com.cn is authoritative" rule would promote every commercial site
# in the country, so these are named individually.
_KNOWN_OFFICIAL_DOMAINS = frozenset({
    "chsi.com.cn",    # 学信网 / 阳光高考，教育部学生服务与素质发展中心
    "pep.com.cn",     # 人民教育出版社，教育部直属
    "smartedu.cn",    # 国家中小学智慧教育平台，教育部
    "ncet.edu.cn",    # 教育部教育技术与资源发展中心
})

_KNOWN_PRIMARY_DOMAINS = {
    # Audited provider bootstrap authorities.  This is a source-identity
    # allowlist, not teaching content and not a Domain Pack.
    "python.org",
    "docker.com",
}
_KNOWN_PRIMARY_REPOSITORIES = (
    "raw.githubusercontent.com/docker/docs/",
)
_INTERNAL_CONTEXT = re.compile(
    r"我的|我们的|我们当前|这个项目|当前项目|本项目|工作区|共享视角|上传的|这份文档|当前产物|刚才|前面|"
    r"my\s+(?:current\s+)?project|our\s+(?:current\s+)?project|this project|current project|workspace|"
    r"shared perspective|uploaded document|current artifact",
    re.I,
)
_EXTERNAL_MATERIAL = re.compile(
    r"官方|官网|政策|规则|比赛|竞赛|价格|研究|证据|核实|真假|是否属实|"
    r"official|policy|competition|price|research|evidence|verify|fact.check",
    re.I,
)
_OFFICIAL_REQUEST = re.compile(r"官方|官网|政策|规则|official|policy|primary source|原文", re.I)
_CORROBORATION_REQUEST = re.compile(r"研究|证据|核实|真假|是否属实|统计|数据表明|research|evidence|verify|fact.check|corroborat|statistic", re.I)
_FRESH_REQUEST = re.compile(r"最新|现在|目前|今年|今天|近期|current|latest|today|recent|updated", re.I)
_EXPERIENCE_REQUEST = re.compile(r"体验|实际经历|使用感受|经验|experience|lived|in practice", re.I)
_MAX_RETRIEVAL_ROUNDS = 3


def should_search(context: SearchContext) -> tuple[bool, str]:
    message = context.message.strip()
    has_trusted_internal_context = bool(
        context.shared_perspective or context.workspace_object or context.artifact_context
    )
    if has_trusted_internal_context and _INTERNAL_CONTEXT.search(message) and not _EXTERNAL_MATERIAL.search(message):
        return False, "可信的当前项目上下文足以回答，不需要外部检索"
    # Background goals may contain words such as “competition” for months. They
    # disambiguate a query but must not silently turn every internal question
    # into a web search. The current user message owns the search decision.
    if _CURRENT.search(message):
        return True, "当前问题含有会变化或需要外部核实的信息"
    return False, "现有上下文足以回答，不需要外部检索"


def search_shaped(text: str) -> str:
    """Rewrite what a person typed into something a search engine can answer.

    Two shapes have to survive here, and the difference was learned by
    measurement rather than assumed:

    *A question about a thing* -- "Python 闭包是什么". Stripping the question
    words gives "Python 闭包", which returns six direct sources where the
    original returned Python homepages.

    *A spoken sentence* -- "我今年想在杭州落户，应届本科生，需要什么条件？
    社保要交多久？我想知道今年最新的政策。" Trimming the edges does nothing
    here, because the question words are in the middle and the subject is buried
    in a clause. Sent to a search engine whole, it latched onto the leading
    "我" and returned 百度百科 pages for the Chinese character 我 and for a
    张国荣 song. So this shape is treated as extraction: drop the framing
    clauses, keep the content-bearing ones.

    The subject is never paraphrased -- only framing, question words and
    sentence particles come out.
    """
    original = " ".join(str(text or "").split())
    if not original:
        return original

    leading = re.compile(
        r"^(?:请问|请帮我|帮我|麻烦|请|我想知道|想知道|告诉我|能不能|可以帮我|可以|看看|看下|讲讲|说说)"
    )
    framing = re.compile(r"^(?:什么是|什么叫|啥是|啥叫)")
    # "我今年想在" / "我想知道" / "我们打算去" -- the speaker, not the subject.
    speaker = re.compile(
        r"^(?:我|我们|你|您|他|她|他们)"
        r"(?:今年|明年|去年|现在|目前|最近)?"
        r"(?:想知道|想了解|知道|了解|想|要|打算|准备|需要|想要|希望|计划)?"
        r"(?:在|去|到|把|给)?"
    )
    # A question tail hanging off a content clause: "社保要交多久" -> "社保".
    question_tail = re.compile(
        r"(?:要|需要|得|应该|可以)?(?:交|办|准备|提供|满足)?"
        r"(?:多久|多长时间|多少时间|什么条件|什么材料|什么手续|什么要求|哪些条件|哪些材料"
        r"|是什么时候|是几号|是哪天|什么时候"
        r"|多少|哪些|什么|怎么|怎样|如何|是什么|是啥|吗|呢|吧)"
        r"(?:的)?(?:时间|条件|材料|手续|要求|政策|东西|流程)?$"
    )
    dot = " ，,。.、；;：:！!~～"
    only_question = re.compile(r"^[吗呢吧啊呀？?。.，,！!]*$")

    def clean(chunk: str) -> str:
        value = chunk.strip(dot)
        for _ in range(6):
            stripped = framing.sub(" ", leading.sub(" ", speaker.sub(" ", value))).strip(dot)
            if stripped == value:
                break
            value = stripped
        value = question_tail.sub("", value).strip(dot)
        # The learner describing themselves is not part of the subject:
        # "物理电磁感应这一章我完全不入门" ranks nothing, "物理电磁感应这一章"
        # finds the chapter. Cut the self-description off the end of a clause.
        value = re.sub(
            r"(?:我|本人|你|他|她)(?:完全|也|都|反正|基础|一点|真的)?"
            r"(?:不|没)(?:太)?(?:入门|会做|会|懂|熟悉|了解|清楚|明白|行|会做)"
            r".*$",
            "",
            value,
        ).strip(dot)
        value = re.sub(r"(?:是不是|有没有)$", "", value).strip(dot)
        return " ".join(value.split())

    clauses = [part for part in re.split(r"[，,。.；;！!？?、]+", original) if part.strip()]
    kept = [clean(part) for part in clauses]
    kept = [part for part in kept if len(part) >= 2 and not only_question.match(part)]

    if not kept:
        return original
    # A short clause next to long ones is framing ("我是高三的"), not a subject.
    if any(len(part) >= 8 for part in kept):
        kept = [part for part in kept if len(part) >= 5] or kept
    # A short clause that is only a time or an occasion ("下周三月考") describes
    # when it hurts, not what it is about. Long clauses are left alone even when
    # they open with a date: "明年高考报名时间" is a real query.
    if any(len(part) >= 8 for part in kept):
        kept = [
            part for part in kept
            if not (len(part) <= 6 and _SITUATION_CLAUSE.search(part))
        ] or kept
    # Search engines degrade on long queries, and a spoken sentence runs on.
    # Keep the leading clauses -- the subject usually arrives first -- and stop
    # before the tail of the story.
    trimmed: list[str] = []
    length = 0
    for part in kept:
        if trimmed and length + len(part) > 24:
            break
        trimmed.append(part)
        length += len(part) + 1
    shaped = " ".join(trimmed)
    # A single clause that lost everything but its question is not a query.
    return shaped if len(shaped) >= 2 else original


def build_search_query(context: SearchContext) -> str:
    # Whether the request stands on its own is a property of what the learner
    # said, not of how the string was reshaped for a search engine. Measuring it
    # after reshaping let a specific three-part question be trimmed below the
    # threshold and inherit an unrelated old learning goal.
    raw = re.sub(r"\s+", " ", context.query_override or context.message).strip()
    message = search_shaped(raw)
    personalized = bool(re.search(
        r"适合我|按我的|根据我的|结合我的|为我|我现在的|我目前的|"
        r"for me|my (?:current )?(?:level|background|situation|goal)",
        message,
        re.I,
    ))
    if context.preferred_urls:
        # A topic-specific audited bootstrap means the current message already
        # identifies the subject.  Appending an older learning goal can turn a
        # Docker query into "Docker + closures" and poison provider fallback.
        if not personalized:
            return message[:120]
        goal = str(context.shared_perspective.get("goal") or "").strip()
        purpose = str(context.workspace_object.get("purpose") or "").strip()
        additions = [value[:24] for value in (goal, purpose) if value and value not in message]
        return " ".join([message[:88], *additions])[:120]
    if len(raw) >= 24 and not personalized:
        # A complete, specific request owns its retrieval scope.  Historical
        # goals are useful for short/deictic queries, but appending one here can
        # make a later conflict or failure check echo an unrelated old topic.
        return message[:120]
    goal = str(context.shared_perspective.get("goal") or "").strip()
    purpose = str(context.workspace_object.get("purpose") or "").strip()
    additions = [value[:24] for value in (goal, purpose) if value and value not in message]
    # Search engines become less useful when the whole workspace narrative is
    # used as a query. Context disambiguates, but never overwhelms the need.
    return " ".join([message[:88], *additions])[:120]


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def _host_matches_trusted_site(host: str, sites: tuple[str, ...]) -> bool:
    """站点白名单按域名后缀匹配，不做子串包含。

    以前这里还有一条 `site in host`，于是任何把白名单域名当子串拼进去的主机
    （`gov.cn.example.com`、`eviledu.cn`）都能混进可信来源。既然这份名单现在要
    承担报税、考证、查企业这类现实问题的出处，就不能再留这个口子。
    """
    normalized = str(host or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    return any(
        normalized == site or normalized.endswith("." + site)
        for site in sites
    )


_EXPLICIT_URL = re.compile(r"https?://[^\s<>\]\)\uff09\u3002\uff0c,]+", re.I)


async def _is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password or parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        rows = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError:
        return False
    addresses = {row[4][0].split("%", 1)[0] for row in rows}
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(value).is_global for value in addresses)
    except ValueError:
        return False


async def _fetch_explicit_source(
    url: str,
    *,
    budget: SearchBudgetTelemetry,
    attempts: list[ProviderAttempt],
    query: str = "",
) -> SearchHit | None:
    """Fetch only a user-supplied public URL, with redirect and size bounds."""
    current = url.rstrip(".,")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DARYA-Evidence/1.0)"}
    transient_failures = 0
    for _ in range(3):
        if not await _is_safe_public_url(current):
            attempts.append(ProviderAttempt("direct-source", current, "REJECTED_UNSAFE", 0, 0))
            return None
        if not budget.consume():
            budget.early_stop_reason = "REQUEST_OR_TIME_BUDGET_EXHAUSTED"
            return None
        started = time.monotonic()
        timeout = min(budget.per_provider_timeout_seconds, budget.remaining_seconds())
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers) as client:
                response = await asyncio.wait_for(client.get(current), timeout=max(0.05, timeout))
            elapsed = int((time.monotonic() - started) * 1000)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                attempts.append(ProviderAttempt("direct-source", current, "REDIRECT", 0, elapsed))
                if not location:
                    return None
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not any(kind in content_type for kind in ("text/html", "text/plain", "application/json", "application/xml", "text/xml")):
                attempts.append(ProviderAttempt("direct-source", current, "REJECTED_CONTENT_TYPE", 0, elapsed, content_type[:80]))
                return None
            # Modern official documentation pages may include a sizeable
            # server-rendered navigation payload.  Keep the network/body bound
            # explicit while allowing the audited Python/Docker authorities to
            # be read.  The retained evidence excerpt remains much smaller.
            if len(response.content) > 2_097_152:
                attempts.append(ProviderAttempt("direct-source", current, "REJECTED_SIZE", 0, elapsed))
                return None
            body = response.text[:512_000]
            title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
            markdown_title_match = re.search(r"(?m)^title:\s*(.+?)\s*$", body)
            meta_match = re.search(
                r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']',
                body, re.I | re.S,
            )
            title = _plain(
                title_match.group(1)
                if title_match else markdown_title_match.group(1)
                if markdown_title_match else urlparse(current).netloc
            )[:300]
            published_match = re.search(
                r'<meta[^>]+(?:name|property)=["\'](?:article:modified_time|article:published_time|date|last-modified)["\'][^>]+content=["\'](.*?)["\']',
                body, re.I | re.S,
            ) or re.search(r'<time[^>]+datetime=["\'](.*?)["\']', body, re.I | re.S)
            body_without_noise = re.sub(
                r"(?is)<(script|style|noscript|svg).*?>.*?</\1>", " ", body,
            )
            body_plain = _plain(body_without_noise)
            meta_excerpt = _plain(meta_match.group(1) if meta_match else "")
            excerpt = _focused_excerpt(
                " ".join(part for part in (title, meta_excerpt, body_plain) if part),
                query=query,
            )
            attempts.append(ProviderAttempt("direct-source", current, "SUCCESS", 1, elapsed))
            return SearchHit(
                title=title or current,
                url=current,
                excerpt=excerpt,
                source=urlparse(current).netloc.lower().removeprefix("www."),
                published_at=(published_match.group(1).strip()[:80] if published_match else response.headers.get("last-modified")),
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - started) * 1000)
            attempts.append(ProviderAttempt("direct-source", current, "TIMEOUT", 0, elapsed, "TimeoutError"))
            if transient_failures == 0 and budget.can_request():
                transient_failures += 1
                continue
            return None
        except Exception as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            attempts.append(ProviderAttempt("direct-source", current, "ERROR", 0, elapsed, type(exc).__name__))
            if transient_failures == 0 and budget.can_request():
                transient_failures += 1
                continue
            return None
    return None


def _focused_excerpt(value: str, *, query: str, limit: int = 1800) -> str:
    """Keep a bounded live-page passage near the user's subject.

    Search snippets are often too shallow for a current version or open-topic
    claim.  This helper does not summarize or invent; it selects verbatim plain
    page text around the first useful query anchor.
    """
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    query_tokens = [
        token.lower() for token in re.findall(r"[a-z0-9][a-z0-9.+#-]{2,}|[\u4e00-\u9fff]{2,}", query or "")
        if token.lower() not in _DIRECTNESS_STOP
    ]
    lowered = text.lower()
    positions = [
        match.start()
        for token in query_tokens
        for match in re.finditer(re.escape(token), lowered)
    ]
    # Documentation headers and navigation often repeat the product name long
    # before the relevant paragraph. Choose the window with the densest set of
    # current-query terms so the retained passage is claim evidence, not chrome.
    scope_requested = bool(re.search(r"\bscope\b|适用范围|默认|\bdefault\b", query or "", re.I))

    def window_score(anchor: int) -> tuple[int, int, int, int]:
        start = max(0, anchor - 300)
        window = lowered[start:start + limit]
        return (
            int(scope_requested and bool(re.search(
                r"\bdifferences?\s+between\b|\bwhereas\b|\bunlike\b|区别|不同|相比",
                window,
                re.I,
            ))),
            sum(bool(re.search(pattern, window, re.I)) for pattern in (
                r"\bdefault\b|默认",
                r"\buser[- ]defined\b|\bcustom\b|用户定义|自定义",
                r"\blegacy\b|旧版|历史",
                r"\bcurrent\b|当前|现行",
            )) if scope_requested else 0,
            len({token for token in query_tokens if token in window}),
            sum(window.count(token) for token in query_tokens),
        )

    anchor = max(positions, key=window_score) if positions else 0
    start = max(0, anchor - 220)
    return text[start:start + limit]


def _source_type(hit: SearchHit) -> tuple[SourceType, str]:
    host = hit.source.lower().removeprefix("www.").rstrip(".")
    title = hit.title.lower()
    if _COMMUNITY.search(host + " " + title):
        return "community", "社区或个人经验，只能说明体验，不能替代官方事实"
    if _AUTHORITATIVE.search(host) or any(
        host == domain or host.endswith("." + domain) for domain in _KNOWN_OFFICIAL_DOMAINS
    ):
        return "authoritative", "政府、教育、标准或权威机构来源"
    if _SECONDARY.search(host):
        return "secondary", "有编辑责任的专业二手来源"
    if any(host == domain or host.endswith("." + domain) for domain in _KNOWN_PRIMARY_DOMAINS):
        return "primary", "已核验的一手发布域名，与页面主题直接相关"
    if any(hit.url.lower().startswith("https://" + prefix) for prefix in _KNOWN_PRIMARY_REPOSITORIES):
        return "primary", "已核验的官方源文档仓库，与发布页面内容直接对应"
    # Never promote a site merely because its own brand appears in the title.
    # Look-alike documentation domains and SEO pages must remain untrusted
    # until an audited source-identity rule is added.
    return "unknown", "来源类型尚不能可靠判断，需要交叉核实"


def _canonical(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.netloc.lower().removeprefix('www.')}{path.lower()}"


def normalize_hits(hits: list[SearchHit], retrieved_at: str, include_community: bool = True) -> list[EvidenceSource]:
    seen_urls: set[str] = set()
    seen_fingerprints: set[str] = set()
    result: list[EvidenceSource] = []
    for hit in hits:
        key = _canonical(hit.url)
        fingerprint = re.sub(r"\W+", "", (hit.title + hit.excerpt).lower())[:160]
        if key in seen_urls or (fingerprint and fingerprint in seen_fingerprints):
            continue
        seen_urls.add(key)
        seen_fingerprints.add(fingerprint)
        source_type, reason = _source_type(hit)
        if source_type == "community" and not include_community:
            continue
        claim_type: ClaimType = "EXPERIENCE" if source_type == "community" else "FACT"
        result.append(EvidenceSource(
            title=hit.title, url=hit.url, source=hit.source, excerpt=hit.excerpt,
            source_type=source_type, trust_reason=reason, retrieved_at=retrieved_at,
            published_at=hit.published_at, claim_type=claim_type,
        ))
    priority = {"primary": 0, "authoritative": 1, "secondary": 2, "community": 3, "unknown": 4}
    return sorted(result, key=lambda item: priority[item.source_type])


_QUESTION_ONLY = re.compile(
    r"(?:[?？]\s*$)|^(?:how|what|why|when|where|who|does|do|is|are|can|could|should|will)\b|"
    r"^(?:是否|什么|为何|为什么|怎么|如何|能否|会不会)",
    re.I,
)
_NEGATIVE = re.compile(
    r"\b(?:not|no|never|cannot|can't|won't|isn't|aren't|without|prohibited|unsupported|optional)\b|"
    r"不允许|不支持|不可|禁止|无需|不需要|未删除|没有删除|不会|不能|没有|并非",
    re.I,
)
_ASSERTION = re.compile(
    r"\b(?:allow|allowed|available|eligible|required|require|retain|retained|store|stored|"
    r"delete|deleted|support|supported|train|trained|must|will|is|are)\b|"
    r"允许|支持|必须|需要|保留|存储|删除|训练|可以|已经|仍然",
    re.I,
)
_STOP_WORDS = {
    "about", "after", "before", "being", "data", "does", "from", "have", "more",
    "official", "policy", "source", "that", "their", "this", "what", "when", "where",
    "which", "with", "your", "experience", "question", "information",
}


def _claim_text(source: EvidenceSource) -> str:
    return f"{source.title}. {source.excerpt}".strip()


def _is_question_only(source: EvidenceSource) -> bool:
    text = _claim_text(source).strip()
    title = source.title.strip()
    # Search snippets often repeat a question and then mention a possible
    # condition ("is it retained ... or not?").  That condition is not an
    # asserted counter-claim.  Prefer a false negative over inventing conflict.
    framed_as_question = (
        (any(mark in title for mark in ("?", "？")) and any(mark in source.excerpt for mark in ("?", "？")))
        or bool(_QUESTION_ONLY.search(title))
    )
    return (framed_as_question and not bool(re.search(r"\b(?:answer|confirmed|we found)\b|回答|证实", source.excerpt, re.I))) or (
        text.endswith(("?", "？")) and not bool(_ASSERTION.search(source.excerpt))
    )


def _topic_tokens(source: EvidenceSource) -> set[str]:
    words = re.findall(r"[a-z0-9]{4,}|[\u4e00-\u9fff]{2,}", _claim_text(source).lower())
    return {word for word in words if word not in _STOP_WORDS}


def _comparable(left: EvidenceSource, right: EvidenceSource) -> bool:
    return bool(_topic_tokens(left) & _topic_tokens(right))


_MAX_STORAGE_PATTERNS = (
    re.compile(r"\b(?:deleted?|removed?)\s+(?:no later than|within|by|before)\s+(\d+)\s+days?\b", re.I),
    re.compile(r"\b(?:retained?|stored?)\s+(?:for\s+)?(?:at most|no more than|exactly)\s+(\d+)\s+days?\b", re.I),
)
_PRESENT_AFTER_PATTERNS = (
    re.compile(r"\b(?:still\s+)?(?:present|visible|available|stored|retained)\b[^.]{0,80}\bafter\s+(\d+)\s+days?\b", re.I),
    re.compile(r"\b(?:remained?|remain)\s+(?:present|visible|available|stored|retained)\b[^.]{0,80}\bafter\s+(\d+)\s+days?\b", re.I),
    re.compile(r"\bafter\s+(\d+)\s+days?\b[^.]{0,80}\b(?:still\s+)?(?:present|visible|available|stored|retained|not\s+deleted)\b", re.I),
)
_NEGATIVE_REQUIREMENT = re.compile(r"\b(?:not|no)\s+(?:\w+\s+){0,4}required\b|\boptional\b", re.I)
_POSITIVE_REQUIREMENT = re.compile(r"\brequires?\b|\brequired\b", re.I)
_NEGATIVE_PERMISSION = re.compile(r"\bprohibited\b|\bnot\s+allowed\b|\bcannot\b|\bcan't\b", re.I)
_POSITIVE_PERMISSION = re.compile(r"\ballowed?\b|\bpermitted?\b|\bcan\b", re.I)


def _captured_days(patterns: tuple[re.Pattern, ...], text: str) -> list[int]:
    return [int(match.group(1)) for pattern in patterns for match in pattern.finditer(text)]


def _has_unqualified(pattern: re.Pattern, excluded: re.Pattern, text: str) -> bool:
    return bool(pattern.search(excluded.sub(" ", text)))


def _qualified_constraints_conflict(left_text: str, right_text: str) -> bool:
    """Recognize only a few contradictions whose constraints cannot overlap.

    This deliberately does not infer an upper bound from "at least N" or a
    universal rule from "may"/"sometimes". Unknown cases remain non-conflict.
    """
    left_max = _captured_days(_MAX_STORAGE_PATTERNS, left_text)
    right_max = _captured_days(_MAX_STORAGE_PATTERNS, right_text)
    left_present_after = _captured_days(_PRESENT_AFTER_PATTERNS, left_text)
    right_present_after = _captured_days(_PRESENT_AFTER_PATTERNS, right_text)
    if any(present >= maximum for maximum in left_max for present in right_present_after):
        return True
    if any(present >= maximum for maximum in right_max for present in left_present_after):
        return True

    left_required = _has_unqualified(_POSITIVE_REQUIREMENT, _NEGATIVE_REQUIREMENT, left_text)
    right_required = _has_unqualified(_POSITIVE_REQUIREMENT, _NEGATIVE_REQUIREMENT, right_text)
    left_not_required = bool(_NEGATIVE_REQUIREMENT.search(left_text))
    right_not_required = bool(_NEGATIVE_REQUIREMENT.search(right_text))
    if (left_required and right_not_required) or (right_required and left_not_required):
        return True

    left_allowed = _has_unqualified(_POSITIVE_PERMISSION, _NEGATIVE_PERMISSION, left_text)
    right_allowed = _has_unqualified(_POSITIVE_PERMISSION, _NEGATIVE_PERMISSION, right_text)
    left_prohibited = bool(_NEGATIVE_PERMISSION.search(left_text))
    right_prohibited = bool(_NEGATIVE_PERMISSION.search(right_text))
    return (left_allowed and right_prohibited) or (right_allowed and left_prohibited)


def _materially_disagrees(left: EvidenceSource, right: EvidenceSource) -> bool:
    left_text = _claim_text(left)
    right_text = _claim_text(right)
    if not _comparable(left, right) or _is_question_only(left) or _is_question_only(right):
        return False
    # Lexical opposition (deleted/stored, must/may, always/sometimes) is not
    # enough. Only declare conflict when the supported qualifier constraints
    # cannot both hold. Prefer a false negative when scope is uncertain.
    return _qualified_constraints_conflict(left_text, right_text)


def detect_source_relationship(sources: list[EvidenceSource]) -> tuple[SourceRelationship, list[str], list[str]]:
    """Classify only explainable pair relationships; no semantic claim graph."""
    comparable_pairs: list[tuple[EvidenceSource, EvidenceSource]] = []
    different_layer_pairs: list[tuple[EvidenceSource, EvidenceSource]] = []
    for index, left in enumerate(sources):
        for right in sources[index + 1:]:
            if _is_question_only(left) or _is_question_only(right):
                continue
            if not _comparable(left, right):
                continue
            comparable_pairs.append((left, right))
            if _materially_disagrees(left, right):
                note = f"{left.source} 与 {right.source} 对可比条件给出相反陈述。"
                return "CONFLICTING", [note], [note]
            if {left.source_type, right.source_type} & {"community"} and (
                left.source_type in {"primary", "authoritative"}
                or right.source_type in {"primary", "authoritative"}
            ):
                different_layer_pairs.append((left, right))
    if different_layer_pairs:
        return (
            "DIFFERENT_LAYER",
            ["官方/权威来源说明规则，社区来源说明体验；二者可同时有用，但不被当作互相否定。"],
            [],
        )
    return (
        "NOT_COMPARABLE",
        ["现有来源没有形成可直接比较的相反主张。"],
        [],
    )


def _conflicting_source_urls(sources: list[EvidenceSource]) -> set[str]:
    urls: set[str] = set()
    for index, left in enumerate(sources):
        for right in sources[index + 1:]:
            if _materially_disagrees(left, right):
                urls.update((left.url, right.url))
    return urls


def _select_useful_sources(sources: list[EvidenceSource], include_community: bool, limit: int = 3) -> list[EvidenceSource]:
    if not include_community:
        return sources[:limit]
    community = next((source for source in sources if source.source_type == "community"), None)
    selected = [source for source in sources if source.source_type != "community"][: limit - (1 if community else 0)]
    if community:
        selected.append(community)
    return selected[:limit]


@dataclass
class EvidenceAssessment:
    sufficient: bool
    disposition: EvidenceDisposition
    reason: str
    missing_evidence: list[str]


_DIRECTNESS_STOP = {
    "current", "latest", "today", "recent", "official", "policy", "evidence",
    "verify", "research", "source", "rules", "rule", "官网", "官方", "政策",
    "规则", "最新", "现在", "目前", "今年", "今天", "近期", "证据", "核实",
    # Generic CJK instruction/claim framing pairs.  These cannot establish
    # topical relevance on their own (for example, “两个/必须” must not match
    # an unrelated encyclopedia page when the user reports two conflicting
    # application claims without naming the application authority).
    "两个", "说法", "冲突", "申请", "附件", "必须", "提交", "不需", "需要",
    "核对", "哪个", "查不", "不到", "明确", "如果", "联网", "当前", "官方",
    "版本", "什么", "怎么", "想学", "基础", "我的", "我们", "帮我", "用户",
    "自己", "一个", "一份", "直接", "给我", "可以", "开始", "计划", "材料",
}


def _evidence_terms(value: str) -> set[str]:
    text = (value or "").lower()
    terms = set(re.findall(r"[a-z0-9]{3,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return {term for term in terms if term not in _DIRECTNESS_STOP}


def _matched_cjk_concept_groups(question: str, claim: str) -> int:
    """Count independently matched Chinese clauses, not overlapping bigrams.

    Two generic bigrams from one entity used to make a city homepage look like
    direct evidence for a multi-part education decision.  Clause groups keep
    entity relevance separate from task/claim relevance without a domain list.
    """
    claim_lower = (claim or "").lower()
    groups = []
    for chunk in re.split(r"[，。；、：！？,.!?;:\s]+", question or ""):
        run = "".join(re.findall(r"[\u4e00-\u9fff]", chunk))
        if len(run) < 3:
            continue
        anchors = {
            run[index:index + 3]
            for index in range(len(run) - 2)
            if run[index:index + 2] not in _DIRECTNESS_STOP
        }
        if anchors:
            groups.append(anchors)
    return sum(1 for anchors in groups if any(anchor in claim_lower for anchor in anchors))


_SOURCE_GRADER_SYSTEM = (
    "你是 DARYA 的资料判读员。给你一位用户此刻的处境、他想达成的事，以及一批刚检索回来的网页标题与摘要。"
    "你要判断的是：**这个页面能不能帮到这个人**。"
    "不要用「查询词有没有出现在页面里」来判，也不要用关键词重合来判："
    "用户问的是一件事，能帮到他的可能是另一件相邻的事——他问「下个月15号要报税」，"
    "而一篇讲「电子税务局怎么查自己的征收方式」的页面完全不含「15号」，它照样是他最需要的东西。"
    "反过来，标题里堆满用户原话却没有任何可执行信息的页面（目录页、首页、广告、问答聚合）不算有用。"
    "判据：这个页面的内容能不能让这个人少走一步、少问一次、少犯一个错，或替他确认一个会改变做法的条件。"
    "不确定就判 false，宁可少收也不要把没用的东西塞进证据。"
)


def _grade_prompt(context: "SearchContext", candidates: list["EvidenceSource"]) -> str:
    goal = str(context.shared_perspective.get("goal") or "").strip()
    purpose = str(context.workspace_object.get("purpose") or "").strip()
    situation = "；".join(
        value for value in (goal, purpose) if value
    ) or "（本轮没有单独记录目标）"
    rows = [
        {
            "id": index,
            "host": urlparse(source.url).netloc.lower().removeprefix("www."),
            "title": str(source.title or "")[:120],
            "excerpt": str(source.excerpt or "")[:280],
        }
        for index, source in enumerate(candidates)
    ]
    return (
        f"用户此刻的处境与原话：\n{context.message[:900]}\n\n"
        f"他要达成的事：{situation[:300]}\n\n"
        f"候选页面：\n{json.dumps(rows, ensure_ascii=False)}\n\n"
        '只输出 JSON：{"useful":[{"id":0,"why":"一句话说明它能帮上什么"}]}。'
        "只列真正有用的 id；一个都没有就写 {\"useful\":[]}。不要输出 JSON 以外的字符。"
    )


async def grade_sources_for_goal(
    context: "SearchContext",
    candidates: list["EvidenceSource"],
    *,
    llm: Any | None = None,
    max_candidates: int = 16,
) -> set[str]:
    """让模型按「能不能帮到这个人」挑出可用来源，返回它们的 URL。

    词面判据（`_is_direct_for_context` 的术语交集）会把真正有用的页面判成无关：
    它量的是「查询词有没有出现在正文里」，而用户要的是「这条内容能不能解决我的事」。
    两者不是一回事——实测里模型写好的查询取回了对题页面，却因为正文用词与查询词
    不完全一致而被整批丢弃，最终 0 来源、只能标「待核实」。

    这里补上按帮助性判读的那一步（业界叫 document grading：逐条让模型判相关性，
    而不是靠关键词过滤）。它只在便宜的词面判据没挑够时才跑，所以正常轮次不额外花时间。
    判读失败返回空集合，调用方照旧走原来的词面判据，不引入新的失败模式。

    送进来之前会按「与查询有没有任何共同词」重排一次，只是为了让上限截断时留下的
    是更有希望的那批——**判读本身仍然只看能不能帮到用户**，这一步不参与判定。
    2026-09-23 实测：不重排时合并顺序由提供方决定，最有用的几个排在末尾，
    被上限截在门外，判读只能看到一堆跑题页，于是正确答案是「一个都没用」。
    """
    if not candidates:
        return set()
    try:
        from services.llm_service_real import extract_llm_text, get_llm_service

        if llm is None:
            llm = await get_llm_service()
        scope_terms = _evidence_terms(search_shaped(context.query_override or context.message))

        def prior(source: "EvidenceSource") -> int:
            if not scope_terms:
                return 0
            return len(scope_terms & _evidence_terms(_claim_text(source)))

        ordered = sorted(candidates, key=prior, reverse=True)
        picked = ordered[:max_candidates]
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": _SOURCE_GRADER_SYSTEM},
                {"role": "user", "content": _grade_prompt(context, picked)},
            ],
            temperature=0.0,
            max_tokens=600,
            use_cache=False,
            write_cache=False,
            response_format={"type": "json_object"},
        )
        text = extract_llm_text(raw)
        match = re.search(r"\{.*\}", text or "", re.S)
        if not match:
            return set()
        parsed = json.loads(match.group(0))
        useful = parsed.get("useful")
        if not isinstance(useful, list):
            return set()
        urls: set[str] = set()
        for row in useful:
            try:
                index = int(dict(row).get("id"))
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(picked):
                urls.add(picked[index].url)
        return urls
    except Exception:
        return set()


def _covers_public_decision_subject(question: str, claim: str) -> bool:
    """Require the source to name the actual high-stakes decision subject.

    Shared location or generic planning words are not enough to turn a fiscal
    report into evidence for a civil-service position decision, or a campus
    homepage into evidence for postgraduate admissions.
    """
    question_text = str(question or "")
    claim_text = str(claim or "")
    gates = (
        (r"考公|公务员|省考|国考|事业编|事业单位|职位表|选岗", r"公务员|省考|国考|事业编|事业单位|职位表|招录|招考"),
        (r"考研|研究生|择校|招生专业|复试", r"考研|研究生|硕士|招生|复试|专业目录"),
        (r"就业|求职|校招|招聘|找工作", r"就业|求职|校招|招聘|岗位|职位"),
    )
    for question_pattern, claim_pattern in gates:
        if re.search(question_pattern, question_text) and not re.search(claim_pattern, claim_text):
            return False
    return True


def _is_direct_for_context(context: SearchContext, source: EvidenceSource) -> bool:
    if _is_question_only(source):
        return False
    if source.source_type == "primary" and any(
        _canonical(source.url) == _canonical(preferred_url)
        for preferred_url in context.preferred_urls
    ):
        # The planner's preferred URL is an audited, topic-specific provider
        # bootstrap.  An exact live fetch of that URL is direct evidence even
        # when the user and source use different languages (for example,
        # Chinese "容器网络" and English "Networking overview").
        return True
    # Same shaping as the query itself: the words used to test whether a
    # source is on-topic have to be the subject, not the question.
    relevance_scope = search_shaped(context.query_override or context.message)
    needed = _evidence_terms(relevance_scope)
    if not needed:
        return source.source_type in {"primary", "authoritative"}
    if not _covers_public_decision_subject(relevance_scope, _claim_text(source)):
        return False
    overlap = needed & _evidence_terms(_claim_text(source))
    cjk_groups = _matched_cjk_concept_groups(relevance_scope, _claim_text(source))
    if (
        overlap
        and source.source_type in {"primary", "authoritative"}
        and _OFFICIAL_REQUEST.search(source.title)
    ):
        return True
    # A homepage sharing only an organization name is not direct evidence for
    # a multi-part requirements/version claim. Short claims can still match on
    # one material term; longer claims need at least two independent anchors.
    minimum = 1 if len(needed) <= 2 else 2
    if len(re.findall(r"[\u4e00-\u9fff]", relevance_scope)) >= 12 and cjk_groups < 2:
        return False
    return len(overlap) >= minimum


def assess_evidence_sufficiency(
    context: SearchContext,
    sources: list[EvidenceSource],
    relationship: SourceRelationship,
    graded_useful: set[str] | None = None,
) -> EvidenceAssessment:
    """Apply an explainable, claim-sensitive stop/continue decision.

    This is the evidence-layer counterpart of the accepted bounded Quality Gate:
    it can request FURTHER_RETRIEVAL, but it is not a new reviewer or agent.

    `graded_useful` 是按帮助性判读挑出来的 URL（见 grade_sources_for_goal）。
    它们和词面判据挑出的来源一样计入「直接来源」——量的是同一条标准
    （这条内容能不能解决用户的事），只是判读方式从词面换成了模型。
    """
    direct = [
        source for source in sources
        if _is_direct_for_context(context, source)
        or (graded_useful and source.url in graded_useful)
    ]
    strong = [source for source in direct if source.source_type in {"primary", "authoritative"}]
    credible = [source for source in direct if source.source_type in {"primary", "authoritative", "secondary"}]
    communities = [source for source in direct if source.source_type == "community"]
    credible_domains = {urlparse(source.url).netloc.lower().removeprefix("www.") for source in credible}
    message = context.message
    wants_official = bool(_OFFICIAL_REQUEST.search(message))
    wants_community = bool(_COMMUNITY.search(message) and _EXPERIENCE_REQUEST.search(message))
    wants_corroboration = bool(_CORROBORATION_REQUEST.search(message)) and not wants_official
    wants_fresh = bool(_FRESH_REQUEST.search(message))
    missing: list[str] = []

    if relationship == "CONFLICTING":
        missing.append("需要针对冲突条件、时间或适用范围继续查找澄清")
    if wants_official and not strong:
        missing.append("缺少直接支持该主张的一手或权威来源")
    elif wants_community and not wants_official and len(communities) < 2:
        missing.append("缺少第二个独立的实际体验来源")
    elif wants_corroboration and len(credible_domains) < 2:
        missing.append("缺少独立来源的交叉印证")
    elif not wants_official and not wants_community and not wants_corroboration:
        if not strong and len(credible_domains) < 2:
            missing.append("缺少直接权威来源或两个独立可信来源")

    if wants_official and wants_community and not communities:
        missing.append("缺少可与官方规则分层比较的实际体验来源")

    # For time-sensitive claims, a direct primary/authoritative source is an
    # acceptable freshness anchor. Secondary-only evidence needs an explicit
    # date signal; absent dates stay absent rather than being invented.
    if wants_fresh and not strong:
        current_year = str(datetime.now(timezone.utc).year)
        dated = any(
            source.published_at or current_year in _claim_text(source)
            for source in credible
        )
        if not dated:
            missing.append("缺少可判断时效性的发布日期、更新时间或当前权威来源")

    if not direct and sources:
        missing.append("现有来源与用户所问主张不够直接")
    if not sources:
        missing.append("尚未取得可用来源")

    missing = list(dict.fromkeys(missing))
    if missing:
        return EvidenceAssessment(
            sufficient=False,
            disposition="FURTHER_RETRIEVAL",
            reason="当前证据尚未覆盖：" + "；".join(missing),
            missing_evidence=missing,
        )
    if strong:
        reason = "直接的一手或权威来源已覆盖所问主张，达到回答阈值"
    elif len(credible_domains) >= 2:
        reason = "两个独立可信来源已直接覆盖所问主张，达到交叉印证阈值"
    else:
        reason = "独立体验来源已覆盖用户请求的经验层问题"
    return EvidenceAssessment(True, "ANSWER", reason, [])


def _next_retrieval_query(
    base_query: str,
    assessment: EvidenceAssessment,
    round_number: int,
    *,
    provider_failed: bool = False,
) -> tuple[str, str, str]:
    chinese = bool(re.search(r"[\u4e00-\u9fff]", base_query))
    missing = " ".join(assessment.missing_evidence)
    if provider_failed:
        strategy = "RETRY_WITH_FOCUSED_QUERY"
        suffix = "权威来源 原始文件" if chinese else "authoritative source primary document"
        reason = "上一轮提供方未返回可用结果，改用更聚焦的权威来源查询"
    elif "冲突" in missing:
        strategy = "TARGET_CONFLICT_CLARIFICATION"
        suffix = "官方澄清 适用范围 时间条件" if chinese else "official clarification scope effective date"
        reason = "上一轮存在可比冲突，定向查找适用范围与时间条件的澄清"
    elif "一手" in missing or "权威" in missing:
        strategy = "FIND_PRIMARY_SOURCE"
        suffix = "官方原文 最新文件" if chinese else "official primary source current document"
        reason = "上一轮缺少一手或权威来源，改查原始发布主体"
    elif "交叉" in missing or "第二个" in missing:
        strategy = "INDEPENDENT_CORROBORATION"
        suffix = "独立来源 交叉核实" if chinese else "independent corroborating source"
        reason = "上一轮缺少独立印证，改查不同发布主体"
    elif "时效" in missing:
        strategy = "VERIFY_FRESHNESS"
        year = str(datetime.now(timezone.utc).year)
        suffix = f"{year} 最新更新 生效日期" if chinese else f"{year} latest update effective date"
        reason = "上一轮缺少时效锚点，定向查找更新时间或生效日期"
    else:
        strategy = "TARGET_MISSING_CLAIM"
        suffix = "具体要求 原始说明" if chinese else "specific requirements original statement"
        reason = "上一轮来源不够直接，缩小到缺失主张本身"
    if round_number >= _MAX_RETRIEVAL_ROUNDS:
        suffix += " 原始附件" if chinese else " original publication"
    query = f"{base_query[: max(40, 158 - len(suffix))]} {suffix}".strip()[:160]
    return query, strategy, reason


async def build_evidence_bundle(
    context: SearchContext,
    provider: SearchProvider | None = None,
    *,
    force_search: bool = False,
    search_intent: tuple[bool, str] | None = None,
) -> EvidenceBundle:
    default_chain = provider is None
    budget = SearchBudgetTelemetry()
    provider_chain = SearchProviderChain(None if default_chain else [provider], budget=budget)
    direct_attempts: list[ProviderAttempt] = []
    now = datetime.now(timezone.utc).isoformat()
    query = build_search_query(context)
    search_needed, reason = search_intent or should_search(context)
    decision: SearchDecision = "QA_FORCED_SEARCH" if force_search else "SEARCH" if search_needed else "SKIP"
    if force_search:
        reason = "QA_FORCED_SEARCH：验收显式绕过生产搜索决策，仅用于验证真实检索链路。"
    if decision == "SKIP":
        return EvidenceBundle(
            query=query,
            search_decision=decision,
            search_executed=False,
            intent_reason=reason,
            status="UNVERIFIED",
            source_relationship="NOT_COMPARABLE",
            relationship_notes=["未执行外部检索，因此没有来源关系可比较。"],
            conclusion_hint="现有上下文足以回答，本轮没有执行外部检索。",
            sources=[],
            conflicts=[],
            retrieved_at=now,
            provider=provider_chain.name,
            sufficient=True,
            review_disposition="NO_SEARCH",
            missing_evidence=[],
            retrieval_rounds=[],
            bounded_exhausted=False,
            provider_attempts=[],
            search_budget=budget.public_dict(),
        )
    include_community = bool(_COMMUNITY.search(context.message))
    all_hits: list[SearchHit] = []
    rounds: list[RetrievalRound] = []
    current_query = query
    # 模型计划里的其余检索式，按顺序在后续轮次用掉。
    planned_queries = [
        str(value).strip() for value in (context.planned_queries or [])
        if str(value).strip() and str(value).strip() != query
    ]
    current_strategy = "INITIAL_QUERY"
    current_reason = "先用用户问题与最小工作区上下文检索"
    # 模型判读出来的「能帮到用户」的来源 URL 与来源本身，以及是否已经判过（一轮只判一次）。
    graded_useful: set[str] = set()
    graded_extra: list[EvidenceSource] = []
    graded_done = False
    assessment = EvidenceAssessment(False, "FURTHER_RETRIEVAL", "尚未检索", ["尚未取得可用来源"])
    relationship: SourceRelationship = "NOT_COMPARABLE"
    relationship_notes = ["尚未取得可比较的来源。"]
    conflicts: list[str] = []
    normalized: list[EvidenceSource] = []
    last_error: str | None = None

    explicit_urls = _EXPLICIT_URL.findall(context.message) if default_chain else []
    preferred_urls = list(context.preferred_urls) if default_chain else []
    seed_urls = list(dict.fromkeys([*explicit_urls, *preferred_urls]))[:2]
    if seed_urls:
        for seed_url in seed_urls:
            direct_hit = await _fetch_explicit_source(
                seed_url, budget=budget, attempts=direct_attempts, query=context.message,
            )
            if direct_hit is not None:
                all_hits.append(direct_hit)
        normalized = normalize_hits(all_hits, now, include_community=include_community)
        relationship, relationship_notes, conflicts = detect_source_relationship(normalized)
        assessment = assess_evidence_sufficiency(context, normalized, relationship)
        direct_error = next((item.error for item in reversed(direct_attempts) if item.error), None)
        rounds.append(RetrievalRound(
            round_number=1,
            query=" | ".join(seed_urls),
            strategy="EXPLICIT_DIRECT_SOURCE" if explicit_urls else "TRUSTED_OFFICIAL_BOOTSTRAP",
            reason=(
                "用户显式给出了已知 URL，先在安全边界内直接核对原页面"
                if explicit_urls else
                "DARYA 选择了可审计的一手来源入口，并实时读取原页面"
            ),
            new_source_count=len(normalized),
            cumulative_source_count=len(normalized),
            disposition=assessment.disposition,
            sufficient=assessment.sufficient,
            missing_evidence=assessment.missing_evidence,
            error=direct_error,
            provider="direct-source",
        ))
        if assessment.sufficient:
            budget.early_stop_reason = "SUFFICIENT_DIRECT_SOURCE"

    for round_number in range(len(rounds) + 1, _MAX_RETRIEVAL_ROUNDS + 1):
        if assessment.sufficient or not budget.can_request():
            if not assessment.sufficient and budget.early_stop_reason is None:
                budget.early_stop_reason = "REQUEST_OR_TIME_BUDGET_EXHAUSTED"
            break
        previous_count = len(normalized)
        round_error: str | None = None
        attempt_start = len(provider_chain.attempts)
        try:
            all_hits.extend(await provider_chain.search(current_query))
            last_error = None
        except Exception as exc:
            round_error = type(exc).__name__
            last_error = round_error
        normalized = normalize_hits(all_hits, now, include_community=include_community)
        relationship, relationship_notes, conflicts = detect_source_relationship(normalized)
        # 词面判据没挑够时才花这一次模型判读：正常轮次不额外加时间。
        # 只判一次，判读用的是当时拿到的候选；后续轮次沿用同一份 URL 集合。
        if not graded_done and normalized:
            if len([s for s in normalized if _is_direct_for_context(context, s)]) < 2:
                # 判读要看得到全部候选，**包括社区来源**。
                # 「这件事具体怎么操作」这类问题上，一条知乎的逐步操作指引比一个
                # 只放目录的官网首页有用得多；而社区来源是不是有用，本来就该由
                # 「能不能帮到他」来判，不该由用户话里有没有出现社区类词来预先决定。
                grade_pool = normalize_hits(all_hits, now, include_community=True)
                graded_useful = await grade_sources_for_goal(context, grade_pool)
                graded_extra = [s for s in grade_pool if s.url in graded_useful]
            graded_done = True
        pool = list(normalized) + [
            source for source in graded_extra
            if source.url not in {item.url for item in normalized}
        ]
        assessment = assess_evidence_sufficiency(
            context, pool, relationship, graded_useful=graded_useful,
        )
        if round_error and not normalized:
            assessment = EvidenceAssessment(
                False,
                "FURTHER_RETRIEVAL",
                "提供方未返回可用结果，仍在有界预算内",
                ["检索提供方失败，尚未取得可用来源"],
            )
        rounds.append(RetrievalRound(
            round_number=round_number,
            query=current_query,
            strategy=current_strategy,
            reason=current_reason,
            new_source_count=max(0, len(normalized) - previous_count),
            cumulative_source_count=len(normalized),
            disposition=assessment.disposition,
            sufficient=assessment.sufficient,
            missing_evidence=assessment.missing_evidence,
            error=round_error,
            provider="->".join(dict.fromkeys(
                item.provider for item in provider_chain.attempts[attempt_start:]
            )) or None,
        ))
        if assessment.sufficient:
            budget.early_stop_reason = "EVIDENCE_SUFFICIENT"
            break
        # 判读已经挑出了能帮到用户的来源，就不要再花一整轮去追更权威的了：
        # 每一轮都是链子上限（4s）加上可能的判读，而用户此刻要的是答案。
        # 此时状态仍按实测写（多半是 PARTIALLY_SUPPORTED），不因为停下来就升格。
        if graded_useful:
            budget.early_stop_reason = "GRADED_SOURCES_SUFFICIENT"
            break
        if round_number < _MAX_RETRIEVAL_ROUNDS:
            if (
                not round_error
                and normalized
                and round_number == 1
                and provider_chain.has_untried(query)
            ):
                # The query worked -- results came back. What was missing was a
                # *better source*, not a better string. Rewriting the query here
                # re-asks the provider that already answered and leaves the ones
                # that never ran untouched: for "2026 年高考报名时间" that meant
                # never reaching the provider that had the official 阳光高考
                # page, while three rounds were spent appending keywords to the
                # same weak result set.
                #
                # Keeping the query identical makes the chain advance to the
                # next provider, because it skips (provider, query) pairs it has
                # already tried. Broaden the wording only after that.
                current_strategy = "TRY_NEXT_PROVIDER"
                current_reason = (
                    "上一轮取回了结果但来源不够权威：先问还没答过的提供方，"
                    "而不是把同一个查询再加几个词重问一遍"
                )
            elif planned_queries:
                # 模型计划里的下一条检索式优先于正则改写：它知道这一轮要核对的是哪个主张。
                current_query = planned_queries.pop(0)
                current_strategy = "MODEL_PLANNED_QUERY"
                current_reason = "上一轮没有取得足够直接的来源：按模型研究计划里的下一条检索式继续核对"
            else:
                current_query, current_strategy, current_reason = _next_retrieval_query(
                    query,
                    assessment,
                    round_number + 1,
                    provider_failed=bool(round_error and not normalized),
                )

    exhausted = not assessment.sufficient and (
        len(rounds) >= _MAX_RETRIEVAL_ROUNDS or not budget.can_request()
    )
    # Search rank is discovery, not evidence relevance.  Only sources that
    # directly cover the current claim may reach the final bundle/source UI;
    # unrelated high-authority pages must not decorate a weak answer.
    conflict_source_urls = _conflicting_source_urls(normalized) if conflicts else set()
    # 判读挑出来的来源（可能含社区页）和词面判据挑出的一样进入最终来源。
    graded_by_url = {source.url: source for source in graded_extra}
    final_pool = list(normalized) + [
        source for url, source in graded_by_url.items()
        if url not in {item.url for item in normalized}
    ]
    directly_relevant = [
        source for source in final_pool
        if _is_direct_for_context(context, source)
        or (graded_useful and source.url in graded_useful)
        or source.url in conflict_source_urls
    ]
    sources = _select_useful_sources(directly_relevant, include_community)
    if assessment.sufficient:
        status: EvidenceStatus = "SUPPORTED"
        disposition: EvidenceDisposition = "ANSWER"
        hint = assessment.reason + "；现在应直接回答，并只附最强的 1–3 个来源。"
    else:
        status = "CONFLICTING" if conflicts else "PARTIALLY_SUPPORTED" if sources else "UNVERIFIED"
        disposition = "CALIBRATED_FALLBACK"
        if sources:
            hint = "有界检索已完成；请回答现有证据能支持的部分，并明确仍缺少什么。"
        else:
            hint = "有界检索未取得可用外部证据；请继续给出基于可信内部上下文或一般知识的有用答案，并降低确定性。"
    return EvidenceBundle(
        query=query,
        search_decision=decision,
        search_executed=True,
        intent_reason=reason,
        status=status,
        source_relationship=relationship,
        relationship_notes=relationship_notes,
        conclusion_hint=hint,
        sources=sources,
        conflicts=conflicts,
        retrieved_at=now,
        provider=provider_chain.name,
        sufficient=assessment.sufficient,
        review_disposition=disposition,
        missing_evidence=assessment.missing_evidence,
        retrieval_rounds=rounds,
        bounded_exhausted=exhausted,
        error=last_error,
        provider_attempts=[*direct_attempts, *provider_chain.attempts],
        search_budget=budget.public_dict(),
    )


def build_evidence_directive(bundle: EvidenceBundle) -> str:
    """Give the existing DARYA/review path bounded, exact retrieved evidence."""
    import json
    payload = json.dumps(bundle.public_dict(), ensure_ascii=False)
    return (
        "本轮已执行真实外部检索。以下 JSON 是唯一允许引用的外部证据：\n" + payload + "\n"
        "中心先给一个人话结论，必要时补一个重要保留。不得发明 URL、标题、日期、作者或统计。"
        "FACT 必须能由 evidence sources 直接支持；弱来源只能写成意见或体验。"
        "若 review_disposition=ANSWER，证据阈值已经达到：直接回答，不要用笼统的‘证据不足’拒绝。"
        "若 review_disposition=CALIBRATED_FALLBACK，有界检索已经结束：仍要回答证据可支持、可信内部上下文或稳定一般知识能支持的部分，"
        "把缺口变成具体保留和下一步核对建议，不要把 usefulness 降为零。"
        "若 source_relationship=CONFLICTING，明确说可比主张冲突，不要替用户硬选。"
        "若 DIFFERENT_LAYER，说明规则与体验是不同层而非矛盾；若 NOT_COMPARABLE，不要强行比较。若 UNVERIFIED，明确说未取得证据。"
        "Sidecar 只列 1-3 条最有用证据，每条保留标题、来源、链接、证据类型和简短理由；不要倾倒搜索结果。"
    )


def search_recovery_result(bundle: EvidenceBundle, *, first_move: str = "") -> str:
    """Keep a bounded, source-backed result when answer generation returns nothing."""
    query = re.sub(r"[\[\]\r\n]+", " ", bundle.query).strip()[:160] or "本轮问题"
    lines = ["[tide_center]", f"关于「{query}」，本轮检索已经完成。"]
    if bundle.sources:
        lines.append("我先交付实际取回的材料；它们能支持的部分可以继续用，未覆盖的部分暂不当作结论。")
        lines.append("[/tide_center]")
        lines.append('[tide_sidecar type="evidence" title="本轮检索结果"]')
        for source in bundle.sources[:3]:
            excerpt = re.sub(r"\s+", " ", source.excerpt).strip()[:260]
            lines.append(f"- {source.title}（{source.source}）：{excerpt} {source.url}")
    else:
        move = re.sub(r"\s+", " ", first_move).strip()[:320]
        if move:
            lines.append(f"现在仍可先做：{move}")
        else:
            lines.append("本轮没有取回可核对的外部来源；与当前版本、政策或数字有关的结论仍待核实。")
        lines.append("[/tide_center]")
        lines.append('[tide_sidecar type="evidence" title="本轮核对记录"]')
    missing = "；".join(bundle.missing_evidence[:2]) or "缺少直接覆盖问题的一手材料"
    lines.append(f"- 待核对：{missing}")
    lines.append("[/tide_sidecar]")
    return "\n".join(lines)


def enforce_evidence_response(
    raw: str,
    bundle: EvidenceBundle,
    *,
    goal_context: Mapping[str, Any] | None = None,
) -> str:
    """Deterministic last gate: no invented link can reach the user."""
    allowed = {source.url.rstrip("/.,)") for source in bundle.sources}
    def checked(match: re.Match) -> str:
        value = match.group(0).rstrip("/.,)")
        return match.group(0) if value in allowed else "[未核实链接已移除]"
    result = re.sub(r"https?://[^\s<>\]\)]+", checked, str(raw or ""))
    goal_context = dict(goal_context or {})
    useful_first_move = re.sub(
        r"\s+", " ", str(goal_context.get("useful_first_move") or "")
    ).strip()[:500]
    clarification = re.sub(
        r"\s+", " ", str(goal_context.get("clarification_question") or "")
    ).strip()[:240]
    decision_criteria = [
        re.sub(r"\s+", " ", str(item)).strip()[:180]
        for item in list(goal_context.get("decision_criteria") or [])[:4]
        if str(item).strip()
    ]
    assistant_commitments = [
        re.sub(r"\s+", " ", str(item)).strip()[:240]
        for item in list(goal_context.get("assistant_commitments") or [])[:3]
        if str(item).strip()
    ]
    if goal_context.get("active") and useful_first_move and bundle.review_disposition == "CALIBRATED_FALLBACK":
        # A weak retrieval result must not take the whole conversation hostage.
        # The model still receives the bounded evidence directive and can give
        # stable, generally applicable guidance. Keep that useful work while
        # isolating current facts behind an explicit evidence boundary. Only a
        # short or non-actionable answer is replaced by the deterministic V0.
        visible = re.sub(r"\[/?tide_(?:center|sidecar)[^\]]*\]", "", result, flags=re.I).strip()
        provider_useful = (
            len(visible) >= 48
            and bool(re.search(r"(?:先|今天|本周|第一|计划|建议|可以|步骤|判断|选择)", visible))
            and not re.fullmatch(r"[^?？]{0,300}[?？]", visible, re.S)
        )
        career_goal = bool(re.search(
            r"(?:考公|公务员|岗位|求职|就业|转行|选岗|职位表)",
            str(goal_context.get("goal") or ""),
            re.I,
        ))
        creator_goal = bool(re.search(
            r"(?:B站|UP主|视频|课程|教学内容|涨粉|完播|流量|观众|内容创作)",
            str(goal_context.get("goal") or ""),
            re.I,
        ))
        # Without direct evidence, career and job-selection prose is especially
        # prone to plausible-sounding folklore (demand, competition, fit). Use
        # the deterministic decision scaffold for the whole class, not only
        # for phrases the detector happened to recognize.
        if career_goal:
            provider_useful = False
        if provider_useful:
            center_match = re.search(r"\[tide_center\]([\s\S]*?)\[/tide_center\]", result, re.I)
            if center_match and re.search(
                r"(?:政策|专项计划)[^。！？\n]*(?:直接决定|白复习|最该做|第一步)",
                center_match.group(1),
            ):
                safe_center = (
                    "先把会变化的外部条件列为待核对项，同时用一周的小实验补齐真正会改变选择的个人信息；"
                    "在拿到结果前不做绝对推荐。"
                )
                result = result[:center_match.start(1)] + "\n" + safe_center + "\n" + result[center_match.end(1):]
            result = re.sub(
                r"退役(?:大学生|士兵)?身份[^。！？\n]*(?:最大(?:的)?杠杆|最值钱|红利)[^。！？\n]*[。！？]?",
                "退役身份可能影响报考路径，但在官方条件核实前，不把它当作选方向的前提。",
                result,
            )
            replacements = {
                "首选，偏应用、好上手": "若更偏工程实践，可先试学再比较",
                "次选，不卷算法": "若偏数据应用，可先试学再比较",
                "数学好才考虑": "若数学基线较强，可纳入比较",
                "数学没把握别碰": "若数学基线较弱，先补基础并试学后再决定",
            }
            for asserted, conditional in replacements.items():
                result = result.replace(asserted, conditional)
            # Prevent a no-source answer from acknowledging its boundary and
            # then laundering market folklore into certainty later on.
            no_source_replacements = {
                "在公考里是优势，不是劣势": "可以形成一类专业匹配线索，但是否构成优势要看当年职位表",
                "是公务员招录里需求量很大的专业方向": "可能对应部分专业限制岗位，具体范围要以当年职位表核对",
                "你能报的岗位远比你想象的多": "你能报哪些岗位目前还不能下结论",
                "招会计大户，专业对口度极高": "可能出现财会类岗位，具体专业代码与条件待核对",
                "与会计工作内容最接近": "可能与既有会计经验有部分可迁移之处，仍需看岗位职责",
                "专业高度匹配": "是否匹配取决于职位表的专业代码与具体条件",
                "竞争相对激烈": "竞争情况待当年招录数据核对",
                "需要财会背景的岗位不少": "是否招收财会背景待职位表核对",
                "门槛较高": "具体门槛待职位表核对",
                "这两块提分最快": "可先各做一小组题，用实际正确率和耗时决定先学哪块",
            }
            for asserted, calibrated in no_source_replacements.items():
                result = result.replace(asserted, calibrated)
            if creator_goal:
                creator_replacements = {
                    "完播和涨粉会自然跟上来": "发布后再用完播、互动与关注变化检验这次改法是否有效",
                    "有稳定规律可循": "可以先从可验证的教学设计假设开始",
                    "多数时候是承诺太泛，不是内容不行": "先把“观看承诺是否具体”作为第一项待检验假设，不预设内容或流量问题的结论",
                    "四条都过，就值得坚持": "这四条只作为首轮自检，是否值得延续仍要看发布后的真实反馈",
                    "先别追热点和流量": "先不把追热点或流量当作未经验证的答案",
                }
                for asserted, calibrated in creator_replacements.items():
                    result = result.replace(asserted, calibrated)
            result = re.sub(
                r"如果情绪持续影响睡眠或日常，[^。！？\n]*(?:热线|\d{3,})[^。！？\n]*[。！？]?",
                "如果情绪持续影响睡眠或日常，优先联系可信任的人，并通过当地卫健部门官方渠道查询正规心理援助资源。",
                result,
            )
            result = re.sub(
                r"每天固定\s*(?:1(?:\.5)?|2|3)\s*小时",
                "先做一组短练习，具体时长在确认你的可用时间后调整",
                result,
            )
            sensitive = re.compile(
                r"(?:专项计划|加分(?:政策)?|单独划线|单独录取|不占(?:用)?普通|全国有|"
                r"竞争压力|录取率|复试分数线|专项计划线|招生名额|岗位表|报考条件|"
                r"资格条件|身份红利|最值钱的一张牌|最大(?:的)?杠杆|\b408\b)",
                re.I,
            )
            unsupported_market = re.compile(
                r"(?:招录量|岗位(?:多|不少)|专业对口度|对口度(?:高|极高)|"
                r"匹配度(?:高|低)|最对口|竞争(?:比|激烈|相对小|最大|最小|难度)|"
                r"进面分数线|需求量|最主流|经验能直接用|提分最快|保底(?:选项)?|"
                r"国考一般(?:是|考)|(?:有|是).{0,6}优势|\d+\s*个(?:左右)?岗位)",
                re.I,
            )
            assertive = re.compile(
                r"(?:\d|通常|可以享受|可享受|远小于|更小|必须|都是|均为|"
                r"单独|不占|全国|名额|加分|分数线)",
                re.I,
            )
            verification = re.compile(r"(?:待核实|需要核对|下一步核对|帮你查|以当年.*为准)")
            calibrated_lines: list[str] = []
            for line in result.splitlines():
                plain = re.sub(r"^[\s>*#|\-]+", "", line).strip()
                unsafe_assertion = bool(
                    sensitive.search(plain)
                    and assertive.search(plain)
                    and not (verification.search(plain) and not re.search(r"\d", plain))
                )
                unsafe_assertion = unsafe_assertion or bool(unsupported_market.search(plain))
                if not unsafe_assertion:
                    calibrated_lines.append(line)
            result = "\n".join(calibrated_lines)
            if creator_goal:
                boundary = (
                    "> **事实边界：** 本轮没有取得可直接核对的平台或频道数据。"
                    "推荐机制、流量因果、行业基准和所谓爆款规律均不作确定结论；"
                    "以下先交付可拍摄、可观察、可复盘的小实验。\n\n"
                )
            else:
                boundary = (
                    "> **事实边界：** 本轮没有取得可直接核对的官方来源。"
                    "政策、资格、科目、数量、平台机制或市场数据等会随时间和主体变化的内容，"
                    "这里不作确定结论；以下先交付不依赖这些事实的判断框架和行动计划。\n\n"
                )
            sidecar_open = re.search(r"\[tide_sidecar\b[^\]]*\]\s*", result, re.I)
            if sidecar_open:
                result = result[:sidecar_open.end()] + boundary + result[sidecar_open.end():]
            else:
                result = (
                    result.rstrip()
                    + "\n[tide_sidecar type=\"evidence\" title=\"仍待核对的外部事实\"]\n"
                    + boundary
                    + "- 本轮检索没有取得可直接核对的来源；涉及当年政策、岗位表、院校要求或数字的部分仍按未核实处理。\n"
                    + "- 已先交付的判断框架和行动计划不依赖这些未核实事实，可立即开始并在拿到官方信息后修正。\n"
                    + "[/tide_sidecar]"
                )
            goal_text = str(goal_context.get("goal") or "")
            if re.search(r"(?:计划|规划|怎么开始|从哪里开始|plan|start)", goal_text, re.I):
                completion_count = len(re.findall(
                    r"(?:完成信号|完成标准|验收|记录|产出|测出|检验|复盘|调整|可回退)",
                    result,
                    re.I,
                ))
                if completion_count < 2:
                    quality_block = (
                        "\n\n### 完成信号与回退规则\n"
                        "- 每个阶段至少留下一个可检查的产出或记录，而不是只计算坐在书桌前的时长。\n"
                        "- 每周末用实际完成率和最大阻碍复盘；连续两次完不成时，把下一阶段任务量减半，保留核心动作后再调整。\n"
                    )
                    result = re.sub(
                        r"\s*\[/tide_sidecar\]",
                        quality_block + "[/tide_sidecar]",
                        result,
                        count=1,
                        flags=re.I,
                    )
            return result

        question_line = f"\n\n{clarification}" if clarification else ""
        criteria_line = (
            "先把决定拆成几个会真正改变选择的问题：\n"
            + "\n".join(f"- {item}" for item in decision_criteria)
            if decision_criteria else useful_first_move
        )
        commitment_lines = (
            "\n\n这些工作由我来承担，而不是再布置给你：\n"
            + "\n".join(f"- {item}" for item in assistant_commitments)
            if assistant_commitments else ""
        )
        goal_text = str(goal_context.get("goal") or "")
        if career_goal:
            emotional_opening = (
                "先暂停不可逆决定：你现在可以只处理今天，不必在情绪峰值决定辞职、报岗或长期去向。\n\n"
                if re.search(r"情绪|刺激|焦虑|崩溃|难受", visible)
                else "先不急着替自己定方向，把决策缩成一个可检查的小实验。\n\n"
            )
            criteria_line = (
                emotional_opening
                + "**现在已知**：你的职业背景和转向目标可以作为筛选维度；"
                "**现在不能推出**：哪些岗位能报、竞争强弱、是否有专业优势或哪条路最适合你。\n\n"
                + "### 最小启动版\n"
                + "- **今天（10–20 分钟）**：只写三项不能妥协的条件，例如地区、工作节奏、是否接受基层。完成信号：留下三行条件；撑不住时只写第一项。\n"
                + "- **未来 3 天**：做一小组能力摸底并记录正确率、耗时和感受，不用分数评价自己。完成信号：得到一条真实基线。\n"
                + "- **本周末**：在拿到对应地区的官方公告或职位表后，再按资格、专业代码、地点和个人条件逐项筛选；没有官方材料就保持“待核实”，不排名。完成信号：形成一张条件表，而不是凭印象选岗位。\n\n"
                + criteria_line
            )
        result = (
            "[tide_center]\n"
            "先按当前已确认的信息推进；会变化的排名、比例、要求或机会暂不当作结论，也不妨碍现在开始。\n\n"
            f"{criteria_line}"
            f"{commitment_lines}"
            f"{question_line}\n"
            "[/tide_center]\n"
            "[tide_sidecar type=\"evidence\" title=\"外部信息待核对\"]\n"
            "- 这次检索结果没有直接覆盖你的目标，因此未将它作为推荐依据。\n"
            "- 如需核对当前机会、要求或数字，我们再以具体主体或官方页面继续查。\n"
            "[/tide_sidecar]"
        )
    elif bundle.status == "UNVERIFIED" and bundle.review_disposition == "CALIBRATED_FALLBACK":
        # A language model can correctly admit retrieval failure and still fill
        # the gap with an unsupported current fact. For the no-source case the
        # only reliable final gate is to return a useful, claim-free fallback.
        # This is not silence: it gives a decision posture and a concrete next
        # verification step without inventing versions, dates, rules or stats.
        subject = re.sub(r"[\[\]\r\n]+", " ", bundle.query).strip()[:120] or "这项外部事实"
        missing = "；".join(bundle.missing_evidence[:2]) or "仍缺少可核对的一手来源"
        visible_answer = re.sub(r"\[/?tide_(?:center|sidecar)[^\]]*\]", "", result, flags=re.I).strip()
        teaching_request = bool(
            re.search(r"教(?:我|教|一?下)|带我学|我想学|想学会|怎么学|学会|入门|先学|讲(?:讲|解|一下|一讲)?|举例说明", subject, re.I)
            and not re.search(r"官方|最新|政策|核实|求证|版本|价格|发布|官网", subject, re.I)
            and not re.search(
                r"考公|公务员|岗位|求职|就业|转行|职业|职位表|B站|UP主|涨粉|完播|观众|招生|录取|院校",
                subject, re.I,
            )
        )
        teaching_answer_useful = (
            len(visible_answer) >= 48
            and bool(re.search(r"(?:先|可以|步骤|例|公式|判断|练习|第一步|生活|想象)", visible_answer))
        )
        if teaching_request and teaching_answer_useful:
            # A failed open-topic grounding must not take a stable-knowledge
            # teaching turn hostage. Keep the substantive explanation and make
            # the evidence boundary explicit instead of asserting unverifiable
            # current facts.
            sidecar_open = re.search(r"\[tide_sidecar\b[^\]]*\]\s*", result, re.I)
            boundary = (
                f"- 当前边界：{missing}\n"
                "- 本轮没有取得可核对来源；讲解只覆盖稳定的学科知识，不涉及会变化的现实事实。\n"
            )
            if sidecar_open:
                result = result[:sidecar_open.end()] + boundary + result[sidecar_open.end():]
            else:
                result = (
                    result.rstrip()
                    + "\n[tide_sidecar type=\"evidence\" title=\"资料依据\"]\n"
                    + boundary
                    + "[/tide_sidecar]"
                )
            return result
        if re.search(r"(?:考公|公务员|岗位|职位表|求职|就业|转行|职业)", subject, re.I):
            result = (
                "[tide_center]\n"
                "已接住你补充的地区与时间条件，不需要重讲职业背景，也不把翻职位表重新丢给你。\n\n"
                "### 现在的下一步\n"
                "- **今天**：只补齐会影响硬门槛的私人信息——学历、学位、是否应届、基层经历；完成信号是一张四行清单。\n"
                "- **我来核对**：对应地区官方招考入口、当期公告与职位表、专业代码口径、学历和经历限制；没有原文的项目保持待核实，不用第三方汇总替代。\n"
                "- **核对后交付**：把符合与不符合条件的原因写进同一张筛选表，再按你的地区和工作节奏比较，不按传闻中的竞争强弱排名。\n"
                "- **回退规则**：今天状态不足时只填学历一项，剩余三项下次续上，不算中断。\n\n"
                "现在只缺一个会改变硬门槛的变量：你的最高学历和学位是什么？\n"
                "[/tide_center]\n"
                "[tide_sidecar type=\"evidence\" title=\"待核对的官方信息\"]\n"
                f"- 当前边界：{missing}\n"
                "- 核对对象：地区官方招考公告、职位表及其专业目录说明。\n"
                "[/tide_sidecar]"
            )
        else:
            result = (
                "[tide_center]\n"
                "先按“未确认”处理这项外部信息；不要据此做不可逆动作。现在先核对原始发布主体、更新时间和适用范围。\n"
                "[/tide_center]\n"
                "[tide_sidecar type=\"evidence\" title=\"还需要核对什么\"]\n"
                f"- 当前边界：{missing}\n"
                "- 下一步：提供对应官方页面或更具体的主体、地区、版本，我可以继续逐项核对。\n"
                "[/tide_sidecar]"
            )
    elif bundle.review_disposition == "CALIBRATED_FALLBACK" and bundle.sources:
        # Related-but-indirect search results must not be inflated into a
        # current version/date/requirements claim. Preserve the strongest
        # retrieved excerpt as a labeled lead and state the exact gap.
        strongest = bundle.sources[0]
        excerpt = re.sub(r"\s+", " ", strongest.excerpt).strip()[:260]
        missing = "；".join(bundle.missing_evidence[:2]) or "来源还没有直接覆盖所问主张"
        result = (
            "[tide_center]\n"
            "我找到了相关页面，但它们还不足以直接确认你问的当前版本、日期、要求或数字，"
            "所以我不把相关性写成已核实结论。\n\n"
            f"目前可安全保留的线索是：「{strongest.title}」（{strongest.source}）的检索摘要为“{excerpt}”。\n"
            "[/tide_center]\n"
            "[tide_sidecar type=\"evidence\" title=\"还需要哪一步\"]\n"
            f"- 证据缺口：{missing}\n"
            f"- 相关线索：{strongest.title} — {strongest.url}\n"
            "- 下一步：打开对应官方原文或提供确切 URL，再核对版本、日期和适用范围。\n"
            "[/tide_sidecar]"
        )
    return result
