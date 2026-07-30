---
id: M2-03-RATIFICATION
parent: decisions/M2-03-RESOLVED-experiment-queue.md
labels: [wayfinder:decision]
mode: HITL
status: RATIFIED
ratified: 2026-07-30
ticket: BRE-16
supersedes-noun: Modal (by C-01)
---

# M2-03 ratification — the shape is ratified, the provider never was

## What was actually being asked

BRE-16 says *"ratify M2-03."* The doc's own front matter already says
`RATIFIED — shape ratified, provider unnamed`, so on a first read the ticket is
a no-op.

It is not, and the reason is a contradiction inside the document. The header
disclaims the provider; the verdict names one:

> Adopt the compute substrate's own job primitive (**Modal** `spawn` → handle →
> poll), and build a thin durable `JobRegistry` [...]

C-01 resolved four days later that compute is the owner's local RTX 4070, and
nothing has run on Modal since. So the decision as written would bless a provider
the project does not use, and the header says it does not — one of the two is
wrong, and a reader has no way to tell which without this file.

**Ratified: the shape. Overturned: the noun.**

## The shape, stated without a vendor in it

> **Adopt whatever durable job primitive the compute substrate already has —
> submit returns a handle that outlives the submitting process, and the handle is
> pollable afterwards. Build only the `JobRegistry` that records which ticket is
> waiting on which handle, and owns the failure semantics.**

Every sentence of M2-03's reasoning survives that rewrite, because none of it
depended on which company owned the GPU:

- **Metaflow** still loses on safety. A second store that believes it records
  what happened is ambiguity about which record is the truth, and removing that
  ambiguity is the project.
- **Prefect** still loses as redundant, and is still the named fallback if the
  registry outgrows a file.
- **Celery / RQ** still lose on operational weight.
- **"Nothing"** is still eliminated by M2-07.

What changes is one sentence of the Prefect argument. M2-03 declined Prefect
partly because *"Modal already supplies that"* durable state. On the local
substrate nothing supplies it for free — `LocalGpuSubstrate` implements the
durability itself, in a detached process with an on-disk handle directory. The
decline still holds, but it now rests on scale (≤3 concurrent jobs, one live
workload) rather than on inheriting someone else's queue. **That is a weaker
argument than the one written down**, and it is the reason to keep Prefect named
as the fallback rather than declined outright.

## What was built, and whether it matches

| M2-03 said | What exists |
|---|---|
| substrate's job primitive, `submit → handle → poll` | `ComputeSubstrate.submit/poll/fetch_artifact` in [`registry.py`](../../src/expfactory/registry.py) |
| a handle that outlives the submitting process | `LocalGpuSubstrate` runs detached; the handle is a directory, and `poll` reads it with the submitter gone |
| a thin `JobRegistry` holding *(ticket, handle, deadline, cost)* | `JobRegistry`, plus the reservation protocol BRE-30 required |
| if the queue loses a job, the registry notices | `sweep()` → `LostJob` → breaker trips, ticket to `needs-human`, **no auto-retry** |
| a `running-unattended` ticket state | `STATE_RUNNING_UNATTENDED`, writable by both adapters |

Two corrections to the record while ratifying it, both from things that went
wrong rather than from re-reading:

**"It is small (~200 lines)."** It is not. `registry.py` is several times that,
and the growth is not padding — BRE-30's reservation protocol
(`reserved → bound | released | orphaned → abandoned`, fsynced before the
substrate is touched) and BRE-41's nine fail-open fixes are what it costs to make
"the registry is the only thing that notices" actually true. The estimate was
made before anyone had tried to lose a job on purpose.

**The seam must name no hardware, and BRE-33 found that it did.** The
`ComputeSubstrate` docstring said GPU. Most work entering this factory needs no
accelerator; the accelerator-bound part is `LocalGpuSubstrate`, which sits
*behind* the seam. Fixed there, and pinned by a test here so the vendor cannot
creep back in through a docstring the way the device class did.

## Reversibility

Unchanged from M2-03 and now cheaper than it was, because the seam is real:
swapping to a rented substrate is a constructor argument. `RateCard` lives on the
substrate rather than beside it, so a registry cannot be wired to one provider
and priced by another's rates — which is the specific way a provider swap would
otherwise go wrong quietly.

## What this does not ratify

**BRE-30's box is met; the detach model's is not.** `AgentSession.run` is still
synchronous, so the runner blocks for the length of a job. The sweep can notice a
job nobody waited on, but nothing yet submits one that way. That is the last open
box on ticket 07 and it is not closed by this ratification.
