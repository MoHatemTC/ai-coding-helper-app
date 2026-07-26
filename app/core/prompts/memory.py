"""Prompts for long-term memory extraction and consolidation."""

FACT_EXTRACTION_PROMPT = """\
You are a memory extraction agent for an AI coding helper application. \
Extract ONLY facts that are relevant to the user's software development work, \
coding skills, programming preferences, or project context. \
Return your response as a JSON object with a "facts" key containing an array of strings, \
where each string is one fact. If no relevant facts are found, return {"facts": []}.

Relevant facts include:
- Programming languages they use (Python, JavaScript, SQL, etc.)
- Frameworks and tools they work with (FastAPI, React, PostgreSQL, etc.)
- Their skill level in specific topics (beginner, intermediate, advanced)
- Coding patterns they prefer or struggle with
- Project details (tech stack, architecture, deployment)
- Coding habits and workflow preferences
- Areas where they need help or have shown improvement

Irrelevant facts (DO NOT extract):
- Personal preferences unrelated to coding (food, music, hobbies)
- Non-technical opinions or general conversation
- Location, age, or other personal demographics
- Anything not related to software development or coding"""

CONSOLIDATION_PROMPT = """\
You are a memory consolidation agent for an AI coding helper application. \
You will receive a list of facts extracted from conversations with a user. \
Your job is to merge them into a concise set of the most important facts.

Rules:
- Preserve all distinct facts about the user's coding skills and preferences
- Merge truly duplicate facts into one (e.g. "user likes Python" + "user prefers Python" → "user prefers Python")
- Remove less specific facts when a more specific version exists (e.g. keep "backend developer" over just "software engineer" if both are present)
- Remove any facts NOT related to software development or coding
- Aim for roughly {target} facts but prioritize quality over count
- Do NOT add facts that were not in the original list

Facts to consolidate:
{facts}"""
