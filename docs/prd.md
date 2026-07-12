# Product Requirements Document — Coding Helper (AI Mentor & Code Reviewer)

| | |
| --- | --- |
| **Product** | Coding Helper — AI Mentor & Code Reviewer |
| **Status** | Draft |
| **Author** | Engineering |
| **Last updated** | 2026-07-09 |
| **Related docs** | [Project overview](project-overview.md) · [Delivery plan](plan/README.md) · [Architecture](architecture.md) |

## 1. Overview

Coding Helper is an AI mentor and senior code reviewer for developers. It reviews submitted code with line-level findings, guides users toward solutions with progressive hints and questions, explains the concepts behind every recommendation, and remembers each user's level and history across sessions — while deliberately **never writing the complete solution** for the user.

It is built on the existing FastAPI + LangGraph foundation in this repository (PostgreSQL + pgvector, mem0 long-term memory, JWT auth, Langfuse observability, Prometheus/Grafana monitoring).

## 2. Problem statement

Beginner and intermediate developers increasingly rely on AI to write entire solutions for them. This causes real damage:

- Problem-solving, debugging, and design skills atrophy from disuse
- Code ships without its author truly understanding it
- Edge cases, performance, and security are overlooked because the AI, not the developer, made the decisions
- Teams inherit code no one on staff can confidently explain or maintain

Existing AI coding tools optimize for producing code fast, which makes the dependency worse. There is no widely used tool that optimizes for making the *developer* better.

## 3. Goals

1. **Real learning** — protect and grow the user's problem-solving ability; hints, steps, and questions instead of finished code.
2. **Real code review** — specific, line-referenced findings ("line 12 misses the empty-input case"), covering correctness, security, and performance.
3. **Explain the why** — every recommendation teaches the concept behind it; security findings explain how the exploit happens and why the code is dangerous.
4. **Code quality & best practices** — coach users toward clean code, design patterns, and performance/security thinking from the start.
5. **Continuous mentorship** — remember the user's level and history; catch repeated mistakes ("remember the memory-leak discussion last week?"); adapt depth as the user grows.

## 4. Non-goals

- Generating complete, ready-to-paste solutions — this is explicitly against the product's core rule, even when users push for it.
- Being a general coding autocomplete or IDE copilot — Coding Helper is a conversational mentor, not an inline code generator.
- Executing or automatically fixing the user's code — analysis and guidance only; small illustrative snippets are the ceiling.
- Formal grading or certification — it coaches; it does not score people for third parties.

## 5. Users & personas

| Persona | Needs | What they get |
| --- | --- | --- |
| **Beginner / junior developer** | Learn to solve problems, understand mistakes | Guided hints, concept explanations, patient step-by-step coaching |
| **Intermediate developer** | Level up code quality, patterns, security awareness | Senior-style reviews, best-practice coaching, terser nudges as they grow |
| **Engineering team / employer** | Staff who understand what they ship | A safe AI alternative that develops employees instead of replacing their thinking |
| **Team lead / mentor** | Scale senior-level mentorship | Routine review and coaching handled; humans reserved for judgment calls |

## 6. User stories

### Developer (learner)

- As a developer, I can submit a code snippet and get a real review: what's wrong, on which line, and why it matters.
- As a developer, when I ask "how do I do X?", I get a hint and a logical next step — and deeper hints only if I'm still stuck.
- As a developer, I can ask "why?" about any finding and get a clear explanation of the underlying concept with a small illustrative example.
- As a developer, if my code has a security flaw, the assistant explains how the vulnerability works and why my code is at risk.
- As a developer, the assistant remembers my past sessions — if I repeat an old mistake, it points back to when we discussed it.
- As a developer, even if I demand the full solution, the assistant redirects me to hints instead of handing it over.

### Engineering team / lead

- As a team lead, I can offer juniors a mentor that coaches them without doing their work for them.
- As an employer, I can adopt an AI tool knowing it strengthens my engineers' understanding instead of replacing it.

## 7. Functional requirements

Requirements are grouped by rollout phase; each maps to a sprint in the [delivery plan](plan/README.md).

### Groundwork — Foundation (Sprint 1)

| ID | Requirement | Priority |
| --- | --- | --- |
| F0.1 | Establish the mentor persona via system prompt: senior reviewer voice, "guide, don't solve" rule, explain-the-why rule, honest uncertainty | Must |
| F0.2 | Accept code submissions (snippet + optional language) through the chat API with validation and size limits | Must |
| F0.3 | Carry code, language, and review context in graph state across turns (checkpointed) | Must |
| F0.4 | Configure a code-capable LLM with review-tuned parameters, environment-driven, with working fallback | Must |

### Phase 1 — Interactive reviewer (Sprint 2)

| ID | Requirement | Priority |
| --- | --- | --- |
| F1.1 | Structured code-review tool producing typed findings (location, severity, category, message, rationale) | Must |
| F1.2 | Every finding references a concrete line or range in the submitted code, validated against code length | Must |
| F1.3 | Correctness review: unhandled edge cases and bugs, each with a concrete failure scenario | Must |
| F1.4 | Security review: common vulnerability classes with exploit-path and impact explanations | Must |
| F1.5 | Performance & best-practice suggestions that name a better approach conceptually without pasting the rewrite | Should |

### Phase 2 — Guided mentor (Sprint 3)

