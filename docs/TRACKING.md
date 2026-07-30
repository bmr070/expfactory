# TRACKING — where work lives

Per decision [W-07](decisions/W-07-provision-the-issue-tracker.md).

| Layer | System | Role |
|---|---|---|
| Work queue | **Linear** (private workspace) | Everything agents pull from, and everything humans decide |
| Code, PRs, CI | **GitHub** — [bmr070/expfactory](https://github.com/bmr070/expfactory) | `main` protected; CI green required. **Not a work queue.** |

**There is no sync.** An earlier version of this document had Linear syncing
one-way into GitHub Issues, with runners reading GitHub only, to avoid two-way
state races. Removing the mirror removes the race outright — see
[W-07-AMENDMENT](decisions/W-07-AMENDMENT-linear-as-machine-plane.md).

## The dispatch rule

> **Only a human-applied `agent-ready` label is dispatch-eligible.**

The tracker is untrusted input. Anyone who can file a ticket can prompt-inject
the factory, so dispatch is allowlisted rather than inferred. No ticket
self-promotes, and no agent applies that label — including the agent that set
this repo up. The label exists; nothing carries it.

**And it must be applied to something implementable** (BRE-36). `agent-ready` on
a `stage:wayfinder` question or a `stage:spec` design is a configuration error:
an agent pointed at either will build something plausible, which is the failure
the pipeline order exists to prevent.

Both halves are enforced by
[`agent-ready-guard.yml`](../.github/workflows/agent-ready-guard.yml) as two
separate steps, so the timeline shows which refusal fired. Prose does not
ratchet (invariant 8), so neither rule lives only here.

### The allowlist holds account ids, not names (BRE-42)

`Runner(human_allowlist=...)` is compared against whatever `Tracker.label_actor`
returns, and what that is differs by adapter on purpose:

| Adapter | Returns | Why |
|---|---|---|
| `linear_tracker` | Linear **`User.id`** (a UUID) | `displayName` and `name` are self-editable by any workspace member, so a name-based allowlist could be joined from a settings page |
| `github_tracker` | the account **login** | GitHub logins are namespace-unique and a rename frees the old one; there is no second identifier the timeline exposes |

Get the ids for a Linear team with:

```bash
curl -s -H "Authorization: $LINEAR_API_KEY" -H 'Content-Type: application/json' \
  -d '{"query":"{ users(first: 50) { nodes { id name displayName email } } }"}' \
  https://api.linear.app/graphql
```

A stale name-based allowlist fails **closed**: the id will not match, the ticket
is refused, and the reason names the id that was not recognised. That is the
intended failure — a dispatch check that degrades to "allow" on a config drift is
not a check.

## The intake chain

A project enters as a ticket, and no implementation ticket exists until the chain
above it exists and is linked:

```
wayfinder map  →  spec  →  tickets  →  implement  →  code-review
 (Linear project) (issue)  (issues)
```

This is **preregistration applied to engineering work.** G-07 refuses a run whose
preregistration is not at a strictly earlier ledger position, because a metric
chosen after seeing the data is a metric that already moved. The intake chain
refuses a ticket whose spec does not precede it, because a justification written
after the code always fits the code. The *ordering* is the control in both, and
in both it is checkable rather than a matter of discipline.

Linkage uses Linear natives rather than a text convention, so the check is a
query: the map is a **project**, the spec `relatedTo` its wayfinder node, and
tickets carry the spec as `parentId` with `blockedBy` edges.

Standing up a new project: [`provision/new-project/`](../provision/new-project/).

## Labels

Source of truth is [`provision/labels.json`](../provision/labels.json), which
carries a `dimension` on every entry; this table is the reading copy. The role →
string mapping an agent reads is
[`docs/agents/triage-labels.md`](agents/triage-labels.md).

> **Four dimensions. Exactly one label from each. (BRE-43)**
>
> stage, lane, category, state. A ticket carrying two states is not a formatting
> problem — it is two people disagreeing about what happens next. **Stop and ask;
> do not pick one.** Disjointness is pinned by
> [`tests/test_provision_intake.py`](../tests/test_provision_intake.py), because a
> vocabulary nothing checks is a vocabulary that drifts.

**Stage** — position in the pipeline. Research is `stage:wayfinder`, not a label
of its own.

| Label | Meaning |
|---|---|
| `stage:wayfinder` | A question, not a task. Closes when the answer is recorded. |
| `stage:spec` | A published specification. Links back to the node it synthesises. |
| `stage:ticket` | Atomic tracer bullet. **The only dispatch-eligible stage.** |
| `stage:review` | In `/code-review`. Clean-context sign-off pending. |

**Lane** — which verifier owns the outcome. Never defaulted; a missing lane is
how the two lanes get conflated.

| Label | Meaning |
|---|---|
| `lane:empirical` | Verified by the gate harness + ledger. **Model training and evaluation are this**, not labels of their own. |
| `lane:deterministic` | Verified by CI exit code. |

**Category** — what kind of work. Added in BRE-43; the review findings this week
were all bugs in shipped code while the BRE-2x series were mostly enhancements,
and nothing distinguished them.

| Label | Meaning |
|---|---|
| `bug` | Shipped code does not do what it says. |
| `enhancement` | New capability, or an existing one made better. |

**State** — a state machine, not a tag set. Transitions are in
[`triage-labels.md`](agents/triage-labels.md).

| Label | Meaning |
|---|---|
| `needs-triage` | Entry point. Leaves when it has a category and a lane. |
| `needs-info` | Waiting on the reporter. Returns to `needs-triage` when they answer. |
| `agent-ready` | Human-tagged, on a `stage:ticket` only. The ONLY state a runner will dispatch. |
| `ready-for-human` | Gate lane green, branch current. A human decides. |
| `blocked` | Has an unmet blocking edge. |
| `needs-human` | Cost/failure breaker tripped, or red-lane path. Runner will not touch. |
| `declined` | Killed on evidence at the wayfinder stage. Kept, not deleted — a closed option is a result. |

### Hierarchy is not a label

Linear has `project`, `parentId` and `blockedBy`, and BRE-36 uses all three: the
wayfinder map is a project, the spec is an issue, tickets are children with
blocking edges. An `epic` / `story` pair would put that structure in a second
place that can disagree with the first.

## Pull requests: draft first (BRE-43)

**Work in progress is a draft PR, or no PR at all. Never a ready PR that cannot
merge.** Draft is a field GitHub already understands and branch protection
already respects, so the not-ready state needs no label. Open SWE does the same
thing: it commits and opens a *draft* PR when done.

1. In progress → **draft**.
2. Gate lane green and the branch up to date → **mark ready**, apply
   `ready-for-human`.
3. Review left findings the author should address → `agent-ready`, meaning an
   agent takes the next step on the diff.
4. **A red `substrate-guard` is not "not ready."** It is the expected state for a
   harness change and the PR is still `ready-for-human` — the override *is* the
   review.

A PR that cannot merge is noise on a review queue whose size is this project's
founding constraint ([`MAP.md`](MAP.md), `Runner(max_awaiting_human=N)`). The
rule was written after #72, #73 and #75 were opened stacked, were marked MERGED
when their bases were deleted, and delivered nothing to `main`.

## Current mapping

Work is being consolidated into Linear per the amendment. GitHub issues that
remain open are code-level findings that pair with a PR; new work is filed in
Linear only.


Build slices and their rationale stay in [`tickets/NEXT.md`](tickets/NEXT.md);
the trackers hold status, the repo holds reasoning.
