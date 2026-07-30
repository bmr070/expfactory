# Triage labels — role to string

The mapping an agent reads before it touches a label. Roles are stable; the
strings are this repo's and may differ elsewhere.

**Why the indirection exists.** Matt Pocock's triage skill keeps canonical role
names separate from label text so a repo that already says `bug:triage` gets its
existing label applied instead of a near-duplicate created beside it. The failure
it prevents is a tracker with `bug`, `Bug`, `type:bug` and `bug:triage` all
meaning the same thing and none of them queryable. Our `agent-ready` is a live
instance: the role is `ready-for-agent`, the string is `agent-ready`, and the
string is load-bearing — it is written into invariant 7, the runner, and
[`agent-ready-guard.yml`](../../.github/workflows/agent-ready-guard.yml).
Renaming it to match the role would be a rename of the trust boundary.

The machine-readable source is
[`provision/labels.json`](../../provision/labels.json); every entry carries a
`dimension` field, and [`tests/test_provision_intake.py`](../../tests/test_provision_intake.py)
pins that the dimensions are disjoint. This file is the prose half. When they
disagree, the JSON is right — a vocabulary nothing checks is a vocabulary that
drifts.

## Four dimensions. Exactly one label from each.

| Dimension | Role | String here | Notes |
|---|---|---|---|
| **stage** | `wayfinder` | `stage:wayfinder` | A question. Research is this. |
| | `spec` | `stage:spec` | Links to the wayfinder node it synthesises. |
| | `ticket` | `stage:ticket` | The only dispatch-eligible stage. |
| | `review` | `stage:review` | In `/code-review`. |
| **lane** | `empirical` | `lane:empirical` | Gate harness + ledger. Model training and evaluation are this. |
| | `deterministic` | `lane:deterministic` | CI exit code. |
| **category** | `bug` | `bug` | Shipped code does not do what it says. |
| | `enhancement` | `enhancement` | New capability, or an existing one made better. |
| **state** | `needs-triage` | `needs-triage` | Entry point. |
| | `needs-info` | `needs-info` | Waiting on the reporter. |
| | `ready-for-agent` | **`agent-ready`** | The one role whose string differs. |
| | `ready-for-human` | `ready-for-human` | A human decides. |
| | `blocked` | `blocked` | Unmet blocking edge. |
| | `needs-human` | `needs-human` | Breaker tripped, or red lane. |
| | `declined` | `declined` | Killed on evidence. Kept, not deleted. |

**If two labels from one dimension are present, stop and ask.** Do not pick one.
Two states is not a formatting problem, it is two people disagreeing about what
happens next, and resolving it silently picks a winner without telling either.

## The state machine

State is not a tag set. These are the only transitions:

```
        (unlabelled)
             |
             v
       needs-triage <-----------+
        |    |    |    |        |
        |    |    |    +--> needs-info  (reporter answers)
        |    |    |
        |    |    +--> declined      (killed on evidence)
        |    |
        |    +--> blocked            (unmet edge; returns when cleared)
        |
        +--> agent-ready  --> ready-for-human --> closed
                  ^                  |
                  +------------------+
                   (review left findings)
```

`needs-human` is reachable from anywhere: a tripped cost or failure breaker
overrides whatever the ticket thought it was doing.

## What is deliberately not a label

**Hierarchy.** Linear has `project`, `parentId` and `blockedBy`, and BRE-36 uses
all three — the wayfinder map is a project, the spec is an issue, tickets are
children with blocking edges. An `epic` / `story` label pair would be a second
copy of that structure, in a place that can disagree with the first. The
hierarchy is the hierarchy.

**Research.** That is what a `stage:wayfinder` node does.

**Model training, model evaluation.** That is what an empirical `stage:ticket`
does, and `lane:empirical` already says so. A label per activity is how a
vocabulary becomes a tag cloud: nothing composes, nothing has transitions, and
no rule says which are mutually exclusive.

## Pull requests use the same state vocabulary

Read against the attached diff rather than the issue:

| Label | On a PR it means |
|---|---|
| `agent-ready` | A brief is attached; an agent takes the next step on the diff. |
| `ready-for-human` | Gate lane green, branch current. Ready to merge. |
| `blocked` | Waiting on another PR or an external decision. |

### Draft first

**Work in progress is a draft PR, or no PR at all. Never a ready PR that cannot
merge.** Draft is the mechanism GitHub already understands and branch protection
already respects, so this needs no label of its own. Open SWE does exactly this:
it commits and opens a *draft* PR when done.

1. In progress → draft.
2. Gate lane green and the branch up to date → **mark ready**, apply
   `ready-for-human`.
3. Review left findings the author should address → `agent-ready`.
4. **A red `substrate-guard` is not "not ready."** It is the expected state for a
   harness change, and the PR is still `ready-for-human` — the override *is* the
   review. See [`docs/GOTCHAS.md`](../GOTCHAS.md).

The rule exists because a PR that cannot merge is noise on a review queue whose
size is this project's founding constraint ([`docs/MAP.md`](../MAP.md),
`Runner(max_awaiting_human=N)`). It was written after #72, #73 and #75 were
opened stacked, were marked MERGED when their bases were deleted, and delivered
nothing to `main`.
