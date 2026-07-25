# TRACKING — where work lives

Per decision [W-07](decisions/W-07-provision-the-issue-tracker.md).

| Layer | System | Role |
|---|---|---|
| Human board | **Linear** — [expfactory project](https://linear.app/biosun/project/expfactory-empirical-software-factory-91e5c6bd1b5f) | Objectives, decisions, judgement calls |
| Machine control plane | **GitHub Issues** — [bmr070/expfactory](https://github.com/bmr070/expfactory/issues) | What runners read. **GitHub only.** |
| Code, PRs, CI | **GitHub** | `main` protected; CI green required |

**Sync is one-way: Linear → GitHub Issues.** Runners never read Linear. Two-way
sync introduces races between a human editing the board and a runner editing
state, and the resulting ambiguity is not worth the convenience.

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

| GitHub | Linear | What |
|---|---|---|
| [#1](https://github.com/bmr070/expfactory/issues/1) | BRE-11 | Experiment queue (M2-03) — **load-bearing open decision** |
| [#2](https://github.com/bmr070/expfactory/issues/2) | BRE-12 | Finish provisioning — branch protection, Linear→Issues sync |
| [#3](https://github.com/bmr070/expfactory/issues/3) | BRE-13 | Timeout / handoff test (M2-01) — confirmatory |
| [#4](https://github.com/bmr070/expfactory/issues/4) | BRE-14 | Enforce `require_prereg=True` in the runner |
| [#5](https://github.com/bmr070/expfactory/issues/5) | BRE-14 | Re-calibrate the G-08 churn threshold |
| [#6](https://github.com/bmr070/expfactory/issues/6) | — | `examples/demo_drone.py` miscalibrated scenario |
| [#7](https://github.com/bmr070/expfactory/issues/7) | BRE-14 | Egress policy vs dataset downloads |

Build slices and their rationale stay in [`tickets/NEXT.md`](tickets/NEXT.md);
the trackers hold status, the repo holds reasoning.
