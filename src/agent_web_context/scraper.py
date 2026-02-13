from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path
import random
import traceback
from typing_extensions import Literal
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from typing import Any, Dict, cast, Tuple, Optional, AsyncGenerator
import asyncio
import logging
import os
from patchright.async_api import BrowserContext, Page, Playwright, async_playwright

from .utils import (
    get_relevant_images,
    extract_title,
    get_text_from_soup,
    get_markdown_from_soup,
    clean_soup,
)


@asynccontextmanager
async def scraper_context_manager(
    user_data_dir: str | None = None,
) -> AsyncGenerator["Scraper", None]:
    """Async context manager for Scraper"""
    scraper = Scraper(user_data_dir=user_data_dir)
    try:
        yield scraper
    finally:
        await scraper.cleanup_on_exit()


class Scraper:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    @staticmethod
    def get_domain(url: str) -> str:
        domain = urlparse(url=url).netloc
        parts = domain.split(".")
        if len(parts) > 2:
            domain = ".".join(parts[-2:])
        return domain

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url)
        if not parsed.scheme:
            return "https://" + url
        return url

    @asynccontextmanager
    async def rate_limit_for_domain(self, url: str) -> AsyncGenerator[None, None]:
        """Rate limiting per domain across all scraping requests"""
        try:
            domain = self.get_domain(url)

            semaphore = self._domain_semaphores.get(domain)
            if not semaphore:
                semaphore = asyncio.Semaphore(1)
                self._domain_semaphores[domain] = semaphore

            was_locked = semaphore.locked()
        except Exception as e:
            self.logger.exception(
                f"Rate limiting error for {url}: {str(e)}",
                extra={"url": url, "domain": locals().get("domain")},
            )
            raise

        async with semaphore:
            if was_locked:
                await asyncio.sleep(random.uniform(0.6, 1.2))
            yield

    @staticmethod
    async def natural_scroll(page: Page, delta_y: int, speed: float = 1.0) -> None:
        """Simulate natural human-like scrolling with easing and speed control, ~1600 px/sec"""
        max_chunk = int(120 * speed)  # Base wheel event size
        remaining = abs(delta_y)
        direction = 1 if delta_y > 0 else -1

        while remaining > 0:
            # Variable chunk sizes with slight randomness
            chunk_size = min(remaining, random.randint(int(max_chunk * 0.5), max_chunk))
            actual_chunk = chunk_size * direction

            # Add human-like jitter
            jitter = random.randint(-3, 3)
            await page.mouse.wheel(0, actual_chunk + jitter)

            remaining -= chunk_size

            if remaining > 0:
                # Speed-adjusted delay with randomness
                base_delay = (0.05 / speed) + random.uniform(-0.01, 0.02)
                await asyncio.sleep(max(0.01, base_delay))

    @staticmethod
    async def scroll_page_to_bottom(page: Page, max_scroll_percent: int = 500) -> None:
        """Scroll page to bottom with realistic behavior"""
        total_scroll_percent = 0

        while True:
            try:
                # Bring page to front before each scroll operation
                await page.bring_to_front()

                scroll_percent = random.randint(46, 97)
                total_scroll_percent += scroll_percent

                # Get viewport height for natural scrolling
                viewport = page.viewport_size
                if not viewport:
                    # Fallback to default viewport size
                    scroll_distance = int(800 * scroll_percent / 100)
                else:
                    scroll_distance = int(viewport["height"] * scroll_percent / 100)

                # Use natural human-like scrolling
                scroll_speed = random.uniform(
                    1.0, 1.7
                )  # Vary scrolling speed (1600-2800 px/sec)
                await asyncio.wait_for(
                    Scraper.natural_scroll(page, scroll_distance, scroll_speed),
                    timeout=6,
                )

                await asyncio.sleep(random.uniform(0.23, 0.56))
                await Scraper.wait_or_timeout(page, "load", 2)

                if total_scroll_percent >= max_scroll_percent:
                    break

                # Check if at bottom
                at_bottom = await asyncio.wait_for(
                    page.evaluate(
                        "window.innerHeight + window.scrollY >= document.scrollingElement.scrollHeight"
                    ),
                    timeout=3,
                )
                if cast(bool, at_bottom):
                    break
            except asyncio.TimeoutError:
                Scraper.logger.warning("Scrolling timed out, assuming at bottom")
                break
            except Exception as e:
                Scraper.logger.warning(f"Error during scrolling: {e}")
                break

    @staticmethod
    async def wait_or_timeout(
        page: Page,
        until: Literal["domcontentloaded", "load", "networkidle"] = "load",
        timeout: float = 3,
    ) -> None:
        """Wait for page load state with timeout"""
        try:
            await asyncio.wait_for(
                page.wait_for_load_state(until, timeout=timeout * 1000), timeout
            )
        except asyncio.TimeoutError:
            Scraper.logger.warning(
                f"timeout waiting for {until} after {timeout} seconds",
                extra={"until": until, "timeout": timeout},
            )

    async def _perform_scrape_operation(
        self, page: Page, url: str, output_format: Literal["text", "markdown", "html"]
    ) -> Tuple[str, list[dict[str, Any]], str]:
        """Perform the actual scraping operation on a page"""
        # Use browser navigation response to keep fingerprint clean and detect PDFs reliably
        response = await page.goto(url)

        # If navigation returns a PDF, parse it directly from the response body
        try:
            content_type = (
                (response.headers.get("content-type") if response else "") or ""
            ).lower()
            content_disp = (
                (response.headers.get("content-disposition") if response else "") or ""
            ).lower()
            # Robust, fingerprint-clean detection via response headers only
            is_pdf = ("application/pdf" in content_type) or (
                "filename=" in content_disp and ".pdf" in content_disp
            )
            if is_pdf and response is not None:
                try:
                    pdf_bytes = await response.body()
                    content, title = Scraper._parse_pdf_to_markdown(pdf_bytes)
                    return content, [], title
                except Exception as e:
                    # Any issue in detection/parsing: proceed with standard HTML flow
                    self.logger.warning(f"PDF handling failed; continue as HTML: {e}")
        except Exception:
            # Any issue in detection/parsing: proceed with standard HTML flow
            pass

        if page is None:
            self.logger.error(f"Failed to open page for {url}: page is None")
            return f"Failed to open page for {url}: page is None", [], ""

        await self.wait_or_timeout(page, "load", 5)
        # wait for potential redirection
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await self.wait_or_timeout(page, "networkidle", 2)

        await self.scroll_page_to_bottom(page)

        content_timeout = 10
        try:
            html = await asyncio.wait_for(page.content(), timeout=content_timeout)
        except asyncio.TimeoutError:
            self.logger.error(
                f"Timeout getting content for {url}",
                extra={"url": url, "timeout": content_timeout},
            )
            return (
                f"Timeout getting content for {url} after {content_timeout} seconds",
                [],
                "",
            )

        soup = BeautifulSoup(html, "lxml")
        title = extract_title(soup)

        decompose_irrelevant_imgs = True
        if output_format == "html":
            decompose_irrelevant_imgs = False
        image_urls = get_relevant_images(
            soup,
            url,
            title,
            decompose_irrelevant=decompose_irrelevant_imgs,
        )

        if output_format == "html":
            content = str(soup)
        elif output_format == "markdown":
            soup = clean_soup(soup)
            content = get_markdown_from_soup(soup)
        else:  # text format (default)
            soup = clean_soup(soup)
            content = get_text_from_soup(soup)

        if len(content) < 400:
            self.logger.warning(
                f"Content is too short from {url}. Title: {title}, Content length: {len(content)},\\n"
                f"excerpt: {content}."
            )
            if self.debug:
                screenshot_dir = Path("logs/screenshots")
                screenshot_dir.mkdir(exist_ok=True)
                screenshot_path = (
                    screenshot_dir / f"screenshot-error-{Scraper.get_domain(url)}.png"
                )
                try:
                    await asyncio.wait_for(
                        page.screenshot(path=screenshot_path), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    self.logger.warning(f"Screenshot timeout for {url}, continuing...")
                except Exception as screenshot_error:
                    self.logger.warning(
                        f"Screenshot failed for {url}: {screenshot_error}"
                    )
                self.logger.warning(
                    f"check screenshot at [{screenshot_path}] for more details."
                )

        return content, image_urls, title

    @staticmethod
    def _parse_pdf_to_markdown(pdf_bytes: bytes) -> Tuple[str, str]:
        """Convert PDF bytes to (markdown, title) using PyMuPDF and pymupdf4llm."""
        import pymupdf
        import pymupdf4llm

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        content = pymupdf4llm.to_markdown(doc)
        title = (
            doc.metadata.get("title", "pdf_document")
            if doc.metadata
            else "pdf_document"
        )
        return content, title

    async def _release_context(self) -> None:
        """Clean up context and driver when no active scraping pages remain"""
        try:
            if self._shared_context:
                # Check refcount without holding lock
                async with self._context_lock:
                    should_release = self._context_refcount <= 0

                if should_release:
                    # Wait without holding lock for incoming requests
                    await asyncio.sleep(0.5)

                    # Double-check after delay
                    async with self._context_lock:
                        if self._context_refcount <= 0 and self._shared_context:
                            await self._shared_context.close()
                            self._shared_context = None
                            if self._shared_driver:
                                await self._shared_driver.stop()
                                self._shared_driver = None
        except Exception as e:
            self.logger.exception(
                f"Failed to cleanup context/driver: {type(e).__name__}: {str(e)}"
            )

    async def cleanup_on_exit(self) -> None:
        """Clean up shared resources on exit"""
        try:
            if self._shared_context:
                async with self._context_lock:
                    await self._shared_context.close()
                    self._shared_context = None
            if self._shared_driver:
                await self._shared_driver.stop()
                self._shared_driver = None
        except Exception:
            pass

    async def _cleanup_chrome_locks(self) -> None:
        """Remove Chrome profile lock files that may prevent browser startup"""
        try:
            # Kill any existing Chrome processes first
            import subprocess

            try:
                result = subprocess.run(
                    ["pkill", "-f", "chrome"], capture_output=True, text=True
                )
                if result.returncode == 0:
                    self.logger.info("Killed existing Chrome processes")
                await asyncio.sleep(1)  # Give processes time to terminate
            except Exception as e:
                self.logger.debug(f"No Chrome processes to kill or pkill failed: {e}")

            lock_files = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
            removed_count = 0
            for lock_file in lock_files:
                lock_path = Path(self._user_data_dir) / lock_file
                if lock_path.exists() or lock_path.is_symlink():
                    lock_path.unlink(missing_ok=True)
                    removed_count += 1

            if removed_count > 0:
                self.logger.info(f"Removed {removed_count} Chrome lock files")
        except Exception as e:
            self.logger.error(f"Failed to cleanup Chrome locks: {e}", exc_info=True)

    async def _ensure_shared_context(self, headless: bool = False) -> None:
        """Ensure we have a shared persistent context"""
        if self._shared_driver is None:
            self._shared_driver = await async_playwright().start()

        if self._shared_context is None:
            # Clean up Chrome lock files before launching
            await self._cleanup_chrome_locks()

            self._shared_context = (
                await self._shared_driver.chromium.launch_persistent_context(
                    user_data_dir=self._user_data_dir,
                    headless=headless,
                    channel="chrome",  # Use real Chrome
                    no_viewport=True,
                    args=["--ozone-platform-hint=wayland"],
                )
            )

        # Ensure _shared_context is not None after initialization
        assert self._shared_context is not None, "Context initialization failed"

    @asynccontextmanager
    async def get_context(
        self, headless: bool = False
    ) -> AsyncGenerator[BrowserContext, None]:
        """Get the shared persistent context with reference counting"""
        async with self._context_lock:
            await self._ensure_shared_context(headless)
            self._context_refcount += 1
        try:
            yield cast(BrowserContext, self._shared_context)
        finally:
            async with self._context_lock:
                self._context_refcount -= 1

    def __init__(self, user_data_dir: str | None = None) -> None:
        self.debug: bool = True
        self._shared_driver: Optional[Playwright] = None
        self._shared_context: Optional[BrowserContext] = None
        self._context_lock: asyncio.Lock = asyncio.Lock()
        self._context_refcount: int = 0
        self._domain_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._max_tabs: int = 10
        self._tab_semaphore: asyncio.Semaphore = asyncio.Semaphore(self._max_tabs)
        self._user_data_dir = user_data_dir or "./browser_data"

    async def scrape_async(
        self,
        url: str,
        max_retries: int = 1,
        output_format: Literal["text", "markdown", "html"] = "markdown",
        timeout: float = 30.0,
    ) -> Tuple[str, list[dict[str, Any]], str]:
        url = Scraper.normalize_url(url)
        if not url:
            return (
                "A URL was not specified, cancelling request to browse website.",
                [],
                "",
            )

        for attempt in range(max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._scrape_attempt(url, output_format), timeout=timeout
                )
            except Exception as e:
                is_last_attempt = attempt == max_retries
                attempt_info = f" (attempt {attempt + 1}/{max_retries + 1})"

                if is_last_attempt:
                    self.logger.exception(
                        f"An error occurred during scraping{attempt_info}",
                        extra={
                            "url": url,
                            "attempt": attempt + 1,
                            "max_retries": max_retries + 1,
                        },
                    )
                    return type(e).__name__, [], ""
                else:
                    self.logger.warning(
                        f"An error occurred during scraping{attempt_info}: {str(e)}. Retrying...",
                        # exc_info=True,
                        extra={
                            "url": url,
                            "attempt": attempt + 1,
                            "max_retries": max_retries + 1,
                        },
                    )
                    await asyncio.sleep(random.uniform(1.0, 2.0))

        # This should never be reached due to the logic above, but added for completeness
        return "Maximum retry attempts exceeded", [], ""

    async def _scrape_attempt(
        self, url: str, output_format: Literal["text", "markdown", "html"]
    ) -> Tuple[str, list[dict[str, Any]], str]:
        """Single scraping attempt with all setup and cleanup"""
        page: Optional[Page] = None
        try:
            async with AsyncExitStack() as stack:
                # Acquire resources in order: rate limit, then semaphore, then context
                await stack.enter_async_context(self.rate_limit_for_domain(url))
                await stack.enter_async_context(self._tab_semaphore)
                context = await stack.enter_async_context(self.get_context())

                page = await context.new_page()
                return await self._perform_scrape_operation(page, url, output_format)
        finally:
            # Clean up the page after AsyncExitStack has released all resources
            if page:
                # Fire and forget cleanup to avoid blocking the main flow
                asyncio.create_task(self._release_page(page, url))

    async def _release_page(self, page: Page, url: str) -> None:
        """Clean up page resources asynchronously"""
        try:
            await asyncio.wait_for(page.close(), timeout=10.0)
            await self._release_context()
        except asyncio.TimeoutError:
            self.logger.error(
                "Page close timed out after 10 seconds",
                extra={"url": url},
            )
        except Exception as cleanup_error:
            self.logger.exception(
                f"Failed to close page: {type(cleanup_error).__name__}: {str(cleanup_error)}",
                extra={"url": url},
            )
