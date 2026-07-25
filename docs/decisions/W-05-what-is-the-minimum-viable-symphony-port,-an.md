---
id: W-05
parent: wayfinder:map
labels: [wayfinder:research]
mode: AFK
blocked-by: []
assignee: driver
status: closed
---

# W-05 — What is the minimum viable Symphony port, and where does it break?

## Question

Establish the real cost of implementing the Symphony spec in Python or TypeScript, and
where the spec's assumptions fail for this effort.

Specifically: which SPEC.md sections are core conformance versus optional; what the poll/claim/
workspace/reconcile/retry loop costs in lines; and — critically — how the design behaves for jobs
that run for hours rather than minutes. Note the spec's per-issue exponential backoff and its
in-memory scheduler state with no durable restart recovery.

Output: a port estimate plus the specific gaps a research lane would hit.

<!-- blocked by: nothing -->

## Resolution

**Verdict: do not port first. The spec's own recommended adoption path is to run the reference,
prove value on one ticket type, and port only after.**

The finding that changes W-08: OpenAI had Codex implement the spec in TypeScript, Go, Rust, Java
and Python — but explicitly as *spec-polishing exercises to surface ambiguity*, not production
implementations. Third-party assessment puts a production-grade port at **weeks, not hours**.
Community GitHub Issues tracker adapters already exist; Jira would need building.

The published adoption sequence is: stand up the Elixir reference against a sandbox Linear project
and one non-critical repo → pick one high-volume, well-tested ticket type (dependency bumps are the
named example) → measure time-to-PR and reviewer hours → expand ticket types one at a time →
*only then* consider porting to your stack's language. Forking the Elixir version is noted as fine
for many teams.

**Where the spec breaks for long jobs** (from SPEC.md directly):
- Scheduler state is in-memory with **no durable restart recovery** — retry timers and live sessions
  do not survive a process restart. Recovery is tracker-driven re-polling plus preserved workspaces.
  For a 6-hour training run, an orchestrator restart orphans the job.
- Failure retries are **per-issue exponential backoff**, `10000 * 2^(attempt-1)` capped at 5 min
  default. Thirty tickets failing on a common upstream break produce thirty independent retry storms
  — no circuit breaker. (This is stress-test scenario #4 from the suite, confirmed as a real gap.)
- `turn_timeout_ms` defaults to 1 hour, `stall_timeout_ms` to 5 min. A compute-bound job that emits
  no agent events for 5 minutes is killed as stalled.
- Workspace isolation is **filesystem-only** — separate directories, each with its own clone. That is
  the minimal isolation primitive, not a security boundary.

Explicit warning carried in the deployment guidance: don't point it at payment processing, auth
infrastructure, or anything where a bad commit is a P0. Blast-radius-limited services with strong
rollback only.
