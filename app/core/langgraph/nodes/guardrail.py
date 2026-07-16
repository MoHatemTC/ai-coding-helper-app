
"""Guardrail node for detecting unsafe or disallowed user intents."""

from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.graph.state import Command

from app.core.logging import logger
from app.schemas.graph import GraphState
from app.utils import extract_text_content


def _classify_request(user_message: str) -> str:
    """Classifies the user's incoming intent into a specific category."""
    message_lower = user_message.lower()

    # Simple heuristic safety check
    if any(word in message_lower for word in ["hack", "exploit", "bypass", "delete database"]):
        return "safety_policy_violation"

    # Simple heuristic redirection check
    if any(word in message_lower for word in [
        "write the code for",
        "give me the full function",
        "complete this",
        "solve this leetcode",
        "homework solution",
        "do my homework",
    ]):
        return "writing_whole_function"

    return "code_review"


def _create_redirect_message() -> AIMessage:
    """Generates an AIMessage containing a structured redirect response."""
    content = (
        "**Understanding:**\n"
        "I understand you are looking for a complete code implementation or solution.\n\n"
        "**Review:**\n"
        "To support your growth as an engineer, I don't write complete solutions or solve tasks directly.\n\n"
        "**Explanation:**\n"
        "Deeply understanding how to structure and write this code yourself is key to mastering these concepts.\n\n"
        "**Hint:**\n"
        "Why don't we break this down together? Share the logic you have written so far, or tell me where you are stuck, and we can tackle it step-by-step.\n\n"
        "**Next Step:**\n"
        "Please paste your current draft or describe your immediate logic goal!"
    )
    return AIMessage(content=content)


def _create_blocked_message() -> AIMessage:
    """Generates an AIMessage containing a standard policy violation block."""
    content = (
        "**Understanding:**\n"
        "I detected a request that violates my core safety or security policies.\n\n"
        "**Review:**\n"
        "Request blocked.\n\n"
        "**Explanation:**\n"
        "I cannot fulfill requests that violate safety standards, encourage exploitation, or compromise system security.\n\n"
        "**Hint:**\n"
        "Please rephrase your query to focus on standard educational concepts, algorithms, or secure coding principles.\n\n"
        "**Next Step:**\n"
        "Reset your query and focus on clean, safe development practices."
    )
    return AIMessage(content=content)


async def guardrail_node(state: GraphState) -> Command:
    """Evaluate the user's request and route the graph accordingly."""
    messages = state.messages or []
    if not messages:
        return Command(goto="chat")

    latest_user_message = extract_text_content(messages[-1].content)
    category = _classify_request(latest_user_message)

    if category == "safety_policy_violation":
        blocked_msg = _create_blocked_message()
        logger.warning("guardrail_blocked_request", category=category)
        return Command(update={"messages": [blocked_msg]}, goto=END)

    if category == "writing_whole_function":
        redirect_msg = _create_redirect_message()
        logger.info("guardrail_redirected_request", category=category)
        return Command(update={"messages": [redirect_msg]}, goto=END)

    return Command(goto="chat")

