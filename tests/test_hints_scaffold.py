"""Tests for progressive hint-node state handling and structured responses."""

from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes.hints import generate_hint_node
from app.schemas.hint import HintLevel, HintState, MentorResponse


@pytest.fixture
def mock_mentor_response() -> MentorResponse:
    """Return a valid mentor response for successful LLM invocations."""
    return MentorResponse(
        understanding="I see you are attempting to convert an age string to an integer.",
        review="Casting raw string input directly to an integer can raise exceptions.",
        explanation="If the input string contains non-numeric characters, Python will raise a ValueError.",
        hint="Look at the string format validation or try-except handling.",
        next_step="Wrap the int conversion inside a try block.",
    )


@pytest.mark.asyncio
async def test_hint_node_escalates_nudge_to_direction(mock_mentor_response: MentorResponse) -> None:
    """Escalate the first hint from a nudge to a direction."""
    state: Dict[str, Any] = {
        "hint_state": HintState(current_level=HintLevel.NUDGE, history=[]).model_dump(),
    }

    with patch(
        "app.graph.nodes.hints.invoke_structured_llm_with_retry",
        new=AsyncMock(return_value=mock_mentor_response),
    ):
        result: Dict[str, Any] = await generate_hint_node(state)

    assert result["hint_state"]["current_level"] == HintLevel.DIRECTION.value
    assert result["hint_state"]["history"] == [HintLevel.NUDGE.value]
    assert result["latest_hint"] == mock_mentor_response.model_dump()


@pytest.mark.asyncio
async def test_hint_node_escalates_direction_to_concrete_step(mock_mentor_response: MentorResponse) -> None:
    """Escalate a direction hint to a concrete step."""
    state: Dict[str, Any] = {
        "hint_state": HintState(
            current_level=HintLevel.DIRECTION,
            history=[HintLevel.NUDGE],
        ).model_dump(),
    }

    with patch(
        "app.graph.nodes.hints.invoke_structured_llm_with_retry",
        new=AsyncMock(return_value=mock_mentor_response),
    ):
        result: Dict[str, Any] = await generate_hint_node(state)

    assert result["hint_state"]["current_level"] == HintLevel.CONCRETE_STEP.value
    assert result["hint_state"]["history"] == [HintLevel.NUDGE.value, HintLevel.DIRECTION.value]


@pytest.mark.asyncio
async def test_hint_node_caps_at_concrete_step(mock_mentor_response: MentorResponse) -> None:
    """Keep concrete-step hints at the maximum escalation level."""
    state: Dict[str, Any] = {
        "hint_state": HintState(
            current_level=HintLevel.CONCRETE_STEP,
            history=[HintLevel.NUDGE, HintLevel.DIRECTION],
        ).model_dump(),
    }

    with patch(
        "app.graph.nodes.hints.invoke_structured_llm_with_retry",
        new=AsyncMock(return_value=mock_mentor_response),
    ):
        result: Dict[str, Any] = await generate_hint_node(state)

    assert result["hint_state"]["current_level"] == HintLevel.CONCRETE_STEP.value
    assert result["hint_state"]["history"] == [
        HintLevel.NUDGE.value,
        HintLevel.DIRECTION.value,
        HintLevel.CONCRETE_STEP.value,
    ]
    assert len(result["hint_state"]["history"]) == 3


@pytest.mark.asyncio
async def test_hint_node_fallback_on_failure() -> None:
    """Return a fallback response and advance state when the LLM fails."""
    state: Dict[str, Any] = {
        "hint_state": HintState(current_level=HintLevel.NUDGE, history=[]).model_dump(),
    }

    with patch(
        "app.graph.nodes.hints.invoke_structured_llm_with_retry",
        new=AsyncMock(side_effect=Exception("LLM timed out")),
    ):
        result: Dict[str, Any] = await generate_hint_node(state)

    assert "system disruption" in result["latest_hint"]["understanding"].lower()
    assert result["hint_state"]["current_level"] == HintLevel.DIRECTION.value
    assert result["hint_state"]["history"] == [HintLevel.NUDGE.value]


@pytest.mark.asyncio
async def test_hint_node_parses_valid_findings(mock_mentor_response: MentorResponse) -> None:
    """Validate findings before including them in the mentor payload."""
    finding_message: str = "Convert the input only after validating its numeric format."
    state: Dict[str, Any] = {
        "findings": [
            {
                "line": 12,
                "severity": "high",
                "category": "correctness",
                "message": finding_message,
                "rationale": "Non-numeric input causes int() to raise ValueError.",
            }
        ]
    }
    llm_mock: AsyncMock = AsyncMock(return_value=mock_mentor_response)

    with patch("app.graph.nodes.hints.invoke_structured_llm_with_retry", new=llm_mock):
        await generate_hint_node(state)

    await_args = llm_mock.await_args
    assert await_args is not None
    user_payload: str = await_args.args[1]
    assert finding_message in user_payload


@pytest.mark.asyncio
async def test_hint_node_skips_invalid_findings(mock_mentor_response: MentorResponse) -> None:
    """Skip malformed findings while preserving valid findings in the mentor payload."""
    invalid_message: str = "This finding has an invalid line number."
    valid_message: str = "Validate the age before converting it to an integer."
    state: Dict[str, Any] = {
        "findings": [
            {
                "line": "not-a-line-number",
                "severity": "high",
                "category": "correctness",
                "message": invalid_message,
                "rationale": "The line number must be an integer.",
            },
            {
                "line": 18,
                "severity": "medium",
                "category": "correctness",
                "message": valid_message,
                "rationale": "Invalid input can cause conversion to fail.",
            },
        ]
    }
    llm_mock: AsyncMock = AsyncMock(return_value=mock_mentor_response)

    with patch("app.graph.nodes.hints.invoke_structured_llm_with_retry", new=llm_mock):
        await generate_hint_node(state)

    await_args = llm_mock.await_args
    assert await_args is not None
    user_payload: str = await_args.args[1]
    assert invalid_message not in user_payload
    assert valid_message in user_payload


@pytest.mark.asyncio
async def test_hint_node_resets_on_problem_switch(mock_mentor_response: MentorResponse) -> None:
    """Reset hint progression when the incoming problem changes."""
    state: Dict[str, Any] = {
        "problem_id": "problem_abc",
        "hint_state": HintState(
            current_level=HintLevel.CONCRETE_STEP,
            history=[HintLevel.NUDGE, HintLevel.DIRECTION],
            current_problem_id="problem_old_xyz",
        ).model_dump(),
    }

    with patch(
        "app.graph.nodes.hints.invoke_structured_llm_with_retry",
        new=AsyncMock(return_value=mock_mentor_response),
    ):
        result: Dict[str, Any] = await generate_hint_node(state)

    assert result["hint_state"]["current_level"] == HintLevel.DIRECTION.value
    assert result["hint_state"]["history"] == [HintLevel.NUDGE.value]
    assert result["hint_state"]["current_problem_id"] == "problem_abc"
