"""Prompt for the skill profile ACE pipeline — single combined generate+reflect call."""

SKILL_PROFILE_DELTA_PROMPT = """\
You maintain a structured skill profile for a user of an AI coding mentor, \
built up incrementally across many conversations. You will see the user's \
current profile and the most recent conversation. Propose the MINIMAL set \
of changes needed to keep the profile accurate — do not restate what's \
already correct.

Guidelines:
- Upsert a skill only if: (a) it's new with clear evidence in this \
conversation, or (b) an existing entry is now wrong or outdated — give the \
corrected version.
- Leave alone any skill that's already accurately captured. Silence on a \
topic is not evidence it changed.
- Only include entries in "remove" when the conversation directly \
contradicts them (e.g. the user says they've never used X, or a prior entry \
was clearly a misread). Not mentioning something again is NOT grounds for \
removal.
- "language" / "framework" / "tool" categories require a proficiency level. \
"strength" / "struggle" / "pattern" / "project_context" must NOT have one — \
those describe behavior, not skill level.
- Base everything strictly on evidence in the conversation below. Do not \
infer skills from tone, guess proficiency, or pad the profile.

Current profile:
{existing_profile}

Conversation:
{conversation}
"""
