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

## Labels

| Label | Meaning |
|---|---|
| `agent-ready` | Human-tagged. The ONLY state a runner will dispatch. |
| `lane:empirical` | Verified by the gate harness + ledger. |
| `lane:deterministic` | Verified by CI exit code. |
| `blocked` | Has an unmet blocking edge. |
| `needs-human` | Cost/failure breaker tripped, or red-lane path. Runner will not touch. |

## Current mapping

Work is being consolidated into Linear per the amendment. GitHub issues that
remain open are code-level findings that pair with a PR; new work is filed in
Linear only.


Build slices and their rationale stay in [`tickets/NEXT.md`](tickets/NEXT.md);
the trackers hold status, the repo holds reasoning.
