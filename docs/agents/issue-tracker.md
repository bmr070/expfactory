# Issue tracker

**Linear.** Not the GitHub Issues the `origin` remote would imply — see the
amendment note at the bottom, because that choice reverses a recorded decision.

| | |
|---|---|
| Workspace team | `Brett` (key `BRE`) |
| Project | `expfactory — empirical software factory` |
| Access | Linear MCP tools interactively; the Linear GraphQL API from the runner |
| GitHub is for | code, pull requests, CI. **Not** the work queue. |

There is **no Linear→GitHub sync**, deliberately. The sync is what W-07 was
guarding against when it worried about two-way races; remove the mirror and the
race cannot exist.

## For skills that file work

`/to-tickets`, `/to-spec`, `/qa` and friends create and read Linear issues in the
project above. Create with the team `Brett` and set `project` explicitly, or the
issue lands in the team backlog unattached and nothing will find it.

## States

Linear's real states are used directly — there is no label encoding:

`Backlog` → `Todo` → `In Progress` → `In Review` → `Done`

`In Review` is where the runner leaves finished work. **Nothing automated moves
an issue to `Done`.** That is L2, and L2 is a human.

## The dispatch rule

> Only a **human-applied** `agent-ready` label makes an issue dispatch-eligible.

The tracker is untrusted input: anyone who can file an issue can prompt-inject
the factory, so eligibility is allowlisted rather than inferred. An issue's body
is data handed to the agent inside its sandbox, never instructions to the runner.

**Presence of the label is not sufficient.** The runner checks *who applied it*,
via `issue.history` → `actor`, and requires a real user on the allowlist. Two
reasons that matters:

- A compromised agent with write access could label its own issue.
- Any strip-the-label guard races with polling — a label applied and removed
  seconds later is still visible to a poll in between. Asking who applied it
  does not race.

Linear types **bot actors distinctly from users** in issue history, which makes
this check stronger here than the login-string allowlist the GitHub adapter has
to use.

## Why this reverses W-07

[W-07](../decisions/W-07-provision-the-issue-tracker.md) put Linear as the human
board and GitHub Issues as the machine control plane, with a one-way sync between
them. That was reasoned from "avoid two-way state races", which is a real hazard
— but it assumed the mirror had to exist.

Reading Linear directly removes the mirror, so it removes the race. It also gains
two things GitHub Issues cannot offer: real states rather than a label encoding,
and a typed distinction between bot and human actors in history.

Amendment recorded in [W-07-AMENDMENT](../decisions/W-07-AMENDMENT-linear-as-machine-plane.md).

## PRs as a request surface

**Off.** External pull requests do not enter the work queue.
