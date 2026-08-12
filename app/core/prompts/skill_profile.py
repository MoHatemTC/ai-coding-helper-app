"""Prompt for the skill profile ACE pipeline — single combined generate+reflect call."""

SKILL_PROFILE_DELTA_PROMPT = """\
You maintain a structured skill profile for a user of an AI coding mentor, \
built up incrementally across many conversations. You will see the user's \
current profile and the most recent conversation. Propose the MINIMAL set \
of changes needed to keep the profile accurate.

CRITICAL — grounding rules (violating these is worse than proposing nothing):
- Every skill, tool, and detail you write must be DIRECTLY evidenced by the \
text of the conversation below. If it isn't explicitly mentioned, shown in \
code, or clearly demonstrated, it does not go in the profile.
- Never add a tool or skill just because it's commonly paired with what was \
mentioned (e.g. do not add "mypy" or "type hints" just because Python came \
up — only add them if the user's code or words actually contain them).
- If the code shown does NOT use a feature (type hints, docstrings, tests, \
error handling, etc.), that is not evidence the user knows it. Silence \
about a feature is not evidence either way — leave it out entirely.
- A single basic or clarifying question (e.g. "how do I add an item to a \
list", "is this correct syntax?") is a signal about that SPECIFIC sub-topic \
only. It does NOT justify changing a language's overall proficiency level, \
and it does NOT justify rewriting that language's existing detail. Record \
it as a new STRUGGLE entry (skill = the specific sub-topic, e.g. "list \
methods") instead — leave the LANGUAGE entry untouched.
- Distinguish CURRENT confusion from RESOLVED past difficulty. If the user \
is asking for help right now ("I don't get X", "how do I do Y"), that's a \
live STRUGGLE — log it. If the user is describing something they already \
worked through ("took a while but it works now", "finally figured out X", \
"had trouble with X earlier but got it"), that is NOT a current struggle — \
do not create or reinforce a STRUGGLE entry for it. If an existing STRUGGLE \
entry for that exact sub-topic is now resolved, add it to "remove" instead.
- Proficiency levels require real evidence: "advanced" means the \
conversation shows nontrivial, correct, independent use (architecture \
decisions, correct use of advanced features, teaching/reviewing others). \
Simply naming a language as "my main language" is INTERMEDIATE at most \
until stronger evidence appears — do not default to advanced.
- If a skill is already accurately captured and this conversation adds \
nothing new about it, do NOT include it in upsert. Re-confirming unchanged \
entries wastes a write and is treated as an error, not thoroughness.
- If the current profile already contains a skill referring to the same \
thing you're about to add, REUSE its exact existing name — copy the text \
verbatim from the "Current profile" list below, character for character, \
including capitalization. Do not rephrase, reorder words, abbreviate, or \
expand it. E.g. if the profile lists "async SQLAlchemy", write exactly \
"async SQLAlchemy" — never "SQLAlchemy async", "async-sqlalchemy", or \
"SQLAlchemy". Before writing any skill name, first scan the current \
profile for anything that could be the same thing under different words.
- If you have no grounded changes to propose, return empty lists. An empty \
delta is the correct and expected output most of the time — it is not a \
failure.

Category guide:
- language / framework / tool require a proficiency level.
- strength / struggle / pattern / project_context must NOT have one — those \
describe behavior, not skill level, and are the correct place for a \
specific knowledge gap or a specific thing the user does well.
- Reference examples — use these exact category assignments for well-known \
entities, don't re-judge them each time:
  language: Python, TypeScript, JavaScript, Go, Rust, Java, C++
  framework: FastAPI, Django, Flask, Express, React, Next.js, Spring
  tool: Docker, pytest, Redis, Git, SQLAlchemy, mypy, PostgreSQL, Kubernetes
  A web framework is never a language. A testing/database/ORM/deployment \
library is a tool, not a framework, unless it's a full application \
framework like the ones listed above.

Current profile:
{existing_profile}

Conversation:
{conversation}

---
Worked examples of correct behavior:

Example A — trivial review question, no new grounded evidence:
Conversation: "Can you review this code? def add(a,b): return a+b — is \
this how you write functions in Python?"
Correct output: upsert=[], remove=[]
(The code shows nothing beyond basic syntax the user already has a profile \
entry for. Do not invent type hints, docstrings, or tools. Asking "is this \
correct" is mildly beginner-flavored, not evidence of anything to record — \
one uncertain question is too weak to change an established proficiency.)

Example B — a specific struggle, not a language-wide correction:
Conversation: "I'm confused about how lists work in Python. How do I add \
an item to a list?"
Correct output: upsert=[SkillAssessment(skill="list methods", \
category="struggle", proficiency=None, detail="asked how to add an item \
to a list — unfamiliar with append/insert")], remove=[]
(The existing Python LANGUAGE entry is untouched — proficiency and detail \
stay exactly as they were. The gap is specific and goes in STRUGGLE.)

Example C — a resolved past difficulty, not a current struggle:
Conversation: "I refactored my queries to use async context managers — \
took me a while to get the async generator pattern right but it works now."
Correct output: upsert=[], remove=[]
(The user is describing something they already worked through and solved, \
not asking for help. "But it works now" means this is resolved — do not \
create a STRUGGLE entry for it. If you're not confident there's a clearly \
grounded, non-redundant improvement to log elsewhere, an empty delta is \
correct here too.)
"""
