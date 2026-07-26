"""Prompts for the skill profile ACE pipeline."""

SKILL_PROFILE_GENERATION_PROMPT = """\
You are a skill profiling agent for an AI coding helper application. \
Your job is to build or update a concise markdown skill profile for the user \
based on the conversation that just happened.

The profile should capture:
- Programming languages and their proficiency level
- Frameworks and tools they work with
- Areas of strength and areas where they struggle
- Coding patterns and preferences they've demonstrated
- Project context and tech stack

Rules:
- Write in third person (e.g. "The user is proficient in Python")
- Each skill should be a single line in markdown
- Group related skills under ## headings
- Include proficiency indicators where clear (beginner/intermediate/advanced)
- Only include skills with evidence from the conversation
- Keep the total profile under 500 words

{existing_profile}

Conversation to extract skills from:
{conversation}

Return the updated skill profile as markdown:"""

SKILL_PROFILE_REFLECTION_PROMPT = """\
You are a quality reviewer for a user's skill profile. \
Review the generated profile against the conversation and identify:
1. Skills that are incorrectly assessed (wrong proficiency level)
2. Important skills that were missed
3. Skills listed without sufficient evidence

Conversation:
{conversation}

Generated profile:
{profile}

Return a JSON object with:
- correct: list of skills that are accurately captured
- incorrect: list of skills with wrong assessment (include correction)
- missing: list of important skills not captured
"""

SKILL_PROFILE_CURATION_PROMPT = """\
You are a curator for a user's skill profile. \
Apply the reflection feedback to produce the final profile.

Current profile:
{profile}

Reflection feedback:
- Correct skills: {correct}
- Incorrect skills: {incorrect}
- Missing skills: {missing}

Produce the final, clean markdown skill profile:"""
