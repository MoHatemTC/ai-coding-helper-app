"""Standalone baseline test using OpenRouter (free tier), bypassing
llm_service/LLMRegistry entirely — same reasoning as before: registry.py
only knows OpenAI model names, so reaching an OpenRouter model requires
either this standalone script or a shared-infra change to registry.py
(worth raising with whoever owns that file, not doing solo).
 
Setup:
  1. Create a free account at https://openrouter.ai and generate an API
     key at https://openrouter.ai/keys
  2. Browse https://openrouter.ai/models?max_price=0 for current free
     models (the exact list changes over time — pick one from there and
     set OPENROUTER_MODEL below to match).

Run with:
    set OPENROUTER_API_KEY=sk-or-v1-...
    set OPENROUTER_MODEL=mistralai/mistral-7b-instruct:free
    uv run python run_baseline_with_openrouter.py

(On PowerShell use $env:OPENROUTER_API_KEY="..." instead of `set`.)

NOTE: this file lives in tests/ with a test_ prefix, which means pytest
will try to collect it as a real test module even though it's a manual,
standalone script. Recommend moving/renaming it to
run_baseline_with_openrouter.py at the repo root instead — see the earlier
discussion on this exact point.
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

load_dotenv()  # reads .env in the repo root and loads it into os.environ
 
from app.core.langgraph.nodes.performance_node import (
    SAMPLE_CODE,
    SYSTEM_PROMPT,
    PerformanceReviewResult,
    _number_lines,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _load_config() -> tuple[str, str]:
    """Read OPENROUTER_API_KEY/OPENROUTER_MODEL only when actually called.

    This is intentionally NOT read at module level — reading it there would
    crash the moment this file is imported (e.g. by pytest collection),
    even in environments with no OpenRouter key configured, like CI.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set.\n"
            "Add it to your .env file in the repo root:\n"
            '    OPENROUTER_API_KEY="sk-or-v1-yourfullkeyhere"\n'
            "Get a free key at https://openrouter.ai/keys."
        )
    # Check https://openrouter.ai/models?max_price=0 for current free models —
    # this default may stop being free or may be renamed over time.
    model = os.environ.get("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct:free")
    return api_key, model


async def main() -> None:
    """Run a baseline performance review call against OpenRouter and print the result."""
    api_key, model_name = _load_config()
    llm = ChatOpenAI(
        model=model_name,
        api_key=SecretStr(api_key),
        base_url=OPENROUTER_BASE_URL,
    )
    structured_llm = llm.with_structured_output(PerformanceReviewResult)

    messages = [
        ("system", SYSTEM_PROMPT),
        ("user", f"Numbered code:\n{_number_lines(SAMPLE_CODE)}"),
    ]

    result: PerformanceReviewResult = await structured_llm.ainvoke(messages)

    if not result.findings:
        print("No findings returned — check the prompt, sample code, or try a different free model.")
    for f in result.findings:
        print(f.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
 
