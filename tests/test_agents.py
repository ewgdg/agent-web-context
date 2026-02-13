"""
Tests for the agent system.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.agent_web_context.agents.web_content_analyzer import (
    WebContentAnalyzer,
    AnalyzeRequest,
    LLMExtraction,
    ExtractedContent,
)
from src.agent_web_context.routers.scraping import ScrapeResult, ScrapeResponse


class TestWebContentAnalyzer:
    """Test cases for WebContentAnalyzer."""

    def test_analyzer_initialization(self):
        """Test that WebContentAnalyzer can be initialized."""
        analyzer = WebContentAnalyzer()
        assert analyzer is not None
        assert analyzer.config_manager is not None
        assert analyzer.prompt is not None

    @pytest.mark.asyncio
    async def test_analyze_url_with_mock_content(self, mocker):
        """Test URL analysis with mocked content and agent."""
        analyzer = WebContentAnalyzer()

        # Mock the fetch_web_content function
        mock_scrape_response = ScrapeResponse(
            results=[
                ScrapeResult(
                    content="This is test content about machine learning algorithms.",
                    images=[],
                    title="Test Page",
                )
            ]
        )

        mock_fetch = AsyncMock(return_value=mock_scrape_response)
        mocker.patch(
            "src.agent_web_context.agents.web_content_analyzer.fetch_web_content",
            mock_fetch,
        )

        # Mock the LLM result
        mock_llm_result = LLMExtraction(
            relevant_content="Machine learning algorithms are computational methods that learn patterns from data.",
            relevance=90,
            reliability=95,
            short_answer="Machine learning algorithms learn patterns from data",
            remarks="Content appears well-sourced and accurate",
        )

        # Mock the agent to return our expected result
        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(return_value=mock_llm_result)

        # Mock init_llm to set up the agent
        async def mock_init_llm():
            analyzer.agent = mock_agent

        analyzer.init_llm = mock_init_llm

        # Test the analysis
        request = AnalyzeRequest(
            url="https://example.com",
            query="What are machine learning algorithms?",
            allow_cache=False,
        )

        result = await analyzer.analyze_url(request)

        # Verify the result
        assert result is not None
        assert result.url == "https://example.com"
        assert result.title == "Test Page"
        assert result.relevance == 90
        assert result.reliability == 95
        assert "machine learning" in result.relevant_content.lower()
        assert "learn patterns from data" in result.short_answer.lower()
        assert result.remarks == "Content appears well-sourced and accurate"

        # Verify that the agent was called with correct parameters
        mock_agent.ainvoke.assert_called_once()
        call_args = mock_agent.ainvoke.call_args[0][0]
        assert call_args["query"] == "What are machine learning algorithms?"
        assert call_args["title"] == "Test Page"
        assert "machine learning algorithms" in call_args["content"]

    @pytest.mark.asyncio
    async def test_analyze_url_error_handling(self, mocker):
        """Test error handling when scraping fails."""
        analyzer = WebContentAnalyzer()

        # Mock fetch_web_content to raise an exception
        mocker.patch(
            "src.agent_web_context.agents.web_content_analyzer.fetch_web_content",
            side_effect=Exception("Network error"),
        )

        request = AnalyzeRequest(
            url="https://example.com", query="test query", allow_cache=False
        )

        result = await analyzer.analyze_url(request)

        # Should return error result
        assert result is not None
        assert result.url == "https://example.com"
        assert result.title == "Error"
        assert result.relevance == 0
        assert result.reliability == 0
        assert "failed" in result.short_answer.lower()
        assert "network error" in result.relevant_content.lower()

    @pytest.mark.asyncio
    async def test_analyze_url_no_working_models(self, mocker):
        """Test behavior when no working models are available."""
        analyzer = WebContentAnalyzer()

        # Mock config manager to return no working models
        mocker.patch.object(
            analyzer.config_manager, "get_working_llm", return_value=(None, None)
        )

        request = AnalyzeRequest(
            url="https://example.com", query="test query", allow_cache=False
        )

        result = await analyzer.analyze_url(request)

        # Should return error result
        assert result is not None
        assert result.url == "https://example.com"
        assert result.title == "Error"
        assert result.relevance == 0
        assert result.reliability == 0
        assert "no working models" in result.relevant_content.lower()