| ID | Requirement | Priority |
| --- | --- | --- |
| F2.1 | Progressive hints: nudge → direction → concrete step, escalating only when the user is still stuck | Must |
| F2.2 | Concept explanations on demand — the "why" behind any finding or hint, with a minimal illustrative example | Must |
| F2.3 | Guided (Socratic) problem-solving flow: one leading question per turn, reacting to the user's answers | Should |
| F2.4 | No-complete-solution guardrail that survives extraction attempts and redirects to hints | Must |
| F2.5 | Consistent, structured response format across review / hint / explain modes | Should |

### Phase 3 — Long-term coach (Sprint 4)

| ID | Requirement | Priority |
| --- | --- | --- |
| F3.1 | Per-user memory of skill level, topics, and weaknesses (mem0 + pgvector), retrieved each session | Must |
| F3.2 | Repeated-mistake detection with accurate callbacks to the earlier occurrence (semantic matching) | Must |
| F3.3 | Adaptive hint depth: more scaffolding for beginners, terser nudges as the user improves | Should |
| F3.4 | Progress tracking across sessions with a retrievable summary | Should |
| F3.5 | Mentoring-quality evals (hint usefulness, explanation quality, no-solution-leak) as a regression gate | Must |

## 8. Non-functional requirements

| Category | Requirement |
| --- | --- |
| **Mentoring integrity** | The no-complete-solution rule holds under pressure; illustrative snippets only; measured by the no-solution-leak eval |
| **Review accuracy** | Line references always valid for the submitted code; correctness findings name a concrete failure scenario; low false-positive noise on trivial code |
| **Latency** | Chat responses stream; first token within a few seconds under normal load |
| **Security** | JWT-authenticated sessions; all endpoints rate-limited; submitted code treated as data, never executed; no secrets in code |
| **Privacy** | Per-user memories scoped by `user_id` with no leakage between users; submitted code stored only as needed for review context and memory |
| **Reliability** | LLM calls retried with tenacity + circular model fallback; memory failures degrade gracefully (chat still works) |
| **Observability** | All LLM calls traced in Langfuse; Prometheus metrics; structured logging with request/session/user context |
| **Quality assurance** | Mentoring metrics run via the eval suite (`evals/`) with JSON success-rate reports |
| **Maintainability** | Follows [AGENTS.md](../AGENTS.md) conventions; passes `make check` (lint + typecheck) |

## 9. Success metrics

| Metric | Target (initial) |
| --- | --- |
| No-solution-leak eval success rate | ≥ 95% (including adversarial extraction attempts) |
| Findings with valid line references | 100% (invalid references never reach the user) |
| Hint-usefulness eval success rate | ≥ 85% |
| Concept-explanation quality eval success rate | ≥ 85% |
| Repeated-mistake callbacks that are accurate | ≥ 90% (no false "you did this before") |
| Users reaching the solution themselves in guided flows | ≥ 70% of guided sessions |
| Median first-token latency | < 5 seconds |

Targets are initial estimates — revisit after Sprint 2 with real usage.

## 10. Rollout & milestones

| Milestone | Scope | Delivery plan |
| --- | --- | --- |
| M1 — Mentor foundation | Persona, code intake, state, model config, E2E smoke | [Sprint 1](plan/sprint-1/) |
| M2 — Phase 1 complete | Structured review: line-referenced correctness, security, performance findings | [Sprint 2](plan/sprint-2/) |
| M3 — Phase 2 complete | Progressive hints, concept explanations, Socratic flow, no-solution guardrail | [Sprint 3](plan/sprint-3/) |
| M4 — Phase 3 complete | Per-user memory, repeated-mistake callbacks, adaptive depth, progress tracking, mentoring evals | [Sprint 4](plan/sprint-4/) |

## 11. Assumptions & dependencies

- A code-capable LLM is available through the configured provider (via `LLMRegistry`), with fallback models configured.
- The existing platform services (PostgreSQL + pgvector, mem0, Langfuse, Prometheus/Grafana) remain available.
- Users are authenticated (JWT) so memory and progress can be keyed per user.
- Code is submitted as text snippets through the chat API in the initial scope (no repo/IDE integration yet).

## 12. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| The model leaks full solutions despite the persona | Layered guardrail (prompt policy + guard step), adversarial test conversations, no-solution-leak eval as a regression gate (F2.4, F3.5) |
| Hallucinated line numbers or wrong findings | Numbered code sent to the LLM, line-reference validation, concrete-failure-scenario requirement, tuning against trivial samples (F1.2, F1.3) |
| Hints too vague to help — users just get frustrated | Progressive escalation levels, hint-usefulness eval, adaptive depth by skill level (F2.1, F3.3, F3.5) |
| False "you made this mistake before" callbacks | Semantic-similarity threshold and accuracy validation before shipping callbacks (F3.2) |
| Users abandon the tool for answer-dispensing AIs | Friendly redirects instead of cold refusals; visible progress tracking that shows the payoff of learning (F2.4, F3.4) |
| Memory leakage between users | Memories strictly keyed by `user_id`; scoping verified in review/tests (F3.1) |

## 13. Open questions

- Which languages should the reviewer officially support at launch, and which are best-effort?
- Should there be a "solution unlock" escape hatch (e.g. after N genuine attempts), or is the no-solution rule absolute?
- What are the maximum code-submission size and format (single snippet vs. multiple files) for v1?
- Should progress summaries be exposed as an API endpoint, in-chat only, or both?
- Do teams/employers get any visibility (aggregate progress of their developers), and what are the privacy boundaries if so?
