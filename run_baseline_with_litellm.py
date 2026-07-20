# run_baseline_with_litellm.py  (repo root)
import asyncio
from app.core.config import settings
from app.services.llm import llm_service
from app.core.langgraph.nodes.performance_node import (
    SAMPLE_CODE, SYSTEM_PROMPT, PerformanceReviewDraft, _number_lines,
)

async def main():
    messages = [
        ("system", SYSTEM_PROMPT),
        ("user", f"Numbered code:\n{_number_lines(SAMPLE_CODE)}"),
    ]
    result = await llm_service.call(
        messages,
        model_name="nemotron-3-ultra-550b-a55b:free",
        base_url=settings.OPENAI_BASE_URL,
        response_format=PerformanceReviewDraft,
    )
    for f in result.findings:
        print(f.model_dump_json(indent=2))

if __name__ == "__main__":
    asyncio.run(main())