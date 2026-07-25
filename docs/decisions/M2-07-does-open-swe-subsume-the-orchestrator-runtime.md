---
id: M2-07
parent: wayfinder:map2
labels: [wayfinder:research]
mode: AFK
blocked-by: []
assignee:
status: open
---

# M2-07 — Does Open SWE subsume the orchestrator, the runtime, or both?

## Question

LangChain's Open SWE is a live candidate that does not fit cleanly into one layer, which is why it
needs its own ticket rather than a pill in an existing one.

**What it appears to be:** an open-source asynchronous coding agent built on LangGraph and Deep
Agents, with Manager / Planner / Programmer / Reviewer stages, Linear and Slack invocation, GitHub
label triggers, automatic PR creation, and pluggable sandbox providers (Modal, Daytona, Runloop, E2B,
LangSmith). LangSmith supplies tracing and evaluation.

**Why it complicates the current architecture — three distinct questions:**

1. **Layer ambiguity.** It has its own Manager for routing and its own Linear/GitHub integration, so
   it may replace the orchestrator (L5), the agent runtime (L3), or both. If both, OpenSymphony and
   Kata drop out of the design entirely.
2. **It reopens the GPU-substrate question (Map I W-06).** Modal is a supported sandbox provider and
   Modal offers GPU sandboxes. If an Open SWE sandbox can hold a multi-hour training run, part of the
   two-substrate split may be available off the shelf rather than built.
3. **It forces a correction to the LangGraph rejection.** Map I rejected "LangGraph as the outer loop
   orchestrating coding agents" as a category error — the coding agent already IS the agent loop.
   Open SWE is a *different claim*: a coding agent **implemented in** LangGraph. That is not the
   rejected pattern and must not be dismissed by association.

**Also worth noting:** its own guidance says the architecture is good for complex, longer-running
tasks and *not* optimal for one-liner fixes — which is closer to the empirical lane's shape than to
the deterministic lane's. And its credential pattern matches the standing rule: GitHub operations run
with a dummy token inside the sandbox, backed by a proxy, so the agent never holds a real credential.

**Resolve:** which layer(s) it occupies, whether a sandbox survives a multi-hour compute-bound job
(this overlaps M2-01's timeout test and should be run against both), what the self-hosting story
costs versus the hosted platform, and whether adopting it pulls in LangSmith as a dependency for
observability — which would collide with the MLflow decision in M2-06.

**Date note:** sources describe both an August 2025 launch and a March 2026 release built on Deep
Agents. Establish which is current before relying on any specific capability claim.
