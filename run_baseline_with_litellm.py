"""Standalone baseline test using LiteLLM/Kimi credentials directly.

The test uses llm_service through registry.py and bypasses pytest.

This is the quickest way to confirm the FW-Kimi-K2.6 registry entry
works end-to-end before running the full test suite.

Setup (in your .env, repo root):
    OPENAI_API_KEY=sk-...
    OPENAI_BASE_URL=https://learner-os.sprints.ai/litellm
    DEFAULT_LLM_MODEL=FW-Kimi-K2.6

Run with:
    uv run python run_baseline_with_litellm.py
"""

from __future__ import annotations

import asyncio

from app.core.langgraph.nodes.performance_node import (
    SAMPLE_CODE,
    SYSTEM_PROMPT,
    PerformanceReviewDraft,
    _number_lines,
)
from app.services.llm.registry import LLMRegistry


async def main() -> None:
    """Run a baseline performance review call directly against FW-Kimi-K2.6 and print the result."""
    messages = [
        ("system", SYSTEM_PROMPT),
        ("user", f"Numbered code:\n{_number_lines(SAMPLE_CODE)}"),
    ]

    llm = LLMRegistry.get("FW-Kimi-K2.6").with_structured_output(PerformanceReviewDraft)
    result: PerformanceReviewDraft = await llm.ainvoke(messages)

    if not result.findings:
        print("No findings returned — check the prompt or sample code.")
    for f in result.findings:
        print(f.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
