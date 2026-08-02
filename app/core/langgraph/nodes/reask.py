"""Re-ask node that corrects the agent when a full solution leak is detected."""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.utils import extract_text_content


async def reask_node(state: dict[str, Any], chat_model: Any) -> dict[str, Any]:
    """Generate a mentor-style replacement for an unsafe draft."""
    original_query = next(
        (message.content for message in reversed(state["messages"]) if isinstance(message, HumanMessage)),
        "",
    )
    reask_prompt = (
        "Your previous response gave away the full solution, which violates mentorship rules.\n\n"
        f"The student asked: {original_query}\n\n"
        "Rewrite your response strictly following these rules:\n"
        "- Give a nudge or direction hint only\n"
        "- Do NOT provide working code that solves the problem\n"
        "- Ask one guiding question to help the student think through it\n\n"
        "Respond now with the corrected mentor response."
    )
    response = await chat_model.ainvoke([*state["messages"], HumanMessage(content=reask_prompt)])
    corrected = extract_text_content(response.content)
    return {
        "final_response": corrected,
        "is_safe_output": True,
        "messages": [AIMessage(content=corrected)],
    }
