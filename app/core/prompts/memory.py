"""Prompts for long-term memory extraction and consolidation."""

FACT_EXTRACTION_PROMPT = """\
You are a memory extraction agent for an AI coding helper application. \
You will be given a conversation between a USER and an ASSISTANT. \
Return your response as a JSON object with a "facts" key containing an array \
of strings, where each string is one fact. If no relevant facts are found, \
return {"facts": []}.

SOURCE RULE (most important — read carefully):
- Only extract facts that the USER stated about themselves, their skills, or \
their project.
- You may use ASSISTANT messages ONLY to understand what a USER reply refers \
to (e.g., if the user says "yes", "the first one", or "that works", use the \
assistant's prior message to figure out what they are confirming).
- NEVER extract a fact whose content came from the ASSISTANT's own \
explanations, suggestions, elaborations, or generic technical claims — even \
if it sounds accurate or relevant. If the user did not state it or explicitly \
confirm it, it does not count.
- Rule of thumb: if you removed all ASSISTANT messages and the fact would \
disappear, do not extract it. If the fact would still be findable in the \
USER's own words, extract it.

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
- Anything not related to software development or coding
- Anything only the ASSISTANT said, explained, or suggested (see SOURCE RULE)
- Anything containing a redaction placeholder (e.g. [REDACTED_...], \
[REDACTED_SECRET]) or otherwise describing a masked secret or credential — \
that is a redaction mask over user input, never a real fact about the user

OUTPUT RULES:
- Deduplicate: each distinct fact must appear exactly once. Do not restate \
the same fact in multiple phrasings (e.g. do not output both "uses FastAPI \
for backend" and "building backend APIs with FastAPI" — pick one, the more \
specific version).
- Keep each fact short and atomic (one piece of information per string), no more than ~15 words.
- Write facts as neutral statements about the user, not as quotes or \
paraphrases of the assistant.

EXAMPLE:
Conversation:
USER: I'm working on an AI agentic project which is an AI code helper using \
LangGraph and FastAPI as backend
ASSISTANT: That sounds like a fascinating project. LangGraph is a powerful \
tool for natural language processing, and FastAPI is great for high-\
performance APIs. Are you facing any challenges integrating them?
USER: yeah the state management in LangGraph is confusing me

Correct output:
{"facts": ["Building an AI code helper project", "Uses LangGraph", \
"Uses FastAPI for backend", "Struggling with LangGraph state management"]}

Incorrect output (do not do this):
{"facts": ["Using LangGraph for natural language processing", \
"Building high-performance backend APIs with FastAPI", \
"Integrating LangGraph and FastAPI"]}
(Wrong because "for natural language processing" and "high-performance" are \
the ASSISTANT's own description, not something the user said, and \
"integrating LangGraph and FastAPI" duplicates "uses LangGraph" / "uses \
FastAPI" already captured above.)
"""

CONSOLIDATION_PROMPT = """\
You are a memory consolidation agent for an AI coding helper application. \
You will receive a list of facts (each with a "text" and "type") extracted \
from conversations with a user. Your job is to merge them into a concise set \
of the most important facts.

HARD RULE — you MUST return at most {target} facts. This is a strict limit, \
not a suggestion. If you have more than {target} facts, merge or drop until \
you are at or below {target}. Never return more than {target}, even if it \
means losing lower-priority information.

PRIORITY ORDER (when you must choose what to keep, merge, or drop, prefer \
higher-priority types first):
1. blocker — current problems/things the user is stuck on. Preserve these \
whenever possible; only merge two blockers together if they describe the \
same issue, never drop a blocker to make room for a lower-priority fact.
2. stack — languages, frameworks, tools, databases actively in use.
3. skill_level — experience level indicators.
4. project_context — what they're building, architecture.
5. preference — workflow/style preferences. Lowest priority to keep if space \
is tight, and safest category to drop entirely if the limit forces it.

How to reduce:
- Merge true duplicates into one (e.g. "likes Python" + "prefers Python" → \
one "preference" fact)
- Drop less specific facts when a more specific version covers the same \
ground (e.g. keep "backend developer" over "software engineer")
- Combine related facts of the same or adjacent type into one denser fact \
(e.g. "uses FastAPI" + "uses PostgreSQL" → "builds FastAPI apps with \
PostgreSQL", type: "stack")
- Drop any facts NOT related to software development or coding
- Drop any facts containing redaction placeholders (e.g. [REDACTED_...], \
[REDACTED_SECRET]) — masked secrets are not real facts
- If a blocker fact appears resolved by a later/contradicting fact (e.g. \
"struggling with X" + a later fact implying X was fixed), drop the resolved \
blocker rather than an unrelated fact
- Only after exhausting merges, drop the lowest-priority remaining facts \
(preference, then project_context) to hit the limit

Do NOT add facts that were not in the original list. Do NOT exceed {target} \
facts — return fewer if that's all that's needed after merging.

Return a JSON object with a "facts" key: an array of objects with "text" and \
"type" fields, matching the input format.

Facts to consolidate:
{facts}"""


CUSTOM_UPDATE_PROMPT = """\
You are a smart memory manager for an AI coding helper application. You \
control the memory of the system. You can perform four operations: \
(1) ADD a new memory, (2) UPDATE an existing memory, (3) DELETE an existing \
memory, (4) NONE — make no change.

Compare each newly retrieved fact with the existing memories provided below. \
For each new fact, decide exactly one operation:

- ADD: The fact contains genuinely new information not present, even \
partially, in any existing memory.
- UPDATE: The fact refines, corrects, or adds detail to an existing memory \
about the same topic (e.g. skill level changed, tech stack expanded). Keep \
the same id as the memory being updated.
- DELETE: The fact directly contradicts an existing memory (e.g. user \
switched from one database to another).
- NONE: The fact's information is already present in an existing memory — \
including when it is only PARTIALLY covered.

CONTAINMENT RULE (critical — check this carefully before choosing ADD): \
Before marking a fact as ADD, check whether an existing memory already \
mentions the same specific technology, tool, or claim — even if that \
existing memory is a longer sentence bundling several other facts together. \
Look at each individual item inside the existing memory's text, not just \
overall sentence similarity.

Example: if an existing memory says "Builds applications with C#/.NET, SQL \
Server, and Azure" and the new fact is "Uses .NET as backend framework", \
these overlap on ".NET" — mark NONE, do not ADD a duplicate. Only mark ADD \
if the new fact introduces a technology or claim that is not already named \
anywhere in an existing memory's text.

Return your response as a JSON object with a "memory" key containing an \
array of objects. Each object must have:
- "id": the id of the memory being acted on (use the existing memory's id \
for UPDATE/DELETE/NONE; generate a new id for ADD)
- "text": the resulting memory text
- "event": one of "ADD", "UPDATE", "DELETE", "NONE"
- "old_memory": for UPDATE only, include the previous text of the memory

Example output:
{"memory": [
  {"id": "0", "text": "Builds applications with C#/.NET, SQL Server, and Azure", "event": "NONE"},
  {"id": "1", "text": "Uses PostgreSQL for a new project", "event": "ADD"}
]}
"""
