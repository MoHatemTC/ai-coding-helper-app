"""LLM model registry with pre-initialized instances."""

from typing import (
    Any,
    Dict,
    List,
)

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import logger

load_dotenv()


class LLMRegistry:
    """Registry of available LLM models with pre-initialized instances.

    This class maintains a list of LLM configurations and provides
    methods to retrieve them by name with optional argument overrides.
    """

    LLMS: List[Dict[str, Any]] = [
        {
            "name": settings.DEFAULT_LLM_MODEL,
            "llm": ChatOpenAI(
                model=settings.DEFAULT_LLM_MODEL,
                base_url=settings.LITELLM_BASE_URL,
                api_key=SecretStr(settings.LITELLM_API_KEY),
            ),
            "llm_class": ChatOpenAI,
            "constructor_kwargs": {
                "base_url": settings.LITELLM_BASE_URL,
                "api_key": settings.LITELLM_API_KEY,
            },
        },
        {
            "name": settings.LITE_LLM_MODEL,
            "llm": ChatOpenAI(
                model=settings.LITE_LLM_MODEL,
                base_url=settings.LITELLM_BASE_URL,
                api_key=SecretStr(settings.LITELLM_API_KEY),
            ),
            "llm_class": ChatOpenAI,
            "constructor_kwargs": {
                "base_url": settings.LITELLM_BASE_URL,
                "api_key": settings.LITELLM_API_KEY,
            },
        },
    ]

    @classmethod
    def get(cls, model_name: str, **kwargs) -> BaseChatModel:
        """Get an LLM by name with optional argument overrides.

        When kwargs are provided a fresh instance of the correct LLM class
        is returned with those overrides applied, leaving the shared registry
        entry untouched.

        Args:
            model_name: Name of the model to retrieve.
            **kwargs: Optional arguments to override default model configuration.

        Returns:
            BaseChatModel instance.

        Raises:
            ValueError: If model_name is not found in LLMS.
        """
        model_entry = next((e for e in cls.LLMS if e["name"] == model_name), None)

        if not model_entry:
            available = ", ".join(e["name"] for e in cls.LLMS)
            raise ValueError(f"model '{model_name}' not found in registry. available models: {available}")

        if kwargs:
            llm_class = model_entry["llm_class"]
            extra = model_entry.get("constructor_kwargs", {})
            logger.debug(
                "creating_llm_with_custom_args",
                model_name=model_name,
                llm_class=llm_class.__name__,
                custom_args=list(kwargs.keys()),
            )
            return llm_class(model=model_name, **extra, **kwargs)

        logger.debug("using_default_llm_instance", model_name=model_name)
        return model_entry["llm"]

    @classmethod
    def get_all_names(cls) -> List[str]:
        """Return all registered model names in order.

        Returns:
            List of model name strings.
        """
        return [e["name"] for e in cls.LLMS]

    @classmethod
    def get_model_at_index(cls, index: int) -> Dict[str, Any]:
        """Return the model entry at a specific index, wrapping to 0 if out of range.

        Args:
            index: Index into LLMS.

        Returns:
            Model entry dict.
        """
        if 0 <= index < len(cls.LLMS):
            return cls.LLMS[index]
        return cls.LLMS[0]
