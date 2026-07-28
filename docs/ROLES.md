# Roles — who is trusted, and who adjudicates

The trust architecture of the factory: which parties exist, what each may
produce, and what each may never do.

For the project handbook — overview, architecture, commands, coding standards,
invariants, gotchas — see [`CLAUDE.md`](../CLAUDE.md).

> This is **not** a persona org-chart of LLM agents to spawn. It documents the
> trust boundaries the code already enforces. Claude Code subagents, if any are
> ever added, are defined in `.claude/agents/*.md` and are a different thing.

The one sentence that generates everything below:

> **You cannot verify a result by asking the thing that produced it what the
> result was.** (invariant 9)

Every role boundary here exists to keep the party that produces a number away
from the party that decides whether it counts.

---

## The roster

| Role | Trusted? | Produces | Never does |
|---|---|---|---|
| **Human owner** (`@bmr070`) | Yes, fully | The `agent-ready` label, merges, ratifications | Nothing is withheld |
| **Runner** (`runner.py`) | Yes | Verdicts, ticket transitions, proof-of-work | Approve its own work |
| **Verifier** (`verifier.py` + gates) | Yes | `promoted`, `blocked_by` | Read anything the agent authored about a verdict |
| **Scorer** (`scorer.py`) | Yes | The metric | Return labels, or run agent code |
| **Compute substrate** (`registry.py`) | Yes | Job handles, artifacts, attestations | Interpret a result |
| **Agent session** (`AgentSession`) | **No** | `Candidate` — evidence only | Decide, score, or hold a credential |
| **Reviewer** | Read-only, fresh context | Findings for a human | Override a blocking gate |

The only untrusted row is the agent session. Everything else in the system exists
to bound what that row can claim.

## When to use each

### Human owner
Applies `agent-ready` — the **only** state a runner will dispatch (invariant 7).
No agent applies it, including the agent that set this repo up. Also: every merge
(branch protection), and ratifying HITL decisions in `docs/decisions/`.

Cannot currently be told apart from the agent at the repo boundary: every commit
here carries the same identity. That is GH#15 / BRE-18, and until it is fixed the
CODEOWNERS red lane is **advisory**.

### Runner
The outer loop and the trust boundary. Polls the tracker, checks eligibility,
prepares a workspace, dispatches one agent, **adjudicates what comes back**, posts
a proof-of-work, and moves the ticket to review.

Its load-bearing check is not "is the label present" but **"who applied it"**.
Presence trusts whoever set it; a compromised agent with write access could label
its own ticket. Asking who does not race with the label-stripping workflow.

### Verifier and gates
The only thing that produces `promoted`. Built by the *runner*, never by the
agent, which is what makes `require_prereg=True`, the `PreregStore` and G-09's
`DatasetGrouping` enforceable rather than requests.

### Agent session
Runs one ticket in a sandbox and returns a `Candidate`: runs, metrics, id hashes,
the diff, the preregistration it cites. Evidence, never a verdict.

It receives a workspace prepared by the runner and an environment scrubbed of
every declared secret. It holds no GPU credential and no tracker credential
(invariant 6). It submits a job and receives an artifact by reference.

It still authors the *evidence*. Closing that requires numbers that come from the
compute substrate rather than the agent — W-06's split, G-10's attestation, and
the remaining half of GH#33.

### Reviewer
Fresh context, read-only (invariant 3). A reviewer sharing the implementer's
context rubber-stamps its own reasoning. **Runs after L0 gates, never before**
(invariant 2): reversing the order lets a fake result reach a human wearing a
persuasive endorsement, which is worse than no review because the narrative
launders it.

**A reviewer may never override a blocking gate.**

## Coordination

```
human ──applies agent-ready──▶ tracker (Linear)
                                  │
                                  ▼
                              runner ──prepares──▶ workspace
                                  │
                                  ├──dispatches──▶ agent session  (untrusted)
                                  │                    │
                                  │                    └──submits──▶ compute substrate
                                  │                                       │
                                  │◀────────── Candidate (evidence) ──────┘
                                  │
                                  ├──adjudicates──▶ verifier + gates ──▶ ledger
                                  │
                                  └──posts verdict──▶ tracker ──▶ In Review
                                                                     │
                                                          reviewer ──┘  (fresh context)
                                                                     │
                                                                  human decides
```

Three properties of that diagram are the whole design:

1. **The agent never touches the ledger.** It is append-only and written by
   trusted code only.
2. **The arrow back from the agent carries evidence, not a verdict.** It used to
   carry a verdict, and no amount of checking that verdict could work.
3. **Nothing reaches Done without a human.** The runner moves tickets to review
   and stops.

## Concurrency

Two limits, both on the runner, both bounding the *human*:

- `max_concurrent` — agent sessions in flight.
- `max_awaiting_human` — tickets sitting in `In Review` **or** `Needs Human`.
  Dispatch stalls when that queue is full.

MAP.md's founding constraint: *throughput ceiling is human review bandwidth; any
design that raises agent concurrency without raising review capacity is rejected
by default.* The second limit is that rule as a number.

Counting `Needs Human` alongside `In Review` gives it a second job for free: a run
of correlated failures fills the queue and stalls dispatch, which is the circuit
breaker a naive retry loop lacks.

## Only three roles earn isolation

**Planner, reviewer, triage.** Everything else is one coding agent with tools.
Persona org-charts are declined on evidence; see [`MAP.md`](MAP.md). Independent
confirmation: Open SWE isolates exactly one role, the reviewer, as a separate
graph, and did not build the org chart.

**A model is not a trust boundary.** Factory's droid picks a `--validator-model`
that runs in the same process with the same tools as its workers. Choosing a
different model is not moving the decision somewhere the producer cannot reach.
Roles here are separated by *what they can touch*.

## Before dispatching a real agent

See [`DISPATCH-READINESS.md`](DISPATCH-READINESS.md). Short version: the red lane
is advisory until there is a second identity (GH#15), so an agent dispatched today
can edit the verification substrate and have it merged by the account that wrote
it. `substrate_guard` still blocks the PR, because it asks what changed rather
than who.
