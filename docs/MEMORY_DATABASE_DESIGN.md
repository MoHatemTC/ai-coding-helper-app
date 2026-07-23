# Memory & Database Architecture Redesign

> **Status:** Design Document (Approved)
> **Date:** 2026-07-20
> **Scope:** Chat history, memory layers, skill profiling, API changes

---

## Table of Contents

1. [Current System Problems](#1-current-system-problems)
2. [Design Principles](#2-design-principles)
3. [New System Architecture](#3-new-system-architecture)
4. [API Changes](#4-api-changes)
5. [Database Schema](#5-database-schema)
6. [Three-Layer Memory System](#6-three-layer-memory-system)
7. [Skill Profile System](#7-skill-profile-system)
8. [Data Flow](#8-data-flow)
9. [What Changes](#9-what-changes-and-what-stays)

---

## 1. Current System Problems

### Problem 1: Chat History Coupled to LangGraph Checkpoints

**Current state:** There is no application-level messages table. All message persistence is handled by LangGraph's `AsyncPostgresSaver` checkpointer, which writes to three internal tables: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`.

**Why it's a problem:**
- Reading chat history (`GET /messages`) requires deserializing LangGraph's internal state format via `graph.aget_state()`, then filtering and processing the result
- Clearing chat history (`DELETE /messages`) requires deleting from three internal LangGraph tables — a fragile operation that depends on LangGraph's internal schema
- Chat history and workflow state are inseparable. You cannot query messages independently of the graph's execution context
- The API returns **all messages** from the graph state — no pagination, no filtering, which becomes slow for long conversations

### Problem 2: Skill Profile Generation is Biased (Negative Feedback Loop)

**Current state:** The skill profile system (`app/utils/skill_profile_generation.py`) receives only `list[Finding]` as input — bugs, security issues, performance problems. Clean code submissions produce no findings, so they are never recorded as positive signals.

**Why it's a problem:**
- The more a user interacts with the system, the worse their profile gets
- A user who submits 10 clean code reviews and 1 with a bug will have a profile dominated by the single bug
- There is no mechanism to record strengths — only weaknesses
- The profile LLM is called after every review, which is expensive for what should be a simple aggregation

### Problem 3: Findings Stored in Wrong Place

**Current state:** `store_finding()` in `app/services/memory.py` stores individual code review findings as mem0 memories — semantic text entries in pgvector. `get_all_session_finding()` retrieves them later for skill profile generation.

**Why it's a problem:**
- Findings are structured data (line number, severity, category, rationale) stored as unstructured text in a vector database
- Retrieving all findings for a session requires a mem0 search query, not a simple SQL query
- Findings cannot be aggregated with SQL (e.g., "count findings by category for this user")
- The `store_finding()` and `get_all_session_finding()` methods are **dead code** — never called in the production graph

### Problem 4: GraphState Carries Domain Data That Doesn't Belong

**Current state:** The `GraphState` schema (`app/schemas/graph.py`) contains:

```python
class GraphState(BaseModel):
    messages: Annotated[list, add_messages]
    long_term_memory: str = ""
    skill_profile: str = "No Skill Profile for this user"
    findings: Annotated[list[Finding], operator.add]  # Domain-specific
    code: str | None = None                            # Domain-specific
    language: str | None = None                        # Domain-specific
```

**Why it's a problem:**
- `code` and `language` are already present in the user's message content — duplicating them in state is redundant
- `findings` is a domain-specific accumulator that clutters the state schema
- These fields are serialized and checkpointed by LangGraph on every state transition — wasteful
- The state schema should focus on what the graph needs to function, not domain-specific data

### Problem 5: No Conversation Summarization

**Current state:** `prepare_messages()` (`app/utils/graph.py`) trims messages using LangChain's `trim_messages()` with a token limit. Messages beyond the limit are **silently dropped**.

**Why it's a problem:**
- When a conversation exceeds the token budget, older context is lost entirely
- The user might reference something from earlier in the conversation, but the LLM has no access to it
- There is no mechanism to preserve important context from older messages

---

## 2. Design Principles

1. **Unbiased** — Skill profile must be driven ONLY by code-review evidence, never by chat content
2. **Evidence-based** — Every skill assessment must have a minimum number of exposures before classification
3. **Simple** — Prefer SQL over LLM calls. Aggregation should be pure SQL, no inference needed
4. **Auditable** — Every finding, every skill change must be traceable to a specific submission
5. **Cheap to read** — Skill profile retrieval is a PK lookup, not a vector search
6. **Clean separation** — Chat history, workflow state, semantic memory, and structured data each have their own storage

---

## 3. New System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      GRAPH STATE                          │
│                                                          │
│  messages: [raw messages, last N tokens]                  │
│  summary: "Earlier we discussed X, decided Y..."         │
│  long_term_memory: "User prefers Python, works at..."    │
│  skill_profile: "Intermediate, weak at concurrency..."   │
│                                                          │
│  That's it. Four fields.                                  │
└──────────────────────────────────────────────────────────┘

         ↓ Injected into system prompt ↓

┌──────────────────────────────────────────────────────────┐
│                      SYSTEM PROMPT                        │
│                                                          │
│  {system instructions}                                   │
│  {long_term_memory}    ← from mem0                       │
│  {skill_profile}       ← from Postgres skill tables      │
│  {summary}             ← from GraphState                 │
│  {recent messages}     ← from GraphState.messages        │
│                                                          │
└──────────────────────────────────────────────────────────┘

         ↓ Stored after response ↓

┌──────────────────────────────────────────────────────────┐
│                      STORAGE                              │
│                                                          │
│  messages table (NEW):                                    │
│    Chat history the user sees. Read model for /messages.  │
│    Stores Human + AI messages only. Batch-stored after    │
│    response with full interaction context.                │
│                                                          │
│  mem0 (pgvector):                                         │
│    Cross-session facts. Retrieved semantically each turn. │
│    Processes batched content for richer extraction.       │
│                                                          │
│  LangGraph checkpoints:                                   │
│    Internal workflow state. Not read by API endpoints.    │
│    Auto-saved by graph execution.                         │
│                                                          │
│  skill tables (NEW):                                      │
│    topic_review_event, skill_topic, skill_profile.        │
│    Structured competency data from code review evidence.  │
│    PK lookups, not vector searches.                       │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### Four Storage Systems, Each With a Clear Purpose

| System                    | Purpose                   | What It Stores                                                                              | How It's Read                             |
| ------------------------- | ------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------- |
| **LangGraph checkpoints** | Workflow state            | GraphState objects (messages, summary, memory, skill_profile) — internal, not for API reads | `graph.aget_state()` — internal only      |
| **messages table**        | Chat history (read model) | Human/AI messages the user sees — the source of truth for `/messages`                       | SQL query with pagination                 |
| **mem0 (pgvector)**       | Cross-session memory      | Extracted facts, preferences — retrieved semantically each turn                             | `memory_service.search()` — vector search |
| **skill tables**          | Competency model          | topic_review_event, skill_topic, skill_profile — from code review evidence                  | SQL PK lookup — instant                   |

---

## 4. API Changes

### POST /chat — Return Only the New Message

**Current:** Returns `ChatResponse` containing all messages from the graph state (the entire conversation).

**New:** Returns `ChatResponse` containing only the **new** messages generated by this request — the user's input and the AI's response (plus any tool messages if applicable).

**Why:** The client already has previous messages from `/messages`. Returning the full history on every chat call is wasteful. The client appends the new message to its existing conversation view.

**Implementation:** After `agent.get_response()` returns the full graph state, extract only the messages that were added in this turn (compare message count before and after, or tag new messages).

### GET /messages — Paginated Chat History

**Current:** Returns `ChatResponse` containing all user/assistant messages for the entire session. No pagination.

**New:** Returns `ChatResponse` with paginated results. Uses cursor-based pagination with `id` as the cursor.

**Parameters:**
- `limit` (int, default 50, max 100) — messages per page
- `after` (str, optional) — message ID cursor for next page
- `before` (str, optional) — message ID cursor for previous page

**Why:** Long conversations produce slow, large responses. Pagination is standard for chat applications and scales well.

### DELETE /messages — Clear History

**Current:** Deletes from three LangGraph checkpoint tables.

**New:** Deletes from the `messages` table + LangGraph checkpoint tables. Optionally clears mem0 memories (to be decided later).

---

## 5. Database Schema

### New Table: `messages`

```sql
CREATE TABLE messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     INT NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    session_id  VARCHAR NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    role        VARCHAR NOT NULL CHECK (role IN ('Human', 'AI')),
    message     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_session_id ON messages(session_id, created_at);
CREATE INDEX idx_messages_user_id ON messages(user_id);
```

**Design decisions:**
- `role` is `Human` or `AI` only — no Tool messages in the chat history (tool results are summarized naturally in the AI message)
- `message` is plain text — the AI message may include summarized tool output (e.g., "Based on my security review, I found 2 issues...")
- `id` is UUID for cursor-based pagination and individual message operations
- Composite index on `(session_id, created_at)` for fast paginated queries
- `ON DELETE CASCADE` ensures messages are deleted when user or session is deleted

### Skill Profile Tables (from approved design)

### `topic_review_event` (raw, append-only — source of truth)

| Column              | Type               | Notes                                                                                         |
| ------------------- | ------------------ | --------------------------------------------------------------------------------------------- |
| `id`                | bigint, PK         |                                                                                               |
| `user_id`           | int, FK -> user    |                                                                                               |
| `session_id`        | str, FK -> session |                                                                                               |
| `submission_id`     | uuid               | groups rows from one review call                                                              |
| `topic`             | str                | validated against a fixed topic list                                                          |
| `topic_weight`      | float              | static difficulty weight, looked up at insert time (e.g. Basic=1, Intermediate=2, Advanced=3) |
| `findings`          | JSONB              | raw findings list from the LLM output; `[]` if clean                                          |
| `weighted_severity` | float              | `0` if clean, else sum of severity weights across findings                                    |
| `created_at`        | datetime           |                                                                                               |

Index: `(user_id, topic, created_at)`.

**Every topic in the LLM's output produces a row — clean or not.** This is what makes `count(*)` a valid exposure denominator instead of just a finding count.

### `skill_topic` (derived — read path, one row per user × topic)

| Column                  | Type     | Notes                                                       |
| ----------------------- | -------- | ----------------------------------------------------------- |
| `user_id`               | int      | composite PK                                                |
| `topic`                 | str      | composite PK                                                |
| `exposures`             | int      | `count(*)` of events, current window                        |
| `weighted_severity_sum` | float    | `sum(weighted_severity)`, current window                    |
| `weakness_rate`         | float    | `weighted_severity_sum / exposures`                         |
| `level`                 | enum     | `insufficient_data` \| Beginner \| Intermediate \| Advanced |
| `trend`                 | enum     | improving \| stable \| declining                            |
| `last_seen_at`          | datetime |                                                             |
| `updated_at`            | datetime |                                                             |

### `skill_profile` (derived — read path, one row per user)

| Column          | Type     | Notes                                |
| --------------- | -------- | ------------------------------------ |
| `user_id`       | int, PK  |                                      |
| `overall_level` | enum     | weighted rollup across `skill_topic` |
| `updated_at`    | datetime |                                      |

Can be implemented as a materialized/cached aggregate rather than a physically written table, since it's fully derivable from `skill_topic`.


### Complete Database Schema (All Tables)

```
┌─────────────────────────────────────────────────────────┐
│                   APPLICATION TABLES                      │
│                                                         │
│  user                                                   │
│  ├─ id: int PK                                          │
│  ├─ email: str UNIQUE                                   │
│  ├─ hashed_password: str                                │
│  ├─ username: str                                       │
│  └─ created_at: datetime                                │
│                                                         │
│  session                                                │
│  ├─ id: str PK (UUID)                                   │
│  ├─ user_id: int FK → user.id                           │
│  ├─ name: str                                           │
│  ├─ username: str                                       │
│  └─ created_at: datetime                                │
│                                                         │
│  messages (NEW)                                         │
│  ├─ id: str PK (UUID)                                   │
│  ├─ user_id: int FK → user.id                           │
│  ├─ session_id: str FK → session.id                     │
│  ├─ role: str (Human | AI)                              │
│  ├─ message: text                                       │
│  └─ created_at: datetime                                │
│                                                         │
│  topic_review_event (NEW)                               │
│  ├─ id: int PK                                          │
│  ├─ user_id: int FK → user.id                           │
│  ├─ session_id: str FK → session.id                     │
│  ├─ submission_id: str (UUID, groups one review)        │
│  ├─ topic: str                                          │
│  ├─ topic_weight: float                                 │
│  ├─ findings: JSONB                                     │
│  ├─ weighted_severity: float                            │
│  └─ created_at: datetime                                │
│                                                         │
│  skill_topic (NEW)                                      │
│  ├─ user_id: int (composite PK)                         │
│  ├─ topic: str (composite PK)                           │
│  ├─ exposures: int                                      │
│  ├─ weighted_severity_sum: float                        │
│  ├─ weakness_rate: float                                │
│  ├─ level: str                                          │
│  ├─ trend: str                                          │
│  ├─ last_seen_at: datetime                              │
│  └─ updated_at: datetime                                │
│                                                         │
│  skill_profile (NEW)                                    │
│  ├─ user_id: int PK                                     │
│  ├─ overall_level: str                                  │
│  └─ updated_at: datetime                                │
│                                                         │
│  thread (legacy, unused)                                │
│  ├─ id: str PK                                          │
│  └─ created_at: datetime                                │
│                                                         │
├─────────────────────────────────────────────────────────┤
│              EXTERNAL TABLES (not managed)               │
│                                                         │
│  checkpoint_blobs, checkpoint_writes, checkpoints       │
│  (LangGraph checkpointing — internal workflow state)    │
│                                                         │
│  longterm_memory, mem0migrations                        │
│  (mem0ai — cross-session semantic memory)               │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 6. Three-Layer Memory System

### Layer 1: Recent Window (Raw Messages)

**What:** The most recent messages in the conversation, stored as-is in `GraphState.messages`.

**How it works:**
- LangGraph's `add_messages` reducer maintains the message list
- `summarization_node` checks the token count against a budget (e.g., `MAX_TOKENS = 2000`)
- If within budget: messages are passed directly to the LLM
- If exceeded: oldest messages are summarized (see Layer 2) and removed from the window

**Storage:** LangGraph checkpoint tables (auto-saved by graph execution)

### Layer 2: Session Summary (Compressed Context)

**What:** A compressed narrative of older conversation context, stored in `GraphState.summary`.

**How it works:**
- When the recent window exceeds the token budget, the `summarization_node` triggers
- The oldest messages (those about to be trimmed) are sent to a **cheap model** with a summarization prompt
- The summary is stored in `GraphState.summary`
- The summarized messages are removed from the recent window

**Trigger:** Token count exceeds `MAX_TOKENS` (configurable)

**Model choice:** Cheap/fast model. This is a compression task, not a reasoning task. Cost should be minimal.

**Storage:** `GraphState.summary` field, persisted in LangGraph checkpoints

### Layer 3: Long-Term Memory (Cross-Session Facts)

**What:** Extracted facts, preferences, and context that persist across sessions, stored in mem0 (pgvector).

**How it works:**
- After the AI responds, the batched content (user message + AI response) is sent to `memory_service.add()`
- mem0's extraction LLM identifies facts, preferences, and important context
- These are stored as vector embeddings in the `longterm_memory` table
- Each turn, `memory_service.search()` retrieves relevant memories for the current query
- Results are injected into the system prompt as `{long_term_memory}`

**Storage:** mem0 pgvector `longterm_memory` table

**Retrieval:** Semantic vector search, cached for 60 seconds

### System Prompt Injection

The system prompt (`app/core/prompts/system.md`) is updated to include all memory layers:

```markdown
# Name: {agent_name}
# Role: A world class assistant
Help the user with their questions.

# Instructions
- Always be friendly and professional.
- If you don't know the answer, say you don't know. Don't make up an answer.
- Try to give the most accurate answer possible.

{user_context}

# What you know about the user
{long_term_memory}

# User Skill Profile
{skill_profile}

# Conversation Summary
{summary}

{code_context}
```

The `{summary}` section is new — it provides context from earlier in the conversation that is no longer in the recent window.

---

## 7. Skill Profile System

### Topic Registry

A fixed Python dictionary mapping review topics to difficulty weights:

| Topic             | Difficulty   | Weight |
| ----------------- | ------------ | ------ |
| `security`        | Advanced     | 3.0    |
| `design_patterns` | Intermediate | 2.0    |
| `formatting`      | Basic        | 1.0    |

### How It Works

1. **Code submission** → User submits code via the chat
2. **Review pipeline** → Review nodes (correctness, security, performance) analyze the code
3. **Topic extraction** → Each review result is mapped to a topic from the registry
4. **Finding storage** → Findings are stored in `topic_review_event` with `submission_id` grouping
5. **Aggregation** → `skill_aggregation.py` runs pure SQL queries:
   - `weakness_rate = weighted_severity_sum / exposures`
   - Level classification with `insufficient_data` floor (MIN_EXPOSURES = 5)
   - Difficulty-aware rollup prevents Advanced classification without Advanced-tier exposure
6. **Profile update** → `skill_profile` table is updated with the new `overall_level`

### Key Rules

- **Minimum exposures:** A topic needs at least 5 submissions before being classified (returns `insufficient_data`)
- **Difficulty gating:** Cannot be classified as Advanced overall without at least one Advanced-tier topic exposure
- **Evidence-based only:** Profile is driven by code review findings, never by chat content
- **Auditable:** Every change traces back to a `topic_review_event` with a `submission_id`

---

## 8. Data Flow

### Request Flow

```
1. User sends message to POST /chat
      ↓
2. summarization_node:
   ├─ Check token count vs budget
   ├─ If exceeded: cheap LLM summarizes oldest messages
   └─ Update GraphState.summary
      ↓
3. Chat node:
   ├─ Retrieve: mem0 search → long_term_memory
   ├─ Retrieve: Postgres PK lookup → skill_profile
   ├─ Build prompt:
   │   [system instructions]
   │   [long_term_memory]     ← mem0
   │   [skill_profile]        ← Postgres skill tables
   │   [summary]              ← GraphState
   │   [recent messages]      ← GraphState
   ├─ LLM call → response
   └─ Return new message to user
      ↓
4. Post-response (background):
   ├─ Batch store to messages table
   │   (user_message + ai_response)
   ├─ mem0.add() with batched content
   ├─ If code was submitted:
   │   ├─ Review pipeline → findings stored in topic_review_event
   │   └─ Skill aggregation → skill_topic + skill_profile updated
   └─ LangGraph checkpoint auto-saves state
```

### Data Storage Summary

| Data                | Where It Lives        | How It's Written                      | How It's Read                       |
| ------------------- | --------------------- | ------------------------------------- | ----------------------------------- |
| Chat messages       | `messages` table      | Batch-stored after response           | `GET /messages` with pagination     |
| Workflow state      | LangGraph checkpoints | Auto-saved by graph execution         | Internal only (not API)             |
| Cross-session facts | mem0 pgvector         | `memory_service.add()` after response | `memory_service.search()` each turn |
| Skill findings      | `topic_review_event`  | Review pipeline after code submission | SQL aggregation                     |
| Skill topic scores  | `skill_topic`         | Aggregation after findings stored     | PK lookup for profile injection     |
| Skill profile       | `skill_profile`       | Aggregation after findings stored     | PK lookup for prompt injection      |

---

## 9. What Changes

### What Changes

| Component                 | Current                                                                        | New                                                           |
| ------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------- |
| **GraphState**            | 6 fields (messages, long_term_memory, skill_profile, findings, code, language) | 4 fields (messages, summary, long_term_memory, skill_profile) |
| **Chat history storage**  | LangGraph checkpoints                                                          | `messages` table                                              |
| **POST /chat response**   | All messages in session                                                        | Only new messages                                             |
| **GET /messages**         | All messages, no pagination                                                    | Paginated with cursor                                         |
| **Skill profile input**   | `list[Finding]` only (negative)                                                | Code review findings with topic registry (evidence-based)     |
| **Skill profile storage** | mem0 memories                                                                  | Postgres `skill_profile` table                                |
| **Findings storage**      | mem0 memories (unstructured)                                                   | `topic_review_event` table (structured)                       |
| **Session summarization** | None (messages silently dropped)                                               | `summarization_node` with cheap LLM                           |
| **System prompt**         | No summary section                                                             | Includes `{summary}` section                                  |

### Dead Code to Remove

| Component                   | File                                      | Reason                                                  |
| --------------------------- | ----------------------------------------- | ------------------------------------------------------- |
| `store_finding()`           | `app/services/memory.py:113`              | Never called. Findings go to `topic_review_event` table |
| `get_all_session_finding()` | `app/services/memory.py:137`              | Never called. Findings retrieved via SQL                |
| `get_skill_profile()`       | `app/services/memory.py:164`              | Never called. Profile retrieved via SQL PK lookup       |
| `upsert_skill_profile()`    | `app/services/memory.py:191`              | Never called. Profile updated via aggregation           |
| `_profile_to_text()`        | `app/services/memory.py:223`              | Never called. Profile text generated from Postgres      |
| `SkillProfile` schema       | `app/schemas/skill_profile.py:24`         | Replaced by Postgres `skill_profile` table              |
| `Weakness` schema           | `app/schemas/skill_profile.py:17`         | Replaced by `skill_topic` table                         |
| `generate_skill_profile()`  | `app/utils/skill_profile_generation.py:1` | Replaced by pure SQL aggregation                        |

---

*This document is the complete system design. Implementation will follow as a separate phase.*
