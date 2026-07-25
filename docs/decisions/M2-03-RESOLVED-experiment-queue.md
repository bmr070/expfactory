---
id: M2-03
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
status: RESOLVED — awaiting ratification
resolved: 2026-07-25
supersedes-blocking-edge: M2-01
---

# M2-03 — Experiment queue: build, adopt, or does it not exist?

## Verdict

**No general-purpose orchestrator. Adopt the compute substrate's own job
primitive (Modal `spawn` → handle → poll), and build a thin durable
`JobRegistry` that records outstanding submissions and owns the failure
semantics.**

Prefect, Metaflow, Celery/RQ are all declined — each for a different reason, and
Metaflow on a safety ground rather than a convenience one.

The ticket's own hunch was right: *"Since the ledger already IS the tracking
layer, the usual recommendation may invert."* It does.

## The stale blocking edge

This ticket carried `blocked-by: [M2-01]`, because one option was *"nothing, if
W-01 shows long hooks survive."* M2-07 already eliminated that option on
structural grounds — a blocking shell call inside a live agent session holds an
LLM-metered session open for hours, and no timeout value changes that. The
"nothing" branch is dead regardless of what the timeout test measures, so the
edge is stale and this ticket was takeable.

## What actually has to be owned

From W-06, W-08, W-12 and the two unspecified items in Map II:

1. Accept a job from an agent that then **detaches**
2. Run it on a GPU substrate (Modal / Northflank / Beam are the only ones with GPU sandboxes)
3. Collect the artifact
4. Run gates, append to the ledger
5. Durable restart state
6. A global circuit breaker
7. A compute-tuned stall timeout
8. Cost caps — per-experiment and per-day GPU aggregate, **fail-closed**
9. A ticket state meaning "running, unattended"
10. **If the queue loses a job, someone must notice**

Scale that against reality: a solo factory, ≤3 concurrent agents in v1, one live
workload. Most of that list is *bookkeeping*, not orchestration.

## Why each candidate loses

### Metaflow — declined on safety, not fit

Its headline feature is built-in versioned artifacts. That is precisely what the
ledger is. Adopting it would install a **second store that also believes it
records what happened**, next to the one thing that is allowed to adjudicate.

The standing rule is that a green dashboard line is never a promotion signal
(MLflow is admitted only under that rule). Metaflow is worse than MLflow here,
because MLflow is obviously a dashboard whereas Metaflow's artifact store looks
authoritative. The failure mode is someone — human or agent — reading a promotion
out of the wrong store. **The cost is not duplication, it is ambiguity about
which record is the truth**, and that ambiguity is the exact thing this project
exists to remove.

### Prefect — declined as redundant, not wrong

The best of the three on fit: general-purpose, Python-native, expects you to
bring your own tracking, which is right because we have. Its commonly-cited
weakness — a weaker lineage story — is a non-issue, since lineage lives in the
ledger's `parent_id` chain.

But what it would supply here is durable retry state over long jobs, and **Modal
already supplies that** for the jobs in question. Adopting Prefect means running
two queues: Modal's, which we cannot avoid because it owns the GPU, and
Prefect's on top. It is the strongest fallback if the registry outgrows a file
(see *Reversibility*), and it is not needed at v1 scale.

### Celery / RQ — declined on operational weight

Needs a broker (Redis) to babysit, and solves none of the GPU problem. Pure
addition.

### "Nothing" — eliminated by M2-07

See above.

## What is adopted, and what is built

> **Adopt infrastructure. Build verification.**

- **Adopted:** the compute substrate's job primitive. Modal's `spawn` returns a
  durable handle; results persist and are pollable after the submitting process
  is gone. That is a job queue, it is already paid for, and it is where the GPUs
  are.
- **Built:** a `JobRegistry` — a durable record of *(ticket, submitted handle,
  deadline, cost estimate)* and the transitions out of it.

**Is building the registry a violation of the thesis?** No, and the distinction
matters. A general-purpose queue would be infrastructure, and we adopt it. What
the registry holds is the link between a ticket, a job that is still running, and
the verdict it will eventually produce — plus who notices when that link breaks.
That is verification bookkeeping. It is on the build side of the line by the same
reasoning that put the ledger there.

It is small (~200 lines) *because* Modal does the hard part, not because the
problem is small.

## Answering the two unspecified items

### Failure semantics — if the queue loses a job, who notices?

**The registry, and only the registry.** Nothing else can: the agent session
ended hours ago, and the tracker only knows the ticket says "In Progress." A
submitted entry that is still unresolved past its deadline is a lost job → trip
the circuit breaker, move the ticket to `needs-human`, and **do not auto-retry**
(W-12 already forbids auto-retry on cost; the same applies to a job whose state
is unknown, because retrying may double-spend GPU budget).

This is why the registry cannot be optional. A queue with no one watching it is
how a six-hour run silently disappears.

### Where ticket state lives during a six-hour run

A `running-unattended` state, entered when the agent submits and detaches, exited
only by the registry. It is deliberately distinct from `In Progress`, which
implies someone is working; here nobody is, by design, and the distinction is
what makes a stuck job visible rather than merely old.

## Substrate stays swappable

The registry talks to a `ComputeSubstrate` protocol — `submit`, `poll`,
`fetch_artifact` — with Modal as the first implementation. W-06's two-substrate
split (the agent never holds GPU credentials) is preserved: the agent asks the
registry to submit; the registry holds the credential.

## Reversibility — the stickiness warning, respected

Map II warns orchestrator choice is among the stickiest decisions, and that once
20–30 pipelines sit on one, migration costs quarters.

This decision is deliberately the *least* sticky option available. The lock-in is
to Modal for GPU, which W-06 already forced and which the protocol isolates. The
registry is ~200 lines behind an interface. **If concurrency outgrows a
file-backed store, Prefect goes behind the same interface** — that is the
migration, and it is bounded.

Explicit trigger to revisit: more than ~10 concurrent experiments, or a second
workload lane needing cross-job scheduling.

## Consequences

- Unblocks a build ticket, **N-08 — the JobRegistry and `ComputeSubstrate`
  protocol** (submit/poll/fetch, deadline sweep, breaker, cost accounting).
- Cost caps (W-12) land in the registry, since it is the only component that sees
  every submission. Fail-closed, before the first run.
- MLflow's placement (M2-06) simplifies further: with no Metaflow, there is no
  competing artifact store, and MLflow stays purely observational.
- The `running-unattended` state needs adding to the tracker workflow (N-06).
- **This is a HITL ticket.** The reasoning is recorded so the call can be
  ratified or overturned deliberately, not inherited by default.
