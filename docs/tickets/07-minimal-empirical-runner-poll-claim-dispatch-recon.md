# 07 — Minimal empirical runner: poll, claim, dispatch, reconcile

**What to build:** The outer loop runs unattended: it polls for agent-ready empirical tickets, claims one into a per-issue workspace, dispatches the agent, runs the verifier, posts the verdict and proof-of-work back to the ticket, and moves it to review.

**Blocked by:** 01, 05

**Status:** ready-for-agent

- [ ] Runner polls Issues, claims an agent-ready lane:empirical ticket, and prepares an isolated workspace
- [ ] On completion it posts verdict + proof-of-work bundle and moves the ticket to In Review
- [ ] Concurrency is bounded and only human-tagged tickets are eligible
- [ ] Tracker credentials live in the runner's secret store, never in a workspace
