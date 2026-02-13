import asyncio
import logging
import math
import os
import urllib.parse
from typing import Any, Literal, Protocol

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
BRAVE_WEB_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class SearchResultEntry(BaseModel):
    title: str
    link: str = Field(..., description="url of the website")
    snippet: str = Field(..., description="a snippet of the website")


class SearchClient(Protocol):
    async def search(self, max_results: int = 10) -> list[SearchResultEntry] | None: ...


def _build_domain_restricted_query(query: str, query_domains: list[str] | None) -> str:
    if not query_domains:
        return query
    domains = [d.strip() for d in query_domains if isinstance(d, str) and d.strip()]
    if not domains:
        return query
    domain_query = " OR ".join([f"site:{domain}" for domain in domains])
    return f"({domain_query}) {query}"


def _compact_snippet(text: str, limit: int = 300) -> str:
    snippet = text[:limit]
    return (
        snippet.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t").strip()
    )


def _redact_url_query_param(url: str, param: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        redacted = [(k, "REDACTED" if k == param else v) for k, v in query]
        return urllib.parse.urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urllib.parse.urlencode(redacted),
                parts.fragment,
            )
        )
    except Exception:
        return url


def _parse_brave_web_results(payload: Any) -> list[SearchResultEntry]:
    if not isinstance(payload, dict):
        return []
    web = payload.get("web")
    if not isinstance(web, dict):
        return []
    results = web.get("results", [])
    if not isinstance(results, list):
        return []

    entries: list[SearchResultEntry] = []
    for item in results:
        if not isinstance(item, dict):
            continue

        title = item.get("title")
        url = item.get("url")
        description = item.get("description") or ""
        extra_snippets = item.get("extra_snippets")

        if not isinstance(title, str) or not isinstance(url, str):
            continue
        if not isinstance(description, str):
            description = str(description)
        if not title.strip() or not url.strip():
            continue

        snippet_parts: list[str] = [description.strip()] if description.strip() else []
        if isinstance(extra_snippets, list):
            for s in extra_snippets:
                if not isinstance(s, str):
                    continue
                cleaned = s.strip()
                if cleaned:
                    snippet_parts.append(cleaned)

        # De-duplicate while preserving order.
        deduped: list[str] = []
        seen_parts: set[str] = set()
        for part in snippet_parts:
            if part in seen_parts:
                continue
            seen_parts.add(part)
            deduped.append(part)

        snippet = "\n".join(deduped)
        entries.append(
            SearchResultEntry(title=title.strip(), link=url.strip(), snippet=snippet)
        )

    return entries


