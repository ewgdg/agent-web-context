import asyncio
import math
import os
import urllib.parse
import aiohttp
import logging
import textwrap

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SearchResultEntry(BaseModel):
    title: str
    link: str = Field(..., description="url of the website")
    snippet: str = Field(..., description="a snippet of the website")


class GoogleSearch:
    """
    Google API Retriever
    """

    def __init__(self, query, headers=None, query_domains=None):
        """
        Initializes the GoogleSearch object
        Args:
            query:
        """
        self.query = query
        self.headers = headers or {}
        self.query_domains = query_domains or None
        self.api_key = (
            self.headers.get("google_api_key") or self.get_api_key()
        )  # Use the passed api_key or fallback to environment variable
        self.cx_key = (
            self.headers.get("google_cx_key") or self.get_cx_key()
        )  # Use the passed cx_key or fallback to environment variable

    def get_api_key(self):
        """
        Gets the Google API key
        Returns:

        """
        # Get the API key
        try:
            api_key = os.environ["GOOGLE_API_KEY"]
        except Exception:
            raise Exception(
                "Google API key not found. Please set the GOOGLE_API_KEY environment variable. "
                "You can get a key at https://developers.google.com/custom-search/v1/overview"
            )
        return api_key

    def get_cx_key(self):
        """
        Gets the Google CX key
        Returns:

        """
        # Get the API key
        try:
            api_key = os.environ["GOOGLE_CX_KEY"]
        except Exception:
            raise Exception(
                "Google CX key not found. Please set the GOOGLE_CX_KEY environment variable. "
                "You can get a key at https://developers.google.com/custom-search/v1/overview"
            )
        return api_key

    async def search(self, max_results: int = 10) -> list["SearchResultEntry"] | None:
        """
        Searches the query using Google Custom Search API, optionally restricting to specific domains.
        Returns:
            list: List of search results with title, link, and snippet, or None on fatal error.
        """
        results_per_request = 10  # Google Custom Search's max per call
        total_results_to_fetch = min(100, max_results)
        seen_links = set()

        # Build query with domain restrictions if specified
        search_query = self.query
        if getattr(self, "query_domains", None):
            domains = getattr(self, "query_domains")
            if domains and len(domains) > 0:
                domain_query = " OR ".join([f"site:{domain}" for domain in domains])
                search_query = f"({domain_query}) {self.query}"

        encoded_query = urllib.parse.quote_plus(search_query)
        logger.info(f"Google CSE searching for: {search_query!r}")

        res = []
        try:
            async with aiohttp.ClientSession() as session:
                # Calculate how many pages we need to fetch
                pages = math.ceil(total_results_to_fetch / results_per_request)

                for page in range(pages):
                    start_index = (
                        page * results_per_request + 1
                    )  # Google API is 1-based

                    url = (
                        f"https://www.googleapis.com/customsearch/v1"
                        f"?key={self.api_key}"
                        f"&cx={self.cx_key}&q={encoded_query}&start={start_index}"
                    )

                    logger.debug(
                        textwrap.dedent(f"""
                        Requesting Google CSE page,
                        query: {self.query},
                        page: {page + 1},
                        start_index: {start_index},
                        """).strip()
                    )

                    try:
                        async with session.get(url) as resp:
                            if not (200 <= resp.status < 300):
                                body = await resp.json()
                                logger.error(
                                    textwrap.dedent(f"""
                                        Google search: unexpected response status,
                                        status: {resp.status},
                                        query: {self.query},
                                        page: {page + 1},
                                        start_index: {start_index},
                                        error: {body.get("error", {})}
                                        """).strip()
                                )
                                return None
                            search_results = await resp.json()
                            if "error" in search_results:
                                err = search_results.get("error", {})
                                logger.error(
                                    textwrap.dedent(f"""
                                    Google CSE API returned error,
                                    query: {self.query},
                                    page: {page + 1},
                                    start_index: {start_index},
                                    api_error: {err},
                                    """).strip()
                                )
                                return None
                    except aiohttp.ClientError as e:
                        logger.exception(
                            textwrap.dedent(f"""
                            Google CSE connection error: {e},
                            query: {self.query},
                            page: {page + 1},
                            start_index: {start_index},
                            """).strip()
                        )
                        return None
                    except Exception as e:
                        logger.exception(
                            textwrap.dedent(f"""
                            Error retrieving or parsing Google API response: {e},
                            query: {self.query},
                            page: {page + 1},
                            start_index: {start_index},
                            """).strip()
                        )
                        return None

                    items = search_results.get("items", [])
                    if not items:
                        logger.info(
                            textwrap.dedent(f"""
                            No more items returned by the API,
                            query: {self.query},
                            page: {page + 1},
                            """).strip()
                        )
                        break

                    for item in items:
                        link = item.get("link", "")
                        # skip youtube results, and duplicates
                        if "youtube.com" in link or link in seen_links:
                            continue
                        title = item.get("title", "")
                        snippet = item.get("snippet", "")
                        if not (link and title):
                            continue
                        try:
                            search_result = SearchResultEntry(
                                title=title,
                                link=link,
                                snippet=snippet,
                            )
                            res.append(search_result)
                            seen_links.add(link)
                        except Exception as e:
                            logger.exception(
                                textwrap.dedent(f"""
                                Error creating SearchResultEntry: {e},
                                link: {link},
                                title: {title},
                                """).strip()
                            )
                            continue

                        if len(res) >= max_results:
                            break

                    if len(res) >= max_results:
                        break

                    # If we get fewer than the per-request max, don't bother querying more
                    if len(items) < results_per_request:
                        break

                    # Respect possible API throttling.
                    await asyncio.sleep(0.1)
        except Exception as e:
            logger.exception(
                textwrap.dedent(f"""
                Unexpected error in Google Custom Search flow: {e},
                query: {self.query},
                """).strip()
            )
            return None

        return res[:max_results]
