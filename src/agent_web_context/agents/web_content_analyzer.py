"""
Basic Web Content Analyzer Agent

A simple agent that uses LangChain and OpenAI to extract relevant content
from a single web page based on a query, optimized for token efficiency.
"""

from typing import Optional, cast

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

import logging

from ..routers.scraping import ScrapeRequest, fetch_web_content
from ..config import get_config_manager

logger = logging.getLogger(__name__)


class LLMExtraction(BaseModel):
    """Structured output from LLM for content analysis."""

    relevant_content: str = Field(
        description="Content relevant to the query in markdown format, modified based on relevance and reliability"
    )
    relevance: int = Field(
        description="0-100 percentage representing how well content matches/answers/relates to the query",
        ge=0,
        le=100,
    )
    reliability: int = Field(
        description="0-100 percentage representing how trustworthy the content/source is",
        ge=0,
        le=100,
    )
    short_answer: str = Field(
        description="Concise direct answer to the query based on extracted content"
    )
    remarks: str = Field(
        default="",
        description="Optional concise notes only for valuable insights about reliability, bias, or notable limitations",
    )


class AnalyzeRequest(BaseModel):
    """Request model for content analysis."""

    url: str = Field(..., description="URL to analyze for relevant content")
    query: str = Field(..., description="Query describing what content to extract")
    allow_cache: bool = Field(True, description="Whether to use cached results")


class ExtractedContent(LLMExtraction):
    """Final result combining scraped metadata with LLM analysis."""

    url: str = Field(..., description="The analyzed URL")
    title: str = Field(..., description="Page title")


class WebContentAnalyzer:
    """
    Simple web content analyzer that uses AI to extract
    relevant content from a single URL based on user queries.
    """

    def __init__(self):
        """Initialize the analyzer with model fallback support."""
        self.config_manager = get_config_manager()
        self.llm: Optional[BaseChatModel] = None
        self.agent: Optional[Runnable] = None

        # Create prompt template
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._get_system_prompt()),
                (
                    "human",
                    "Web Page Title: {title}\n\nWeb Page Content:\n<content>{content}</content>",
                ),
                (
                    "human",
                    "Query: {query}\n\n"
                    "<system-hint>Extract relevant content, assess its reliability, and modify based on confidence level.</system-hint>",
                ),
            ]
        )

    def _get_system_prompt(self) -> str:
        """Get the system prompt for content extraction."""
        return """You are an expert content extraction agent. Your task is to:

1. Extract ONLY the content from the provided web page that is directly relevant to the user's query
2. Assess both relevance and reliability of the extracted content
3. Modify content based on both scores: filter irrelevant content, adjust presentation based on reliability
4. Preserve important details and data points while being concise
5. Format the extracted content in clean markdown

Guidelines:
- Be extremely selective - only include content that directly answers or relates to the query
- Preserve specific facts, numbers, dates, and key details from reliable sources
- Remove navigation, ads, and irrelevant sections
- Use concise language but don't lose critical information
- Maintain markdown formatting for headers, links, lists, and emphasis to preserve structure

Content Modification Rules:
- For each piece of info, the compression ratio is determined by the confidence score, evaluated based on the product score of relevance and reliability
- High confidence (80-100%): Include full relevant content
- Medium confidence (50-79%): Include but compress/summarize the content
- Low confidence (20-49%): Heavily compress or mention briefly with caveats
- Very low confidence (0-19%): Remove content or replace with disclaimer

Reliability Assessment Factors:
- Source credibility (domain, authorship, citations)
- Content quality (factual, well-sourced, recent)
- Presence of supporting evidence
- Consistency and logical structure

Relevance Assessment Factors:
- Direct answer to query (90-100%)
- Contextual information that helps answer (70-90%)
- Topic/subject alignment (50-70%)
- Tangentially related content (20-50%)
- Unrelated content (0-20%)

Remarks Guidelines:
- Keep remarks empty unless there's valuable insight to share
- Only include concise notes about significant bias, reliability concerns, or data limitations
- Avoid generic statements about confidence - the score already captures that"""

    async def init_llm(self) -> None:
        """Initialize the LLM using fallback system."""
        if self.agent is None:
            self.llm, model_config = await self.config_manager.get_working_llm(
                "web_content_analyzer"
            )
            if self.llm is None:
                raise ValueError("No working models found for web_content_analyzer")

            # Build the complete agent chain
            structured_llm = self.llm.with_structured_output(LLMExtraction)

            # Set prompt cache key for OpenAI models
            if model_config and model_config.provider == "openai":
                structured_llm = structured_llm.bind(
                    prompt_cache_key=self.__class__.__name__
                )

            self.agent = self.prompt | structured_llm

    async def analyze_url(self, request: AnalyzeRequest) -> ExtractedContent:
        """
        Analyze a single URL and extract content relevant to the query.

        Args:
            request: AnalyzeRequest containing URL, query, and cache settings

        Returns:
            ExtractedContent with relevant information and confidence score
        """
        try:
            # Initialize LLM if needed
            await self.init_llm()

            # Fetch content using cached scraper
            scrape_request = ScrapeRequest(
                urls=[request.url],
                allow_cache=request.allow_cache,
                include_image=False,
                output_format="markdown",
            )
            response = await fetch_web_content(scrape_request)

            if not response.results:
                raise ValueError("No results from scraper")

            result = response.results[0]
            raw_content = result.content
            title = result.title

            # Use agent to extract relevant content
            if self.agent is None:
                raise ValueError("Agent not initialized")

            llm_result = cast(
                LLMExtraction,
                await self.agent.ainvoke(
                    {
                        "title": title,
                        "content": raw_content,
                        "query": request.query,
                    },
                ),
            )

            return ExtractedContent(
                url=request.url,
                title=title,
                relevant_content=llm_result.relevant_content,
                relevance=llm_result.relevance,
                reliability=llm_result.reliability,
                short_answer=llm_result.short_answer,
                remarks=llm_result.remarks,
            )

        except Exception as e:
            logger.exception(
                "WebContentAnalyzer.analyze_url failed for %s", request.url
            )
            # Create error result
            return ExtractedContent(
                url=request.url,
                title="Error",
                relevant_content=f"Failed to analyze {request.url}: {str(e)}",
                relevance=0,
                reliability=0,
                short_answer="Analysis failed",
            )
