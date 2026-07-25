---
id: W-08
parent: wayfinder:map
labels: [wayfinder:grilling]
mode: HITL
blocked-by: [W-04, W-05, W-06]
assignee: driver
status: closed
---

# W-08 — Buy or build the outer loop?

## Question

Decide the dispatcher: gh-aw, a Symphony port, the Copilot coding agent path, or a
hybrid.

The prior recommendation was buy-then-build — gh-aw first because its safe-outputs boundary is
weeks of security work already done — with a port only once you outgrow it. W-04, W-05 and W-06
exist to confirm or kill that recommendation with facts rather than priors.

Resolve only after all three research tickets are closed.

<!-- blocked by: W-04, W-05, W-06 -->

## Resolution

**Verdict: hybrid, split by lane. gh-aw drives the deterministic lane; a minimal custom runner —
Symphony's loop, not Symphony's code — drives the empirical lane. Linear/Issues stays the shared
control plane above both.**

The three research tickets forced this. Neither reference tool spans both lanes:
- **gh-aw cannot host the empirical lane** (W-04): its safe-outputs model emits GitHub objects, and
  an adjudicated experiment result is a ledger row plus artifacts. But its five security layers are
  weeks of hardening already done, so it is the right home for the deterministic lane and the CI
  adapter from W-02.
- **A full Symphony port is weeks and premature** (W-05); its scheduler also breaks on long jobs
  (no restart recovery, no circuit breaker, 5-min stall kill). So don't port it — *reimplement only
  its loop* for the empirical lane, fixing exactly those three breakages because a GPU experiment
  run hits all of them.
- **Execution is two substrates anyway** (W-06), which the custom runner already has to straddle;
  gh-aw would fight that.

**The minimal custom runner (empirical lane only):** poll tracker for `agent-ready` + empirical
label → claim → prepare per-issue workspace → dispatch coding agent → agent submits GPU job to the
separate substrate and receives an artifact → run the verifier plugin (ledger + gates) → post
verdict + proof-of-work bundle back to the ticket → move to review. Add the three fixes Symphony
lacks: durable state so an orchestrator restart resumes in-flight runs, a global circuit breaker
that halts dispatch on N correlated failures, and a stall timeout tuned for compute-bound silence.

**Rejected:** LangGraph / CrewAI / AutoGen — category error. The coding agent already *is* the agent
loop; what sits above it is a supervised job runner with a small state machine, and a software
factory is squarely in the "custom orchestration beats framework" tier.


## CORRECTION (post-research, evidence-driven)

The original verdict said "reimplement Symphony's loop." Verified repo research changes the
*how*, not the shape:

**Fork mraza007/baton as the empirical-lane runner.** 3 commits, 15 stars — a weekend project, and
that is the point. It is **Python**, 9 readable modules (config, tracker, workspace, hooks, prompt,
worker, state, orchestrator, cli) with the exact poll-dispatch-reconcile loop and
Unclaimed→Claimed→Running→RetryQueued→Released state machine. Forking beats adopting because
nothing off-the-shelf does empirical verification, so the runner was always going to be modified
substantially. **The deciding factor is seam language:** the verifier core is Python, so the
GateVerifier plugs into worker.py in-process rather than across a subprocess/FFI boundary.

**Read, don't depend on, the Rust implementations:**
- kumanday/OpenSymphony (641 commits, 47 releases, 65*, v1.0.0) — port the memory-bucket design
  (blocking predecessors, completed children/siblings, path matches) and reconciliation logic.
- gannonh/kata (1,311 commits, 57 releases, 24*) — **SSH worker pools** are the template for GPU
  dispatch (ticket 06); also per-state slot limits and fresh-context subagents.

**Confirmed non-existent:** "Stokowski" is not a real project.
**Confirmed avoid:** macaron-software/software-factory — persona-agent architecture
(PERSONA→REQUIREMENTS→DESIGN→DEV→TESTING), self-built by AI agents, claims SOC2/ISO27001 which are
organizational audits a repo cannot hold. One idea worth stealing: its L0/L1/L2 tiered verification.

**Consequence:** VerdictBundle does NOT need cross-language serialization. The seam is an in-process
Python call, so refactor candidate 1 (typed `runs`) matters more and the serialization constraint
raised earlier is void.

## SUPERSEDED (second correction)

The CORRECTION above — "fork mraza007/baton" — is **withdrawn**. The Python-seam argument that
justified it did not survive scrutiny: a subprocess boundary costs almost nothing, so "same language"
was never a real tiebreaker. Baton is eliminated as a foundation and retained only as a ~200-line
reading exercise.

This ticket is now superseded by **M2-02** (orchestrator: final pick, and is it even load-bearing?)
and **M2-07** (does Open SWE subsume the orchestrator, the runtime, or both?). Do not act on either
verdict recorded above without reading those.

Consequence carried forward: because the runner is no longer assumed to be Python, **the
VerdictBundle serialization constraint is live again** — the seam is likely a subprocess/artifact-file
boundary, not an in-process call.
