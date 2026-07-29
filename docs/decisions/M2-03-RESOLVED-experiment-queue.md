---
id: M2-03
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
status: RATIFIED — shape ratified, provider unnamed, one box unmet (BRE-30)
resolved: 2026-07-25
ratified: 2026-07-29
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

---

# Ratification, 2026-07-29 (BRE-16)

**Ratified — the shape, not the noun.** Three things have changed since this was
written, and one of them means the decision is ratified while the *implementation*
is recorded as not yet meeting it.

## 1. Modal is superseded. The protocol absorbed it, which is the evidence.

The verdict above says "Modal `spawn` → handle → poll" and "Modal as the first
implementation." C-01 replaced that with the local GPU before any Modal adapter
was written, and `local_substrate.LocalGpuSubstrate` is the first real
`ComputeSubstrate` instead.

**Nothing above the seam changed.** That is not a lucky escape, it is the
"Reversibility" section being right on its first real test: the lock-in was to a
provider the protocol isolates, and swapping the provider cost nothing. Ratify
`submit` / `poll` / `fetch_artifact` and the thin registry. Do not ratify Modal —
it is now one candidate among edge, local GPU and infra compute (TBA), and no
part of the design names it.

## 2. A durable queue does exist upstream. It is declined on cost, not absence.

This decision assumed nothing off-the-shelf owned durable long-job state.
Symphony §14.3 confirmed that for Symphony. It is no longer true of the field:
HumanLayer's Agent Control Plane checkpoints to etcd and makes
`handleCheckApproval` → `handleWaitForApproval` → `handleExecute` separate
reconciler phases, with the awaiting state durable **before** the executor acts.

That is exactly the protocol this decision describes, already built.

It is still declined, and the reason has to change from *"it does not exist"* to
*"it is Kubernetes and etcd, and we have one machine."* An honest decline names
the price. See `docs/research/agent-factories-2026.md`.

**What to take from it:** the phase made durable before the side effect. That is
the same reservation protocol BRE-30 asks for, arrived at from two directions.

## 3. The GPU is one slice of one lane, so the registry must stay hardware-neutral

Written when the empirical lane and the GPU were treated as the same thing. They
are not: most work entering the factory needs no GPU, and compute is pluggable
across edge, local GPU and infra.

`ComputeSubstrate` was already neutral — no signature names hardware — and
BRE-29 extended it with `rate_card()` so the substrate prices its own work. The
registry holds caps and reserves; it never learns what silicon it is buying.
Anything that would put a device class into the registry contradicts this
ratification.

## What is ratified, precisely

- No general-purpose orchestrator. **Unchanged and reinforced** — M2-02 reached
  the same conclusion independently, and nothing since has produced a candidate.
- Adopt the substrate's own job primitive behind `ComputeSubstrate`. **Ratified**,
  with the provider unnamed.
- A thin `JobRegistry` owning caps, breaker, sweep and failure semantics.
  **Ratified in design.** See the box below.
- Metaflow declined on the second-authoritative-store ground. **Ratified**, and
  it has aged well: the argument was ambiguity about which record is the truth,
  and that is the same argument BRE-31 makes about a candidate-authored
  attestation.

## The unmet box, stated rather than inherited

This decision's own list of what must be owned includes:

> 5. Durable restart state
> 10. **If the queue loses a job, someone must notice**

**Neither holds today, and ratifying without saying so would be exactly the green
summary this repo refuses.**

`JobRegistry.submit()` calls the substrate first and appends its `submitted`
event afterwards. A process failure in that gap leaves a live, billable job with
**no registry record**. `reconcile()` cannot find it, because it polls records
already in the log — so the one case box 10 exists to catch is the one case it
cannot see. There is also no multi-writer guard, so two runner processes can
both admit work against the same daily budget.

Filed as **BRE-30**. Until it lands, "durable" describes the design and not the
code, and the registry should be treated as a bookkeeper that is correct only if
nothing crashes at the wrong instant.

## Revisit trigger, updated

The original trigger stands: more than ~10 concurrent experiments, or a second
workload lane needing cross-job scheduling.

Add one: **if compute moves off this machine to infra (TBA), re-read ACP before
building a scheduler.** The reason to decline it is the deployment cost of
Kubernetes on a single desktop, and that reason expires the moment there is a
cluster.
