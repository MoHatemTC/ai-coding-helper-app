"""Live smoke coverage for progressive hint generation.

Run explicitly with ``pytest -s tests/integration/integration_test.py`` so the
structured telemetry remains visible in the console.
"""

import os
from importlib import import_module
from typing import Any

import pytest
import structlog

from app.prompts.hints import HINT_SYSTEM_PROMPT
from app.schemas.review import (
    Category,
    Finding,
    HintLevel,
    HintState,
    MentorResponse,
    Severity,
)
from dotenv import load_dotenv
load_dotenv()


logger: Any = structlog.get_logger(__name__)

PROMPT_REQUIRED_TEXT_FIELDS = ("understanding", "review", "explanation", "hint", "next_step")

requires_live_proxy = pytest.mark.skipif(
    not os.getenv("LITELLM_API_KEY") or not os.getenv("LITELLM_BASE_URL"),
    reason="LITELLM_API_KEY and LITELLM_BASE_URL must be configured for the live proxy smoke test",
)


def _assert_populated_mentor_response(raw_hint: dict[str, Any]) -> MentorResponse:
    """Validate the shared response contract and require usable text in every field."""
    response: MentorResponse = MentorResponse.model_validate(raw_hint)

    assert set(raw_hint) == set(MentorResponse.model_fields), "live response keys drifted from MentorResponse"
    for field_name in PROMPT_REQUIRED_TEXT_FIELDS:
        value = raw_hint[field_name]
        assert isinstance(value, str) and value.strip(), f"{field_name} must contain non-empty response text"

    for field_name, value in raw_hint.items():
        if field_name not in PROMPT_REQUIRED_TEXT_FIELDS and value:
            assert isinstance(value, str) and value.strip(), f"{field_name} must contain non-empty response text"

    return response


@requires_live_proxy
@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.asyncio
async def test_generate_hint_node_live_problem_switch_smoke() -> None:
    """Call the live proxy twice and verify schema and problem-switch state behavior."""
    generate_hint_node: Any = import_module("app.core.langgraph.nodes.hints").generate_hint_node
    finding: Finding = Finding(
        line=2,
        severity=Severity.HIGH,
        category=Category.CORRECTNESS,
        message="The conversion raises on non-numeric input.",
        rationale="Calling int() without validating user input can raise ValueError.",
    )
    initial_problem_id = "live-hint-smoke-initial-problem"
    switched_problem_id = "live-hint-smoke-switched-problem"
    initial_state: dict[str, Any] = {
        "problem_id": initial_problem_id,
        "hint_state": HintState(
            current_problem_id=initial_problem_id,
            current_level=HintLevel.NUDGE,
            history=[],
        ).model_dump(),
        "code": "def parse_age(raw_age: str) -> int:\n    return int(raw_age)\n",
        "findings": [finding.model_dump()],
        "user_query": "Help me make this parser safe without giving me the full solution.",
    }

    logger.info(
        "live_hint_smoke_before_execution",
        proxy_base_url=os.environ["LITELLM_BASE_URL"],
        current_hint_tier=HintLevel.NUDGE.value,
        payload_size=len(str(initial_state)),
        problem_id=initial_problem_id,
        prompt_size=len(HINT_SYSTEM_PROMPT),
    )
    first_result: dict[str, Any] = await generate_hint_node(initial_state)
    first_hint: MentorResponse = _assert_populated_mentor_response(first_result["latest_hint"])
    first_hint_state: HintState = HintState.model_validate(first_result["hint_state"])
    logger.info(
        "live_hint_smoke_after_initial_execution",
        payload_size=len(str(initial_state)),
        delivered_hint_length=len(first_hint.hint or ""),
        updated_current_problem_id=first_hint_state.current_problem_id,
        updated_hint_tier=first_hint_state.current_level.value,
        updated_history=[level.value for level in first_hint_state.history],
    )

    assert first_hint_state.current_problem_id == initial_problem_id
    assert first_hint_state.current_level is HintLevel.DIRECTION
    assert first_hint_state.history == [HintLevel.NUDGE]
    assert "system disruption" not in first_hint.understanding.lower(), (
        "live proxy call fell back instead of succeeding"
    )

    switched_state: dict[str, Any] = {
        **initial_state,
        "problem_id": switched_problem_id,
        "hint_state": first_hint_state.model_dump(),
    }
    logger.info(
        "live_hint_smoke_before_problem_switch",
        payload_size=len(str(switched_state)),
        current_hint_tier=first_hint_state.current_level.value,
        prior_history=[level.value for level in first_hint_state.history],
        problem_id=switched_problem_id,
    )
    switched_result: dict[str, Any] = await generate_hint_node(switched_state)
    switched_hint: MentorResponse = _assert_populated_mentor_response(switched_result["latest_hint"])
    switched_hint_state: HintState = HintState.model_validate(switched_result["hint_state"])
    logger.info(
        "live_hint_smoke_after_problem_switch",
        delivered_hint_length=len(switched_hint.hint or ""),
        updated_current_problem_id=switched_hint_state.current_problem_id,
        updated_hint_tier=switched_hint_state.current_level.value,
        updated_history=[level.value for level in switched_hint_state.history],
    )

    assert switched_hint_state.current_problem_id == switched_problem_id
    # The new problem starts at NUDGE; that delivered level is then recorded and
    # escalated to DIRECTION, proving prior progression was discarded.
    assert switched_hint_state.current_level is HintLevel.DIRECTION
    assert switched_hint_state.history == [HintLevel.NUDGE]
    assert "system disruption" not in switched_hint.understanding.lower(), (
        "live proxy call fell back instead of succeeding"
    )
