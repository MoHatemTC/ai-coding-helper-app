# Skill Profile — System Design

## 1. Purpose

The Skill Profile is a per-user, evidence-based competency model derived from code the user submits for review. It answers: *"What is this user's current skill level, per topic, based on demonstrated evidence?"*

It is intentionally separate from general conversational memory (preferences, context, freeform facts about the user). Skill Profile is structured, decays over time, and is driven only by code-review evidence — never by chat content.

---

## 2. Design Goals

- **Unbiased**: must not systematically trend every active user toward "weak" just because they use the tool.
- **Difficulty-aware**: performance on hard topics should count more than performance on trivial ones.
- **Simple**: minimal tables, minimal LLM calls beyond the review that already happens.
- **Auditable**: every derived score should be traceable back to raw evidence.
- **Cheap to read**: skill profile is read on every relevant chat/review request — must be a fast lookup, not a live aggregation over large data.

---

## 3. The Core Problem This Design Solves

A code-review assistant only sees code when a user chooses to submit it for checking. Two biases fall out of that naturally if not corrected:

1. **Selection bias** — code reaching the reviewer isn't representative of the user's overall ability; users submit specifically when they want validation.
2. **Absence-of-success bias** — if only *findings* (bugs/issues) are recorded, the evidence store is 100% negative by construction. A user who writes 50 clean submissions and 1 buggy one looks identical to a user who submits once and it happens to be buggy, unless clean topic exposure is also recorded.

A secondary problem: **not all topics are equal.** A low error rate on trivial topics (loops, naming) can outscore a higher error rate on hard topics (concurrency, security) under a naive rollup — rewarding avoidance of difficulty rather than actual competence.

This design fixes both.

---

## 4. Data Flow

```
Code Submission
     |
     v
Review LLM (structured output)
     |
     v
[
  { topic: "sql.injection", findings: [] },        <- clean exposure, topic still listed
  { topic: "async", findings: [ {severity, message, rationale, line} ] }
]
     |
     v
Insert ONE row per topic in the output — findings present or not
     |
     v
topic_review_event (raw, append-only)
     |
     v
Aggregation (GROUP BY topic, per user)
     |
     v
skill_topic (derived, per-topic level/trend)
     |
     v
skill_profile.overall_level (weighted rollup across topics)
     |
     v
Injected into system prompt on code-review-relevant requests only
```

**Load-bearing rule:** the review prompt must instruct the LLM to list *every topic the code touches*, not only topics with problems. This single behavior is what turns "findings" into a true rate rather than a one-sided tally. This should be covered by a regression test (feed known-clean code, assert the topic still appears with an empty findings list).

---

## 5. Schema

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

---

## 6. Scoring Logic

### Per-topic level

```
exposures            = count(*) WHERE user_id = X AND topic = Y  [within lookback window]
weighted_severity_sum = sum(weighted_severity) WHERE user_id = X AND topic = Y

weakness_rate = weighted_severity_sum / exposures

level =
  insufficient_data   if exposures < MIN_EXPOSURES   (e.g. 5)
  Advanced            if weakness_rate <  T_advanced
  Intermediate        if T_advanced <= weakness_rate < T_beginner
  Beginner            if weakness_rate >= T_beginner
```

The `insufficient_data` floor is the direct fix for "one bad submission = permanently flagged weak."

**Trend**: recompute `weakness_rate` over the most recent short window (e.g. last 2 weeks) vs. the prior window; compare to classify improving / stable / declining.

### Overall profile score (difficulty-aware rollup)

Per-topic scores alone are fine in isolation, but a naive average across topics rewards avoiding hard topics. Fix: weight each topic's contribution by its `topic_weight` (difficulty) in addition to `exposures`.

```
topic_score(Y)     = inverse of weakness_rate(Y)   (higher = more competent)
contribution(Y)    = topic_score(Y) × exposures(Y) × topic_weight(Y)

overall_score = sum(contribution(Y) for all topics Y) / sum(exposures(Y) × topic_weight(Y) for all Y)
```

**Guard rail (gate condition):**
```
overall_level = Advanced  only if:
  overall_score >= advanced_threshold
  AND user has >= MIN_EXPOSURES in at least one topic with topic_weight = Advanced-tier
```

This prevents a high score built entirely from trivial-topic volume from being labeled Advanced.

---

## 7. What This Design Deliberately Excludes (for simplicity)

| Considered                                                     | Decision                         | Why                                                                                                                                   |
| -------------------------------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Separate exposure table                                        | Merged into `topic_review_event` | LLM's full topic list already encodes exposure — no need for a second table                                                           |
| Hint-resolution signal (how much help was needed to fix a bug) | Deferred                         | Adds real signal but isn't required to fix the core bias; can be added later as an additive column/table without touching this schema |
| Vector store / mem0 for skill data                             | Rejected                         | Skill data is structured, fetched by exact key, never by semantic similarity — plain Postgres is faster and cheaper                   |
| Full LLM regeneration of profile on each update                | Rejected                         | Replaced by SQL aggregation over raw events — no LLM call beyond the review that already happens                                      |
| Chat-mentioned topics affecting skill level                    | Rejected                         | Chat is a weak, ambiguous signal (curiosity vs. gap); kept in a separate `topic_interest` table if desired, never feeds `skill_topic` |

---

## 8. Why This Is Cheap

- **One LLM call per submission** — the review call that already exists; topic tagging is an extension of its existing structured output (same pattern as `Severity`/`Category`).
- **No embeddings, no vector search** — skill data is fetched by `user_id` PK lookup, not similarity search.
- **Read path is a cached aggregate** — `skill_topic` is a derived cache; the raw event table is the source of truth, so the scoring formula can change later without a data migration — just rerun the aggregation.

---

## 9. Example Scenario (validates the design against the original bias)

**User A**: 50 clean submissions on `loops`, 1 submission on `loops` with a low-severity finding.
- `exposures = 51`, `weighted_severity_sum ≈ 0.5` → `weakness_rate ≈ 0.01` → Advanced on `loops`.
- But `loops` has `topic_weight = 1` (Basic) → low contribution to `overall_score`.
- Gate check fails (no Advanced-tier topic with sufficient exposure) → `overall_level` capped below Advanced.

**User B**: 5 submissions on `concurrency` (Advanced-tier), 2 with medium-severity findings.
- `exposures = 5`, meets `MIN_EXPOSURES` → real (not `insufficient_data`) score computed.
- Higher `weakness_rate` than User A, but high `topic_weight` → meaningful contribution, and gate check passes.
- Result: User B can plausibly reach Advanced despite a higher raw error count, because the evidence is on harder ground — while User A is correctly capped despite a near-zero error rate on trivial ground.

This is the direct validation case for the design and is worth encoding as an automated regression test.
