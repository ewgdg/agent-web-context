"""
Research Agent

An iterative search–analyze agent that finds comprehensive answers by using web search
and content analysis in a loop until confident enough. The LLM decides when to exit
the loop and provides final answers with cited references.
"""

import asyncio
import json
import logging
from typing import Annotated, Any, Optional, cast, List
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool, BaseTool

from ..search import SearchResultEntry, create_search_client
from .web_content_analyzer import WebContentAnalyzer, AnalyzeRequest
from ..config import get_config_manager

logger = logging.getLogger(__name__)


class Reference(BaseModel):
    """Reference to a source with relevance scoring."""

    url: str = Field(..., description="Source URL")
    title: str = Field(..., description="Page title")
    relevance: int = Field(..., description="Relevance score 0-100", ge=0, le=100)
    reliability: int = Field(..., description="Reliability score 0-100", ge=0, le=100)


# Tools will be created as instance methods


class Evidence(BaseModel):
    """Evidence collected from content analysis."""

    url: str = Field(..., description="Source URL")
    title: str = Field(..., description="Page title")
    relevance: int = Field(..., description="Relevance score 0-100", ge=0, le=100)
    reliability: int = Field(..., description="Reliability score 0-100", ge=0, le=100)
    short_answer: str = Field(..., description="Concise answer from this source")
    content: str = Field(..., description="Relevant content from this source")


class FinalAnswer(BaseModel):
    """Final answer with references."""

    answer: str = Field(..., description="Comprehensive answer in markdown format")
    references: List[Reference] = Field(
        ..., description="Sources cited in the answer, sorted by relevance"
    )


class ResearchAgent:
    """
    Intelligent search agent that iteratively searches and analyzes content
    until it has enough confidence to provide a comprehensive answer.

    Uses a use-and-discard pattern - each agent instance should handle only
    one query and be discarded after completion.
    """

    # Development-defined constants
    MAX_CONCURRENCY = 5  # Maximum concurrent URL analysis requests

    def __init__(self):
        """Initialize the research agent."""
        self.config_manager = get_config_manager()
        self.llm: Optional[BaseChatModel] = None
        self.agent: Optional[Runnable[Any, AIMessage]] = None
        self.exit_agent: Optional[Runnable[Any, AIMessage]] = None
        self.web_analyzer = WebContentAnalyzer()
        # Session state - reset for each query
        self.evidence_collection: List[Evidence] = []
        self.current_query: Optional[str] = None
        self._is_running = False
        self.tools = self._create_tools()

        # Create prompt template
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._get_system_prompt()),
                ("human", "{user_query}"),
                MessagesPlaceholder("history"),
            ]
        )

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the agent."""
        return """You are a research agent that helps find comprehensive answers by iteratively searching and analyzing web content.

Your goal is to gather enough high-quality evidence to generate a final report to the user's query comprehensively. 

**When to search:**
- Need more sources or different perspectives
- Current evidence is insufficient 
- Want to explore specific aspects of the query

**When to analyze:**
- Found promising URLs from search results
- Want to extract detailed content from specific sources
- Need to verify claims or get more detailed information

**When to report:**
- Have gathered sufficient high-quality evidence from multiple reliable sources
- Can provide a comprehensive, well-sourced answer covering the main aspects of the query
- Have analyzed the most relevant and authoritative sources available
- Further searching is unlikely to significantly improve the answer

**Search Strategy:**
- Use specific, focused queries
- Vary query terms to find diverse sources
- Consider domain restrictions for authoritative sources

