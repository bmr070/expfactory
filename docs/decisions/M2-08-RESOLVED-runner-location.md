---
id: M2-08
parent: wayfinder:map2
labels: [wayfinder:decision]
mode: HITL
status: RESOLVED
raised: 2026-07-26
resolved: 2026-07-27
supersedes: M2-08-where-does-the-runner-live.md
---

# M2-08 RESOLVED — Where the runner lives, and what that costs in identity

## What changed since this was raised

M2-08 was written a day before `LocalGpuSubstrate` existed. That build settles
the question, and not in the direction the original options table implied.

> **The runner must be able to reach the compute substrate. The compute
> substrate is a GPU under the owner's desk.**

A scheduled GitHub Action (option A) cannot submit a job to that card without
exposing it to the internet, which is a strictly worse security posture than the
thing it was meant to simplify. Option C has the same problem for the same
reason.

So the option table was answering the wrong question. Runner location does not
get chosen on identity, scheduling and secrets — it is **downstream of where
compute lives**, and compute was decided by C-01.

## Decision

**Option B — a daemon on the owner's machine — for as long as compute is local.**

Not because B is attractive. It is the weakest option on every axis the original
ticket compared: the machine has to be on, the environment is not reproducible,
and long-lived credentials sit on a personal laptop. It wins because the other
options cannot reach the GPU.

**This is explicitly a coupling, not a preference.** Written down so that moving
compute to Modal automatically reopens A and C rather than leaving a daemon in
place out of habit:

> Runner location follows compute location. When compute is rented, the runner
> may be rented. While compute is local, the runner is local.

### Poll, not webhook, initially

Option D (Linear webhooks, no poller) is attractive and is deferred rather than
rejected. A webhook receiver on a home machine needs an inbound path from the
internet — a tunnel or an open port — which is the same exposure that ruled out
A and C. A poll needs only outbound.

Revisit when the runner is not on a home network.

## What this costs, and one thing it saves

Option B supplies **no free identity**, which is the cost M2-08 warned about.
But the picture is better than it was, because of BRE-21.

### BRE-21 is already mitigated, by accident of good design

Open SWE opens PRs **"as the triggering user"**, which M2-08 flagged as
defeating the CODEOWNERS separation the identity work exists for.

That concern was correct when raised and is now largely spent. `substrate_guard`
was built for an unrelated reason — an approval-based rule can be defeated by
getting authorship wrong — and asks **what changed, never who changed it**. It
therefore does not care whether an agent PR is authored as the owner.

So impersonation degrades CODEOWNERS, which was always the weaker of the two
layers, and does not touch the wall. The red lane survives an identity we cannot
control.

### Which makes the *free* identity the load-bearing one

BRE-18 named two identities and treated the GitHub App as the important one.
That is now backwards:

| identity | gates | cost | still critical? |
| -- | -- | -- | -- |
| **Linear agent** | `label_actor` — *who* made a ticket dispatch-eligible | free, no seat | **yes** |
| GitHub App | PR authorship, which `substrate_guard` no longer relies on | setup + maintenance | much less |

The runner's load-bearing check is "which human applied `agent-ready`". That
runs against Linear, and Linear types bot actors distinctly in `issue.history`
— stronger than GitHub's login-string matching. The free one is the one that
matters.

**Net effect: BRE-18 gets cheaper.** Create the Linear agent, which costs
nothing; treat the GitHub App as a later nicety rather than a blocker.

## Consequences

- The runner is a local daemon. It needs supervision (a service, not a terminal),
  and that is a provisioning task rather than a design one.
- Credentials live in a local store on the owner's machine. Invariant 6 still
  holds — the *agent* never holds them; the runner does, and the runner is not
  the agent.
- Nothing about the seams changes. `Tracker`, `ComputeSubstrate` and
  `AgentSession` are unaffected by where the loop runs, which is the point of
  having had them.

## Not decided here

- **Supervision.** systemd, a Windows service, or Task Scheduler. Mechanical.
- **What happens while the machine is asleep.** A poll that does not run is a
  ticket that waits. Acceptable for a solo factory and not forever.
- **Whether to keep the GitHub App at all.** If `substrate_guard` is genuinely
  sufficient, the App may never be worth its maintenance. Revisit once a real
  agent has opened a few PRs.
