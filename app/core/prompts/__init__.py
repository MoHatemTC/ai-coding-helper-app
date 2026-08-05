"""This file contains the prompts for the agent."""

import os
from datetime import datetime
from typing import Optional

from app.core.config import settings

_PROMPTS_DIR = os.path.dirname(__file__)

# Read templates once at module load — no file I/O per request
with open(os.path.join(_PROMPTS_DIR, "system.md"), "r") as _f:
    _SYSTEM_PROMPT_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "session_title.md"), "r") as _f:
    SESSION_TITLE_PROMPT = _f.read()

# hints.md lives in the shared app/prompts directory (two levels up from
# app/core/prompts) alongside the guardrail system prompts.
_HINTS_PROMPT_PATH = os.path.abspath(os.path.join(_PROMPTS_DIR, os.pardir, os.pardir, "prompts", "hints.md"))
with open(_HINTS_PROMPT_PATH, "r") as _f:
    _HINT_SYSTEM_PROMPT_TEMPLATE = _f.read()

CRITICAL_REVISION_NOTICE = (
    "CRITICAL REVISION NOTICE: Your previous draft was flagged for policy violation: {reason}. "
    "Strictly shorten any code blocks and maintain a hint-only perspective."
)


def load_system_prompt(username: Optional[str] = None, **kwargs) -> str:
    """Load the system prompt from the cached template.

    Args:
        username: The name of the user, if available.
        **kwargs: Additional template formatting parameters (e.g., summary, skill_profile, code_context).

    Returns:
        The rendered system prompt string.
    """
    user_context = f"# User\nYou are talking to {username}.\n" if username else ""
    kwargs.setdefault("summary", "")
    kwargs.setdefault("skill_profile", "No skill profile yet.")
    kwargs.setdefault("code_context", "")
    return _SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=settings.PROJECT_NAME + " Agent",
        current_date_and_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_context=user_context,
        **kwargs,
    )


def load_hint_system_prompt(
    username: Optional[str] = None,
    long_term_memory: Optional[str] = None,
    skill_profile: Optional[str] = None,
    summary: Optional[str] = None,
    code_context: Optional[str] = None,
    outbound_trigger_reason: Optional[str] = None,
    **kwargs,
) -> str:
    """Load the progressive hint system prompt from the cached hints.md template.

    Args:
        username: The display name of the developer being mentored.
        long_term_memory: Long-term memory context for the developer.
        skill_profile: Rendered skill profile used to calibrate hint depth.
        summary: Conversation summary so far.
        code_context: Retrieved code chunks from the uploaded files.
        outbound_trigger_reason: When set (a previous draft was rejected by the
            outbound guardrail), a critical self-correction directive is appended
            instructing the model to produce conceptual guidance only.
        **kwargs: Additional template formatting parameters passed to the prompt template.

    Returns:
        The rendered hint system prompt string.
    """
    system_prompt = _HINT_SYSTEM_PROMPT_TEMPLATE.format(
        username=username or "Developer",
        long_term_memory=long_term_memory or "No memory recorded.",
        skill_profile=skill_profile or "No skill profile recorded.",
        summary=summary or "No previous summary.",
        code_context=code_context or "No uploaded code context found.",
        agent_name=settings.PROJECT_NAME + " Hint Agent",
        current_date_and_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **kwargs,
    )
    if outbound_trigger_reason:
        system_prompt += f"\n\n{CRITICAL_REVISION_NOTICE.format(reason=outbound_trigger_reason)}"
    return system_prompt
