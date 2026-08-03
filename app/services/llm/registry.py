"""LLM model registry with pre-initialized instances."""

from typing import (
    Any,
    Dict,
    List,
)

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import logger

_TOKEN_LIMIT: Dict[str, Any] = {"max_completion_tokens": settings.MAX_TOKENS}
_API_KEY = SecretStr(settings.LITELLM_API_KEY)
_BASE_URL = settings.LITELLM_BASE_URL


class LLMRegistry:
    """Registry of available LLM models with pre-initialized instances.

    This class maintains a list of LLM configurations and provides
    methods to retrieve them by name with optional argument overrides.
    """

<<<<<<< HEAD
    # NOTE (22 Jul): entry "name" is a stable lookup key referenced elsewhere
    # (settings.DEFAULT_LLM_MODEL == "fw-kimi-k2.6"); it intentionally no
    # longer matches its own "model" string below -- see the 22 Jul comment
    # on that entry. Do not rename "fw-kimi-k2.6" without also updating
    # DEFAULT_LLM_MODEL in app/core/config.py.
    #
    # A third entry (name="kimi-k2.5", model="kimi-k2.5") was removed here:
    # it was a byte-for-byte duplicate of the entry below once "fw-kimi-k2.6"
    # was repointed at the same underlying "kimi-k2.5" model, so it added a
    # fallback *slot* without adding a fallback *model* -- the circular
    # fallback loop would have retried the same already-failed model twice.
    LLMS: List[Dict[str, Any]] = [
<<<<<<< HEAD
        {
            "name": "fw-kimi-k2.6",
            "llm": ChatOpenAI(
                model="kimi-k2.5",
                api_key=_API_KEY,
                base_url=_BASE_URL,
                temperature=settings.DEFAULT_LLM_TEMPERATURE,
                model_kwargs={"max_completion_tokens": 6000},
                use_responses_api=False,
            ),
        },
        {
            "name": "kimi-k2.6",
            "llm": ChatOpenAI(
                model="kimi-k2.6",
                api_key=_API_KEY,
                base_url=_BASE_URL,
                model_kwargs=_TOKEN_LIMIT,
=======
    LLMS: List[Dict[str, Any]] = [
        {
            "name": "gpt-5-mini",
            "llm": ChatOpenAI(
                model="gpt-5-mini",
                api_key=_API_KEY,
                model_kwargs=_TOKEN_LIMIT,
                reasoning={"effort": "low"},
            ),
        },
        {
            "name": "gpt-5.4",
            "llm": ChatOpenAI(
                model="gpt-5",
                api_key=_API_KEY,
                model_kwargs=_TOKEN_LIMIT,
                reasoning={"effort": "medium"},
            ),
        },
        {
            "name": "gpt-5.4-nano",
            "llm": ChatOpenAI(
                model="gpt-5.4-nano",
                api_key=_API_KEY,
                model_kwargs=_TOKEN_LIMIT,
                reasoning={"effort": "low"},
            ),
        },
        {
            "name": "gpt-5",
            "llm": ChatOpenAI(
                model="gpt-5",
                api_key=_API_KEY,
                model_kwargs=_TOKEN_LIMIT,
                top_p=0.95 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.8,
                presence_penalty=0.1 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.0,
                frequency_penalty=0.1 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.0,
>>>>>>> d372769 (Coding Helper — AI Mentor & Senior Code Reviewer (FastAPI + LangGraph))
            ),
        },
    ]
=======
    {
        "name": "gemini/gemini-3-flash-preview",
        "llm": ChatOpenAI(
            model="gemini/gemini-3-flash-preview",
            api_key=_API_KEY,
            base_url=_BASE_URL,
            temperature=settings.DEFAULT_LLM_TEMPERATURE,
            model_kwargs={"max_completion_tokens": 6000},
            use_responses_api=False,
        ),
    },
    {
        "name": "gemini/gemini-3.1-flash-lite",
        "llm": ChatOpenAI(
            model="gemini/gemini-3.1-flash-lite",
            api_key=_API_KEY,
            base_url=_BASE_URL,
            temperature=settings.DEFAULT_LLM_TEMPERATURE,
            model_kwargs={"max_completion_tokens": 6000},
            use_responses_api=False,
        ),
    },
]
    
>>>>>>> 2d7e6be (edit files needed to be edited)

    @classmethod
    def get(cls, model_name: str, **kwargs) -> BaseChatModel:
        """Get an LLM by name with optional argument overrides.

        When kwargs are provided a fresh ChatOpenAI instance is returned with
        those overrides applied, leaving the shared registry entry untouched.

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
            logger.debug("creating_llm_with_custom_args", model_name=model_name, custom_args=list(kwargs.keys()))
<<<<<<< HEAD
            return ChatOpenAI(model=model_name, api_key=_API_KEY, base_url=_BASE_URL, **kwargs)
=======
            return ChatOpenAI(model=model_name, api_key=_API_KEY, **kwargs)
>>>>>>> d372769 (Coding Helper — AI Mentor & Senior Code Reviewer (FastAPI + LangGraph))

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