class GoogleSearch:
    """Google Custom Search JSON API client (legacy)."""

    def __init__(
        self,
        query: str,
        headers: dict | None = None,
        query_domains: list[str] | None = None,
    ) -> None:
        self.query = query
        self.headers = headers or {}
        self.query_domains = query_domains or None
        self.api_key = self.headers.get("google_api_key") or self._get_env(
            "GOOGLE_API_KEY"
        )
        self.cx_key = self.headers.get("google_cx_key") or self._get_env(
            "GOOGLE_CX_KEY"
        )

    @staticmethod
    def _get_env(name: str) -> str:
        try:
            val = os.environ[name]
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Missing required environment variable: {name}") from e
        if not isinstance(val, str) or not val.strip():
            raise RuntimeError(f"Missing required environment variable: {name}")
        return val.strip()

    async def search(self, max_results: int = 10) -> list[SearchResultEntry] | None:
        total_results_to_fetch = min(100, max_results)
        if total_results_to_fetch <= 0:
            return []
        results_per_request = min(10, total_results_to_fetch)
        seen_links: set[str] = set()

        search_query = _build_domain_restricted_query(self.query, self.query_domains)
        encoded_query = urllib.parse.quote_plus(search_query)
        logger.info("Google CSE searching for: %r", search_query)

        results: list[SearchResultEntry] = []
        try:
            async with aiohttp.ClientSession() as session:
                pages = math.ceil(total_results_to_fetch / results_per_request)
                for page in range(pages):
                    start_index = page * results_per_request + 1  # 1-based
                    request_url = (
                        f"{GOOGLE_CSE_ENDPOINT}"
                        f"?key={self.api_key}"
                        f"&cx={self.cx_key}&q={encoded_query}&start={start_index}"
                        f"&num={results_per_request}"
                    )
                    log_url = _redact_url_query_param(request_url, "key")

                    async with session.get(request_url) as resp:
                        if not (200 <= resp.status < 300):
                            body = await resp.text()
                            logger.error(
                                "Google CSE unexpected status=%s url=%s body_snippet=%s",
                                resp.status,
                                log_url,
                                _compact_snippet(body),
                            )
                            return None
                        payload = await resp.json()
                        if isinstance(payload, dict) and "error" in payload:
                            logger.error(
                                "Google CSE API error url=%s error=%s",
                                log_url,
                                payload.get("error"),
                            )
                            return None

                    items = (
                        payload.get("items", []) if isinstance(payload, dict) else []
                    )
                    if not items:
                        break

                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        link = item.get("link", "")
                        if not isinstance(link, str) or not link:
                            continue
                        if "youtube.com" in link or link in seen_links:
                            continue
                        title = item.get("title", "")
                        snippet = item.get("snippet", "")
                        if not isinstance(title, str) or not title:
                            continue
                        if not isinstance(snippet, str):
                            snippet = str(snippet)
                        results.append(
                            SearchResultEntry(title=title, link=link, snippet=snippet)
                        )
                        seen_links.add(link)
                        if len(results) >= max_results:
                            break

                    if len(results) >= max_results:
                        break
                    if len(items) < results_per_request:
                        break
                    await asyncio.sleep(0.05)
        except aiohttp.ClientError as e:
            logger.exception("Google CSE connection error: %s", e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception("Unexpected error in Google CSE flow: %s", e)
            return None

        return results[:max_results]


class BraveSearch:
    """Brave Search API client (web search)."""

    def __init__(
        self,
        query: str,
        headers: dict | None = None,
        query_domains: list[str] | None = None,
    ) -> None:
        self.query = query
        self.headers = headers or {}
        self.query_domains = query_domains or None
        self.api_key = (
            self.headers.get("brave_api_key")
            or self.headers.get("brave_search_api_key")
            or os.environ.get("BRAVE_API_KEY")
            or os.environ.get("BRAVE_SEARCH_API_KEY")
        )
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise RuntimeError(
                "Brave API key not found. Set BRAVE_API_KEY (or BRAVE_SEARCH_API_KEY)."
            )
        self.api_key = self.api_key.strip()

    async def search(self, max_results: int = 10) -> list[SearchResultEntry] | None:
        # Brave docs: count max 20; offset max 9 (10 pages).
        results_per_request = min(20, max_results) if max_results > 0 else 0
        if results_per_request <= 0:
            return []
        total_results_to_fetch = min(100, max_results, results_per_request * 10)
        pages = math.ceil(total_results_to_fetch / results_per_request)

        search_query = _build_domain_restricted_query(self.query, self.query_domains)
        logger.info("Brave Search querying for: %r", search_query)

        seen_links: set[str] = set()
        results: list[SearchResultEntry] = []

        req_headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }
        try:
            async with aiohttp.ClientSession() as session:
                for page in range(pages):
                    params = {
                        "q": search_query,
                        "count": results_per_request,
                        "offset": page,
                        "extra_snippets": "true",
                        "result_filter": "web,query",
                    }
                    async with session.get(
                        BRAVE_WEB_ENDPOINT,
                        headers=req_headers,
                        params=params,
                    ) as resp:
                        if not (200 <= resp.status < 300):
                            body = await resp.text()
                            logger.error(
                                "Brave Search unexpected status=%s body_snippet=%s",
                                resp.status,
                                _compact_snippet(body),
                            )
                            return None
                        payload = await resp.json(content_type=None)

                    entries = _parse_brave_web_results(payload)
                    if not entries:
                        break

                    for entry in entries:
                        if "youtube.com" in entry.link or entry.link in seen_links:
                            continue
                        results.append(entry)
                        seen_links.add(entry.link)
                        if len(results) >= max_results:
                            break

                    if len(results) >= max_results:
                        break

                    query_obj = (
                        payload.get("query") if isinstance(payload, dict) else None
                    )
                    more = (
                        query_obj.get("more_results_available")
                        if isinstance(query_obj, dict)
                        else None
                    )
                    if more is False:
                        break

                    await asyncio.sleep(0.05)
        except aiohttp.ClientError as e:
            logger.exception("Brave Search connection error: %s", e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception("Unexpected error in Brave Search flow: %s", e)
            return None

        return results[:max_results]


def create_search_client(
    query: str,
    *,
    headers: dict | None = None,
    query_domains: list[str] | None = None,
    provider: Literal["google", "brave"] | None = None,
) -> SearchClient:
    hdrs = headers or {}
    chosen = (
        provider or hdrs.get("search_provider") or os.environ.get("SEARCH_PROVIDER")
    )
    if not chosen:
        chosen = (
            "brave"
            if os.environ.get("BRAVE_API_KEY") or os.environ.get("BRAVE_SEARCH_API_KEY")
            else "google"
        )
    chosen_norm = str(chosen).strip().lower()
    if chosen_norm == "brave":
        return BraveSearch(query=query, headers=hdrs, query_domains=query_domains)
    if chosen_norm == "google":
        return GoogleSearch(query=query, headers=hdrs, query_domains=query_domains)
    raise ValueError(f"Unknown search provider: {chosen!r}")
