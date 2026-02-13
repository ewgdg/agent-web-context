import asyncio
import logging
from typing import Sequence, Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

from ..cache import async_cache_result
from ..scraper import Scraper
from ..services import get_service

router = APIRouter(prefix="/scrape", tags=["scraping"])
logger = logging.getLogger(__name__)


class ScrapeResult(BaseModel):
    class ImageData(BaseModel):
        url: str = Field(..., description="Direct URL to the image file")
        score: float = Field(
            ...,
            description="Relevance score (0-1) indicating how relevant this image is to the page content",
        )
        desc: str = Field(
            ..., description="Alt text or caption describing the image content"
        )

    content: str = Field(
        ...,
        description="Clean text content extracted from the webpage, with HTML tags and scripts removed",
    )
    images: Sequence[ImageData] = Field(
        ...,
        description="List of relevant images found on the page with relevance scoring",
    )
    title: str = Field(
        ..., description="Page title extracted from the HTML <title> tag"
    )


class ScrapeRequest(BaseModel):
    urls: list[str] = Field(
        ..., description="List of website URLs to scrape and extract content from"
    )
    allow_cache: bool = Field(
        True, description="Whether to use cached results for faster responses"
    )
    include_image: bool = Field(
        False, description="Whether to include relevant images in the results"
    )
    output_format: Literal["text", "markdown", "html"] = Field(
        "text", description="Output format: 'text', 'markdown', or 'html'"
    )


class ScrapeResponse(BaseModel):
    results: list[ScrapeResult] = Field(
        ..., description="Array of scraping results, one for each URL in the request"
    )


scrape_semaphore = asyncio.Semaphore(20)


async def _scrape(
    url: str,
    scraper: Scraper,
    output_format: Literal["text", "markdown", "html"] = "markdown",
) -> ScrapeResult:
    async with scrape_semaphore:
        # Keep fingerprint clean: let the browser handle PDFs/HTML uniformly.
        content, images, title = await scraper.scrape_async(
            url, output_format=output_format
        )
        return ScrapeResult(
            content=content,
            images=[ScrapeResult.ImageData.model_validate(img) for img in images],
            title=title,
        )


_scrape_with_cache = async_cache_result(
    argument_serializers={str: str},
    result_serializer=ScrapeResult.model_dump_json,
    result_deserializer=ScrapeResult.model_validate_json,
    predicate=lambda x: isinstance(x, ScrapeResult) and len(x.content) > 400,
)(_scrape)


@router.post(
    "",
    summary="Scrape web pages with a real browser",
)
async def fetch_web_content(request: ScrapeRequest) -> ScrapeResponse:
    """
    Extract clean text content, titles, and images from web pages.

    Uses browser automation to handle JavaScript and dynamic content.

    Use as a last resort to get content, since it output excessive content.
    """
    scraper = get_service(Scraper)
    scrape_results = await asyncio.gather(
        *(
            _scrape_with_cache(
                url,
                scraper,
                allow_cache=request.allow_cache,
                output_format=request.output_format,
            )
            for url in request.urls
        )
    )

    res: list[ScrapeResult] = []
    for r in scrape_results:
        if isinstance(r, ScrapeResult):
            if not request.include_image:
                r.images = []
            res.append(r)

    return ScrapeResponse(results=res)


def register_mcp_tools(mcp: FastMCP):
    """Register MCP tools for this router"""
    mcp.tool()(fetch_web_content)
