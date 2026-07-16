"""Standalone OpenRouter baseline test.

This script bypasses ``llm_service`` and ``LLMRegistry`` because the
registry currently supports only OpenAI model names.

Setup:
    1. Create a free OpenRouter account.
    2. Generate an API key.
    3. Set ``OPENROUTER_API_KEY``.
    4. Set ``OPENROUTER_MODEL``.

Run:
    uv run python run_baseline_with_openrouter.py

On PowerShell, use ``$env:OPENROUTER_API_KEY`` instead of ``set``.
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import SecretStr


from app.core.langgraph.nodes.performance_node import (
    SAMPLE_CODE,
    SYSTEM_PROMPT,
    PerformanceReviewResult,
    _number_lines,
)

load_dotenv()  # reads .env in the repo root and loads it into os.environ

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
# Check https://openrouter.ai/models?max_price=0 for current free models —
# this default may stop being free or may be renamed over time.
MODEL_NAME = os.environ.get("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct:free")


async def main() -> None:
    """Run the baseline performance review against an OpenRouter model."""
    llm = ChatOpenAI(
        model=MODEL_NAME,
        api_key=SecretStr(OPENROUTER_API_KEY),
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
