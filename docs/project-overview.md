# Project Overview: Coding Helper (AI Mentor & Code Reviewer)

## Executive summary

Coding Helper is not a tool that writes ready-made, copy-and-paste code. It is an intelligent **AI Mentor** and **Senior Code Reviewer**. It is designed to guide you, analyze your code, and push you to reach the solution yourself instead of solving the problem on your behalf.

The goal is simple: developers get real, hands-on guidance that strengthens their engineering skills, rather than becoming fully dependent on AI to write code they may not fully understand.

## Problem and solution

### The problem

Beginner and intermediate developers are increasingly losing their problem-solving skills because they rely on AI to write entire solutions for them. This creates real risks:

- Code gets shipped without the author truly understanding it
- Core skills such as debugging, reasoning, and design thinking atrophy
- Edge cases, performance, and security are overlooked because the AI, not the developer, made the decisions
- Teams inherit code that no one on staff can confidently explain or maintain

### The solution

Coding Helper acts as a Tech Lead who works *alongside* you. Instead of handing over the correct code, it reviews what you wrote, explains the reasoning behind good and bad choices, and guides you with hints and questions until you arrive at the best solution yourself.

It reviews your code line by line, discusses engineering decisions with you, and leaves the actual writing to you — so that you learn and grow with every problem you solve.

## Key stakeholders

### Developers and learners

Developers receive genuine code reviews, guided hints instead of finished answers, and clear explanations of the concepts behind each recommendation. They build lasting problem-solving skills instead of a dependency on AI.

### Engineering teams

Teams get members who genuinely understand the code they ship. Coding Helper reinforces clean code, design patterns, and security-first thinking, raising the overall quality bar across the codebase.

### Team leads and mentors

Leads gain a scalable way to provide senior-level mentorship. The assistant handles routine review and coaching, freeing human seniors for the decisions that truly need their judgment.

## Business goals

### Real learning, not shortcuts

The assistant should protect and grow the developer's problem-solving ability. It gives hints, logical next steps, and questions ("What's the first thing you need to think about here? Try looking into this tool. How about structuring the data this way?") instead of the finished solution.

### Real code review, not just correction

When a user submits code or describes a problem, the assistant performs a genuine code review. It points to specific issues: "Line 12 has a problem because you're not handling the edge cases," or "This loop could be faster if you used a Map."

### Explain the *why*, not just the *how*

The assistant should teach the reasoning behind every recommendation. If there is a security flaw, it explains how the vulnerability arises and why the current code is dangerous — not just what to change.

### Improve code quality and best practices

The assistant should coach users to write clean code, follow design patterns, and think about performance and security from the very start.

### Remember context across sessions

The assistant should remember the user's level and past questions. If it sees the same mistake repeated, it should flag it: "Remember when we talked about memory leaks last week? This code can cause the same problem."

## Rollout plan

### Phase 1: Interactive reviewer

This phase focuses on reviewing code the user submits.

Planned outcomes:

- Accept pasted code or a described problem and return a real code review
- Identify bugs, unhandled edge cases, and risky patterns with specific line references
- Suggest improvements without writing the final solution for the user
- Clearly state when something is uncertain or needs more context

### Phase 2: Guided mentor

This phase moves the assistant from reviewing to actively coaching.

Planned outcomes:

- Answer "how do I…?" questions with hints and logical steps instead of finished code
- Explain the concepts and the *why* behind each recommendation
- Walk through security flaws and performance trade-offs so the user understands the risk
- Encourage the user to reach and write the solution themselves

### Phase 3: Long-term coach

This phase makes the mentorship personal and continuous.

Planned outcomes:

- Remember the user's skill level and history of questions across sessions
- Detect repeated mistakes and connect them to earlier conversations
- Adapt the depth of hints and explanations to the user's growing ability
- Track progress over time so guidance keeps pace with the developer

## Final handoff

At the end of the project, stakeholders will receive:

- A live AI mentor that reviews code and guides developers instead of writing for them
- A guided problem-solving experience built on hints, questions, and next steps
- Concept explanations that teach the reasoning behind every recommendation
- Persistent, per-user memory that personalizes coaching and catches recurring mistakes
- An interactive pair-programming experience with a Senior Developer style of review
- A safe alternative for workplaces that want their engineers to grow, not just copy code

---

**In short:** Coding Helper is not a programmer that works *instead of* you — it is a Tech Lead that works *with* you. It reviews your code, points you toward the right path, and leaves the solution to you, so that you learn and grow as an engineer.
