"""Bridge node that extracts the agent's last message into ``draft_response``."""

from typing import Any

from app.utils import extract_text_content


async def collect_draft_node(state: dict[str, Any]) -> dict[str, Any]:
    """Save the full agent response for outbound evaluation."""
    last_message = state["messages"][-1]
    draft = extract_text_content(last_message.content) if last_message.content else ""
    return {"draft_response": draft}