**Analysis Strategy:**
- Prioritize authoritative and reliable sources
- Balance quantity vs quality (don't analyze too many low-quality sources)

Always be strategic about your actions and aim for high-quality, comprehensive answers."""

    def _create_tools(self) -> dict[str, BaseTool]:
        """Create tools dictionary with bound methods."""

        @tool
        async def search_web(
            query: str, max_results: int = 10, query_domains: Optional[List[str]] = None
        ) -> str:
            """Search the web for relevant content."""
            self.current_query = query
            res = await self._execute_search_tool(query, max_results, query_domains)
            return json.dumps([item.model_dump() for item in res])

        @tool
        async def analyze_urls(urls: List[str]) -> str:
            """Analyze specific URLs for relevant content."""
            if not self.current_query:
                raise Exception("No query is provided.")
            res = await self._execute_analyze_tool(urls, query=self.current_query)
            self.evidence_collection.extend(res)
            return json.dumps([item.model_dump() for item in res])

        @tool(return_direct=True)
        def report(
            content: Annotated[
                str,
                ...,
                "The full comprehensive final response to the query that directly returned to the user.",
            ],
        ) -> str:
            """Use this tool to complete the search process. A final answer or last response to the user query is required. There is no more conversation after this invocation."""
            return content

        return {tool.name: tool for tool in (search_web, analyze_urls, report)}

    async def init_llm(self) -> None:
        """Initialize the LLM using fallback system."""
        if self.agent is None:
            self.llm, self.model_config = await self.config_manager.get_working_llm(
                "research_agent"
            )
            if self.llm is None:
                raise ValueError("No working models found for research_agent")

            # Set prompt cache key for OpenAI models first
            self.llm = cast(
                BaseChatModel, self.llm.bind(prompt_cache_key=self.__class__.__name__)
            )

            # Bind tools to the LLM
            llm_with_tools = self.llm.bind_tools(
                tools=cast(list[BaseTool], list(self.tools.values())),
                tool_choice="required",
                parallel_tool_calls=True,
            )
            self.agent = cast(Runnable[Any, AIMessage], self.prompt | llm_with_tools)

            exit_only_tools = [
                tool for tool in self.tools.values() if tool.return_direct
            ]
            llm_with_exit_only = self.llm.bind_tools(
                tools=exit_only_tools, tool_choice="required"
            )
            self.exit_agent = cast(
                Runnable[Any, AIMessage], self.prompt | llm_with_exit_only
            )

    def _extract_reasoning_text(self, msg: AIMessage) -> Optional[str]:
        """Extract reasoning text from additional_kwargs for compatible providers.

        Handles various shapes like:
        - {"reasoning": "..."}
        - {"reasoning": {"content": "..."}}
        - {"reasoning_content": "..."}
        - {"thinking": "..."}
        """
        ak = getattr(msg, "additional_kwargs", None) or {}
        if not isinstance(ak, dict):
            return None

        # Prefer explicit "reasoning" field
        val = ak.get("reasoning")
        if val is None:
            val = ak.get("reasoning_content") or ak.get("thinking")

        if val is None:
            return None

        # Normalize into text
        if isinstance(val, str):
            return val.strip() or None
        if isinstance(val, dict):
            # Common nested shapes: {content|text}
            nested = (
                val.get("content")
                if isinstance(val.get("content"), str)
                else val.get("text")
            )
            if isinstance(nested, str):
                return nested.strip() or None
        # Fallback
        try:
            return str(val)
        except Exception:
            return None

    def _inject_reasoning_into_message(self, msg: AIMessage) -> None:
        """Insert <think>reasoning</think> as a separate text block.

        Always injects a leading content block of shape
        {"type": "text", "text": "<think>...</think>"} and preserves the
        original content as its own block.
        """
        # Only apply for openai-compatible providers
        if not getattr(self, "model_config", None):
            return None
        if self.model_config and self.model_config.provider != "openai-compatible":
            return None

        reasoning_text = self._extract_reasoning_text(msg)
        if not reasoning_text:
            return None

        original_content = msg.content
        think_block = {"type": "text", "text": f"<think>{reasoning_text}</think>"}

        new_blocks: list[Any] = [think_block]
        if isinstance(original_content, list):
            new_blocks.extend(original_content)
        else:
            if original_content:
                new_blocks.append({"type": "text", "text": str(original_content)})

        try:
            msg.content = new_blocks
        except Exception:
            logger.exception("Failed to inject <think> block into AIMessage")
        return None

    # No stripping helpers needed since think blocks are only injected when continuing.

    def _calculate_confidence(self, evidence: List[Evidence]) -> float:
        """Calculate overall confidence based on evidence quality."""
        if not evidence:
            return 0.0

        # Use the maximum confidence score from all evidence
        # Confidence = (relevance * reliability) / 100
        confidence_scores = [
            (ev.relevance * ev.reliability) / 10000  # Convert to 0-1 range
            for ev in evidence
        ]

        if not confidence_scores:
            return 0.0

        max_confidence = max(confidence_scores)
        avg_confidence = sum(confidence_scores) / len(confidence_scores)
        return 0.5 * max_confidence + 0.5 * avg_confidence

    def _create_evidence_summary(self, evidence: List[Evidence]) -> str:
        """Create a summary of current evidence for the LLM."""
        if not evidence:
            return "No evidence collected yet."

        summary_lines = []
        for i, ev in enumerate(evidence, 1):
            confidence = (ev.relevance * ev.reliability) / 100
            summary_lines.append(
                f"{i}. [{ev.title}]({ev.url}) - "
                f"Relevance: {ev.relevance}%, Reliability: {ev.reliability}%, "
                f"Confidence: {confidence:.1f}%\n"
                f"   Answer: {ev.short_answer[:100]}{'...' if len(ev.short_answer) > 100 else ''}"
            )

        return "\n\n".join(summary_lines)

    def _generate_references_from_evidence(
        self, evidence: List[Evidence]
    ) -> List[Reference]:
        """Generate references from accumulated evidence, sorted by relevance."""
        references = []
        seen_urls = set()

        # Sort evidence by confidence score (relevance * reliability)
        sorted_evidence = sorted(
            evidence, key=lambda e: e.relevance * e.reliability, reverse=True
        )

        for ev in sorted_evidence:
            if ev.url not in seen_urls:
                references.append(
                    Reference(
                        url=ev.url,
                        title=ev.title,
                        relevance=ev.relevance,
                        reliability=ev.reliability,
                    )
                )
                seen_urls.add(ev.url)

        return references[:15]

    async def _execute_search_tool(
        self,
        query: str,
        max_results: int = 10,
        query_domains: Optional[List[str]] = None,
    ) -> List[SearchResultEntry]:
        """Execute a search action."""
        try:
            # Check for cancellation before starting search
            await asyncio.sleep(0)

            search = create_search_client(query, query_domains=query_domains)
            results = await search.search(max_results=max_results)
            return results or []
        except asyncio.CancelledError:
            logger.info("Search operation cancelled for query: %s", query)
            raise
        except Exception:
            logger.exception("Search failed for query: %s", query)
            return []

    async def _execute_analyze_tool(
        self,
        urls: List[str],
        query: str,
        allow_cache: bool = True,
    ) -> List[Evidence]:
        """Execute analyze action with concurrency control."""
        if not urls:
            return []

        # Check for cancellation before starting analysis
        await asyncio.sleep(0)

        # Initialize web analyzer
        await self.web_analyzer.init_llm()

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)

        async def analyze_single_url(url: str) -> Optional[Evidence]:
            async with semaphore:
                try:
                    # Check for cancellation before analyzing each URL
                    await asyncio.sleep(0)
                    logger.info("analyzing url: %s", url)

                    request = AnalyzeRequest(
                        url=url,
                        query=query,
                        allow_cache=allow_cache,
                    )
                    result = await self.web_analyzer.analyze_url(request)
                    logger.debug("web analyze result: %s", result.model_dump())

                    return Evidence(
                        url=result.url,
                        title=result.title,
                        relevance=result.relevance,
                        reliability=result.reliability,
                        short_answer=result.short_answer,
                        content=result.relevant_content,
                    )
                except asyncio.CancelledError:
                    logger.info("Analysis cancelled for URL: %s", url)
                    raise
                except Exception as e:
                    logger.exception("Analysis failed for URL: %s", url)
                    return Evidence(
                        url=url,
                        title="Error",
                        relevance=0,
                        reliability=0,
                        short_answer="No Answer.",
                        content=f"<error>{str(e)[:300]}</error>",
                    )

        # Execute concurrent analysis
        tasks = (analyze_single_url(url) for url in urls)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out failed results
        evidence = []
        for result in results:
            if isinstance(result, Evidence):
                evidence.append(result)
            elif isinstance(result, asyncio.CancelledError):
                # Re-raise cancellation if any task was cancelled
                raise result
            elif not isinstance(result, Exception):
                logger.warning("Unexpected result type: %s", type(result))

        return evidence

    async def run(
        self,
        user_query: str,
        max_iterations: int = 20,
    ) -> FinalAnswer:
        """
        Run the intelligent search agent to find a comprehensive answer.

        Args:
            user_query: The search query to answer
            max_iterations: Maximum number of iterations

        Returns:
            FinalAnswer with comprehensive response and references

        Raises:
            ValueError: If agent is already running (use-and-discard pattern)
            asyncio.CancelledError: If the operation is cancelled
        """
        # Reject concurrent requests - use-and-discard pattern
        if self._is_running or self.current_query is not None:
            raise ValueError(
                "Agent is already running or has been used. Create a new agent instance for each query."
            )

        try:
            self._is_running = True
            await self.init_llm()

            if self.llm is None or self.agent is None or self.exit_agent is None:
                raise ValueError("Agent not initialized")

            # Reset and initialize session state
            self.current_query = user_query
            self.evidence_collection = []
            final_answer = None

            # Maintain conversation history across iterations
            history: list[BaseMessage] = []

            for iteration in range(max_iterations + 1):
                # Check for cancellation at the start of each iteration
                # Using a small sleep to yield control and check for cancellation
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    logger.info("Search agent cancelled at iteration %d", iteration)
                    raise

                active_agent = self.agent
                if iteration == max_iterations:
                    logger.info(
                        "Maximum iterations reached, forcing agent to provide final answer"
                    )
                    active_agent = self.exit_agent

                # Get next action from LLM
                logger.info("Processing iteration %d", iteration)
                try:
                    response = await active_agent.ainvoke(
                        {"user_query": user_query, "history": history},
                    )

                    # Append response to history as-is first; we'll inject later if continuing
                    history.append(response)

                    has_tool_call = False
                    # Check if LLM wants to use a tool
                    if response.tool_calls:
                        logger.debug(
                            "LLM wants to use %d tools", len(response.tool_calls)
                        )
                        for tool_call in response.tool_calls:
                            # Check for cancellation before each tool execution
                            try:
                                await asyncio.sleep(0)
                            except asyncio.CancelledError:
                                logger.info(
                                    "Search agent cancelled during tool execution"
                                )
                                raise

                            tool_name = tool_call["name"]
                            tool_args = tool_call["args"]

                            tool = self.tools.get(tool_name, None)
                            if not tool:
                                logger.error("Unknown tool: %s", tool_name)
                                continue

                            logger.info(
                                "Executing tool: %s with args: %s", tool_name, tool_args
                            )

                            has_tool_call = True
                            tool_message: ToolMessage = await tool.ainvoke(tool_call)

                            # Add tool message to history
                            history.append(tool_message)

                            if tool.return_direct:
                                # Prepare user-facing text directly from response and tool
                                final_answer = ""
                                response_text = response.text()
                                tool_message_text = tool_message.text() or ""
                                if response_text:
                                    final_answer += response_text
                                if tool_message_text:
                                    if final_answer:
                                        final_answer += "\n\n"
                                    final_answer += tool_message_text
                                break

                    # If we got a direct return from a tool, exit the iteration loop
                    if final_answer is not None:
                        break

                    # If no tool call, force exit with the model's text
                    if not has_tool_call:
                        logger.warning("No tool call in response - forcing exit")
                        final_answer = response.text()
                        break

                    # We did not return a final answer; inject think now for next turn
                    if final_answer is None:
                        self._inject_reasoning_into_message(response)

                except asyncio.CancelledError:
                    logger.info("Search agent cancelled during LLM invocation")
                    raise
                except Exception:
                    logger.exception("LLM action failed on iteration %d", iteration)
                    # Force exit on LLM failure
                    break
                finally:
                    confidence = self._calculate_confidence(self.evidence_collection)
                    # Only log confidence every 5 iterations or on final iteration
                    logger.info(
                        "Iteration %d/%d - Confidence: %.1f%%, Evidence: %d sources",
                        iteration,
                        max_iterations,
                        confidence * 100,
                        len(self.evidence_collection),
                    )

            if not final_answer:
                final_answer = "Unable to find sufficient information to answer this query comprehensively."

            references = self._generate_references_from_evidence(
                self.evidence_collection
            )
            return FinalAnswer(answer=final_answer, references=references)

        except asyncio.CancelledError:
            logger.info("Search agent operation was cancelled")
            raise
        finally:
            # Clean up state - agent is now used and should be discarded
            self._is_running = False
