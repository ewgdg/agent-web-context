"""
Configuration management for Agent Web Context.

Handles loading YAML configuration and providing model fallback logic for agents.
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import yaml
from pydantic import BaseModel, Field, SecretStr


logger = logging.getLogger(__name__)


class ModelConfig(BaseModel):
    """Configuration for a single model."""

    provider: str = Field(
        ...,
        description="Model provider (openai, openai-compatible, anthropic, ollama, llamacpp, google, etc.)",
    )
    model: str = Field(..., description="Model name/identifier")
    api_key_env: Optional[str] = Field(
        None, description="Environment variable for API key"
    )
    base_url: Optional[str] = Field(
        None, description="Base URL for API (e.g., for Ollama)"
    )
    temperature: float = Field(0.3, description="Temperature setting for the model")
    top_p: Optional[float] = Field(
        None, description="Top-p (nucleus sampling) setting for the model"
    )
    reasoning: Optional[Dict[str, Any]] = Field(
        None, description="Reasoning configuration for OpenAI models"
    )
    include: Optional[List[str]] = Field(
        None,
        description=(
            "Responses API 'include' fields (e.g., ['reasoning.encrypted_content'])."
        ),
    )

    # LlamaCpp specific parameters
    model_path: Optional[str] = Field(
        None, description="Path to GGUF model file (for llamacpp)"
    )
    n_ctx: Optional[int] = Field(
        None, description="Token context window size (for llamacpp)"
    )
    n_gpu_layers: Optional[int] = Field(
        None, description="Number of layers to offload to GPU (for llamacpp)"
    )
    n_batch: Optional[int] = Field(
        None, description="Number of tokens to process in parallel (for llamacpp)"
    )
    max_tokens: Optional[int] = Field(
        None, description="Maximum number of tokens to generate"
    )
    f16_kv: Optional[bool] = Field(
        None, description="Use half-precision for key/value cache (for llamacpp)"
    )


class AgentConfig(BaseModel):
    """Configuration for an agent including its model fallback list."""

    models: List[ModelConfig] = Field(..., description="List of models to try in order")


class Config(BaseModel):
    """Main configuration object."""

    models: Dict[str, Dict[str, AgentConfig]] = Field(
        ..., description="Model configurations by category and agent"
    )


class ConfigManager:
    """Manages configuration loading and model fallback logic."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize config manager.

        Args:
            config_path: Path to config file. If None, looks for config.yaml in project root.
        """
        if config_path is None:
            # Find project root by looking for pyproject.toml
            current_dir = Path(__file__).parent
            while current_dir != current_dir.parent:
                if (current_dir / "pyproject.toml").exists():
                    config_path = str(current_dir / "config.yaml")
                    break
                current_dir = current_dir.parent
            else:
                raise FileNotFoundError(
                    "Could not find project root with pyproject.toml"
                )

        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self) -> Config:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, "r") as f:
            raw_config = yaml.safe_load(f)

        # Parse agents configuration
        agents_config = {}
        if "models" in raw_config and "agents" in raw_config["models"]:
            for agent_name, agent_data in raw_config["models"]["agents"].items():
                agents_config[agent_name] = AgentConfig(**agent_data)

        return Config(models={"agents": agents_config})

    def get_agent_config(self, agent_name: str) -> Optional[AgentConfig]:
        """Get configuration for a specific agent."""
        return self.config.models.get("agents", {}).get(agent_name)

    def get_model_configs(self, agent_name: str) -> List[ModelConfig]:
        """Get list of model configurations for an agent."""
        agent_config = self.get_agent_config(agent_name)
        if agent_config is None:
            return []
        return agent_config.models

    def create_llm_instance(self, agent_name: str, model_index: int = 0):
        """
        Create an LLM instance for the specified agent and model index.

        Args:
            agent_name: Name of the agent
            model_index: Index in the model list to use

        Returns:
            LLM instance or None if failed
        """
        model_configs = self.get_model_configs(agent_name)
        if model_index >= len(model_configs):
            return None

        model_config = model_configs[model_index]

        # Get API key for providers that need it
        api_key: SecretStr | None = None
        if model_config.api_key_env:
            raw_api_key = os.getenv(model_config.api_key_env)
            if raw_api_key:
                api_key = SecretStr(raw_api_key)

        try:
            if model_config.provider == "openai":
                from langchain_openai import ChatOpenAI

                # Auto-request encrypted reasoning items when using reasoning models
                include: List[str] = (
                    model_config.include[:] if model_config.include else []
                )
                if "reasoning.encrypted_content" not in include:
                    include.append("reasoning.encrypted_content")

                return ChatOpenAI(
                    model=model_config.model,
                    api_key=api_key,
                    temperature=model_config.temperature,
                    top_p=model_config.top_p,
                    reasoning=model_config.reasoning,
                    # support reasoning.encrypted_content
                    store=False,
                    include=include,
                    output_version="responses/v1",
                    # prefer concise responses
                    # verbosity="low", # todo: add it back when langchain supports it
                    use_responses_api=True,
                )

            elif model_config.provider == "openai-compatible":
                # Use custom subclass that captures extra provider fields
                from .chat_model.custom_chatopenai import ChatOpenAIWithCustomFields

                return ChatOpenAIWithCustomFields(
                    model=model_config.model,
                    api_key=api_key,
                    temperature=model_config.temperature,
                    top_p=model_config.top_p,
                    reasoning_effort=model_config.reasoning.get("effort", None)
                    if model_config.reasoning
                    else None,
                    base_url=model_config.base_url,
                    use_responses_api=False,
                )

            elif model_config.provider == "anthropic":
                from langchain_anthropic import ChatAnthropic

                return ChatAnthropic(
                    model_name=model_config.model,
                    api_key=api_key,  # type: ignore
                    temperature=model_config.temperature,
                    top_p=model_config.top_p,
                    timeout=None,
                    stop=None,
                )

            elif model_config.provider == "ollama":
                from langchain_ollama import ChatOllama

                return ChatOllama(
                    model=model_config.model,
                    base_url=model_config.base_url or "http://localhost:11434",
                    temperature=model_config.temperature,
                    top_p=model_config.top_p,
                    reasoning=bool(model_config.reasoning),
                )

            elif model_config.provider == "google":
                from langchain_google_genai import ChatGoogleGenerativeAI

                return ChatGoogleGenerativeAI(
                    model=model_config.model,
                    api_key=api_key,
                    temperature=model_config.temperature,
                    top_p=model_config.top_p,
                )

            else:
                logger.error(f"Unsupported provider: {model_config.provider}")
                return None

        except Exception as e:
            logger.warning(
                f"Failed to create LLM for {model_config.provider}/{model_config.model}: {e}"
            )
            return None

    async def get_working_llm(
        self, agent_name: str
    ) -> Tuple[Any, Optional[ModelConfig]]:
        """
        Try each model in the agent's configuration until one works.
        Uses lightweight connectivity validation without consuming tokens.

        Args:
            agent_name: Name of the agent

        Returns:
            Tuple of (llm_instance, model_config) or (None, None) if all fail
        """
        model_configs = self.get_model_configs(agent_name)

        for i, model_config in enumerate(model_configs):
            logger.info(
                f"Trying model {i + 1}/{len(model_configs)}: {model_config.provider}/{model_config.model}"
            )

            # Test if the model can be created without errors
            # If create_llm_instance succeeds, it means credentials/config are valid
            llm = self.create_llm_instance(agent_name, i)
            if llm is not None:
                logger.info(
                    f"Successfully connected to {model_config.provider}/{model_config.model}"
                )
                return llm, model_config
            else:
                logger.warning(
                    f"Model {model_config.provider}/{model_config.model} failed to initialize"
                )
                continue

        logger.error(f"All models failed for agent: {agent_name}")
        return None, None


# Global instance
_config_manager = None


def get_config_manager() -> ConfigManager:
    """Get global config manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
