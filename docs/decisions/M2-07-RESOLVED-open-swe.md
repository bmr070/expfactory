---
id: M2-07
parent: wayfinder:map2
labels: [wayfinder:research]
mode: AFK
status: RESOLVED
resolved: 2026-07-25
---

# M2-07 — Does Open SWE subsume the orchestrator, the runtime, or both?

## Verdict

**Adopt Open SWE at L3 (agent runtime) and the dispatch half of L5. It does not
subsume the experiment queue, and the W-06 two-substrate split survives — now on
design grounds rather than on the outcome of a timeout test.**

OpenSymphony and Kata drop out as *orchestrator* candidates: Open SWE covers the
same dispatch surface and is the better-maintained artifact. Their designs remain
worth reading (Kata's SSH worker pools, OpenSymphony's memory buckets); neither is
a foundation.

## Date ambiguity — resolved

The ticket flagged conflicting sources. Settled: the **17 March 2026** release is
current — MIT-licensed, **Python**, built on **Deep Agents + LangGraph**. The
August 2025 TypeScript launch is superseded. The repo today is `pyproject.toml` +
`uv.lock` + `agent/`, which confirms it. Capability claims sourced from the older
write-ups should be re-checked against the repo, not the blog posts.

## What it actually is (verified against the repo, not the marketing)

| Element | Finding |
|---|---|
| Entry | Webhooks (Slack / Linear / GitHub) → `agent/webapp.py` → LangGraph server |
| Agent | One main coding agent via `create_deep_agent`; **not** a Manager/Planner/Programmer/Reviewer persona org-chart |
| Reviewer | A *separate graph* with `add_finding` / `list_findings` |
| Sandbox | `SandboxBackendProtocol` (from `deepagents.backends.sandbox`); providers: LangSmith, Modal, Daytona, Runloop, E2B, Local |
| Selection | `SANDBOX_TYPE` env var, **defaults to `langsmith`** |
| Shell | `execute` — **300 s default timeout**, `timeout=<seconds>` to extend |
| Backstop | `SANDBOX_EXECUTE_CLIENT_GRACE_SECONDS` (default 30) — client-side kill if the server fails to enforce |
| Credentials | `GH_TOKEN=dummy gh <cmd>`; a proxy injects the real token. The agent never holds one. |
| Lifetime | Sandbox is per conversation thread and persists across follow-up messages |

Worth noting the reviewer being a separate graph is independent evidence for the
MAP.md position that only three roles earn isolation. Open SWE isolated exactly
the one that needs fresh context, and did not build the org chart.

## The three questions

### 1. Which layer(s)?

**L3 + the dispatch half of L5.** It owns: webhook routing, label triggers,
sandbox lifecycle, the agent loop, PR creation, review-as-separate-graph.

It does **not** own: an experiment queue, a durable store for compute-bound jobs,
a cross-job circuit breaker, or cost caps on GPU spend. Those are M2-03 and W-12
and remain ours to build or adopt separately.

### 2. Does a sandbox survive a multi-hour compute-bound job?

**It can be made to, and it still should not be used that way.**

`execute` accepts an arbitrary timeout, so a six-hour call is expressible. But the
shape is a **blocking shell call inside a live agent session**. Using it means
holding an LLM-metered session open for six hours to do nothing but wait. That is
precisely the duration mismatch MAP2 identified; Open SWE makes the timeout
*configurable*, which is not the same as making the shape *correct*.

So: the agent submits a job and detaches; the experiment queue owns the run and
the artifact comes back by reference. **W-06 stands.**

**Consequence for M2-01 — its stakes drop.** The timeout test was framed as
possibly collapsing two other tickets. It no longer decides the architecture,
because the objection is not "will a long call be killed" but "should an agent
session be the thing that waits" — and that answer is no at any timeout value.
M2-01 is still worth running (it is cheap, and it fixes the deterministic lane's
practical ceiling) but it is **downgraded from blocking to confirmatory**.

### 3. Does adopting it force LangSmith, colliding with MLflow (M2-06)?

**No.** `SANDBOX_TYPE` merely *defaults* to `langsmith`; the seam is a protocol
and Modal / Daytona / Runloop / E2B / Local are first-class. Even if LangSmith is
used, it occupies agent-session tracing, MLflow occupies ML-experiment tracking,
and **the ledger remains the only thing that adjudicates**. Three layers, no
contention — the standing rule that a green dashboard line is never a promotion
signal covers both.

Action: set `SANDBOX_TYPE` **explicitly** in config, so the dependency is a
decision on the record rather than an inherited default.

### 4. The LangGraph boundary

Confirmed correct and confirmed not triggered. MAP.md rejected *LangGraph as an
outer loop orchestrating coding agents*. Open SWE is *a coding agent implemented
in LangGraph*. Adopting it does not resurrect the rejected pattern. This is the
distinction the handoff warned against collapsing, and it held up.

## Adopt regardless of the orchestrator decision

The **dummy-token proxy** pattern. `GH_TOKEN=dummy` inside the sandbox with a
proxy injecting the real credential is invariant 6 implemented cleanly, and it is
the template for how our agent should touch the tracker and the GPU substrate —
whatever we end up running.

## Residual — needs the user's accounts

Self-hosting cost versus the hosted platform could not be evaluated from the
outside. Deferred to the same session that does ticket 01 provisioning.

## Sources

- [Open SWE repo](https://github.com/langchain-ai/open-swe) — `agent/prompt.py`
  (300 s default), `agent/server.py` (`SANDBOX_TYPE`), `agent/utils/sandbox_state.py`
  (`SandboxBackendProtocol`), `agent/integrations/langsmith.py` (grace seconds)
- [System architecture](https://deepwiki.com/langchain-ai/open-swe/1.1-system-architecture)
- [byteiota — Open SWE 2026](https://byteiota.com/open-swe-langchain-autonomous-coding-agent/)
- [LangSmith Sandboxes](https://blog.langchain.com/introducing-langsmith-sandboxes-secure-code-execution-for-agents/)
