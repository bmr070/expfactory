---
id: W-04
parent: wayfinder:map
labels: [wayfinder:research]
mode: AFK
blocked-by: []
assignee: driver
status: closed
---

# W-04 — What does gh-aw actually enforce, and does it fit non-GitHub workloads?

## Question

Establish the real capability envelope of GitHub Agentic Workflows.

Specifically: what the safe-outputs boundary enforces and how it is configured; what the
containerised sandbox permits and denies by default; whether long-running or GPU-backed jobs are
viable on Actions runners or self-hosted runners; how concurrency and scheduling are expressed;
and what the compiled .lock.yml can and cannot express.

Output: a capability matrix plus an explicit list of things it cannot do, which is the input to
W-08.

<!-- blocked by: nothing -->

## Resolution

**Verdict: adopt for the deterministic lane; it cannot host the empirical lane.**

Five security layers, all documented and default-on:
1. **Read-only token** — the agent's GitHub token cannot write. Not policy; capability.
2. **Zero secrets in the agent** — write tokens and API keys live only in separate jobs that run
   *after* the agent finishes and its output passes review. A compromised agent has nothing to steal.
3. **Containerised + Agent Workflow Firewall** — outbound traffic routed through a Squid proxy with
   an explicit domain allowlist; everything else dropped at kernel level.
4. **Safe outputs** — agent emits a structured artifact of *intended* actions; a gated job with
   scoped writes applies only what the workflow permits, with hard per-operation limits (e.g. max
   one issue per run), required title prefixes, label constraints. 8+ output types.
5. **Agentic threat detection** — an AI scan of proposed changes for prompt injection, leaked
   credentials, malicious patterns *before* anything is applied. Fails the run on suspicion.

Engines: Copilot, Claude, Codex, custom. Compile-time scanners: actionlint, zizmor, poutine,
runner-guard, validate-images.

**Cost guardrail exists natively**: `max-daily-ai-credits`, default 5000 AIC (~$50/day), org-wide
override via `GH_AW_DEFAULT_MAX_DAILY_AI_CREDITS`. Bypassed for manual and slash-command runs.

**Where it stops.** Agent step defaults to 20 min; GitHub-hosted runners hard-cap at 360 min and a
higher value compiles fine but fails at runtime. Self-hosted runners lift this (technically 35 days)
but the project's own issue tracker calls that "not a practical limit for agentic workflows".
Per-tool-call timeouts default to 60s (Claude) / 120s (Codex).

**The disqualifying constraint is not the timeout — it is the output model.** Safe outputs are
GitHub objects: issues, PRs, comments, labels. An adjudicated experiment result is a ledger row plus
artifacts, which safe-outputs does not model. Forcing it through would mean encoding gate verdicts
as PR comments, losing the append-only ledger that makes the empirical lane defensible.

Also note: the project self-describes as "in early development and may change significantly."
