"""
Tests for the configuration system.
"""

import pytest
from pathlib import Path
from src.agent_web_context.config import ConfigManager, get_config_manager


class TestConfigManager:
    """Test cases for ConfigManager."""

    def test_config_manager_initialization(self):
        """Test that ConfigManager can be initialized."""
        config_manager = get_config_manager()
        assert config_manager is not None
        assert isinstance(config_manager, ConfigManager)

    def test_config_file_exists(self):
        """Test that config.yaml exists in project root."""
        config_manager = get_config_manager()
        assert config_manager.config_path.exists()

    def test_get_agent_config(self):
        """Test getting agent configuration."""
        config_manager = get_config_manager()
        agent_config = config_manager.get_agent_config("web_content_analyzer")

        assert agent_config is not None
        assert len(agent_config.models) > 0

        # Check first model has required fields
        first_model = agent_config.models[0]
        assert first_model.provider is not None
        assert first_model.model is not None
        assert first_model.temperature is not None

    def test_get_model_configs(self):
        """Test getting model configurations for an agent."""
        config_manager = get_config_manager()
        model_configs = config_manager.get_model_configs("web_content_analyzer")

        assert len(model_configs) > 0

        # Check model configs have expected providers
        providers = [model.provider for model in model_configs]
        assert "openai" in providers

    def test_nonexistent_agent(self):
        """Test getting config for non-existent agent."""
        config_manager = get_config_manager()
        agent_config = config_manager.get_agent_config("nonexistent_agent")
        assert agent_config is None

        model_configs = config_manager.get_model_configs("nonexistent_agent")
        assert len(model_configs) == 0


class TestModelFallback:
    """Test cases for model fallback functionality."""

    @pytest.mark.asyncio
    async def test_get_working_llm(self):
        """Test getting a working LLM with fallback."""
        config_manager = get_config_manager()

        # This will try each model until one works (or all fail)
        llm, model_config = await config_manager.get_working_llm("web_content_analyzer")

        # If any API keys are configured, we should get a working model
        # If no API keys, both should be None
        if llm is not None:
            assert model_config is not None
            assert hasattr(llm, "ainvoke")  # Should be a LangChain LLM
        else:
            assert model_config is None

    @pytest.mark.asyncio
    async def test_get_working_llm_nonexistent_agent(self):
        """Test fallback for non-existent agent."""
        config_manager = get_config_manager()

        llm, model_config = await config_manager.get_working_llm("nonexistent_agent")

        assert llm is None
        assert model_config is None
