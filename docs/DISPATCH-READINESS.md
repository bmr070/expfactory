# Dispatch readiness — what has to be true before a real agent runs here

The factory's trust model assumes the agent and the harness owner are **different
identities**. Right now they are not: every commit in this repo, agent-authored
or not, is authored as `bmr070`. Until that changes, CODEOWNERS cannot tell an
agent's change from a human's, and the red lane is advisory.

This is the checklist. Items marked **you** need an account action.

## 0. Decide where the runner runs first  ·  **[M2-08](decisions/M2-08-where-does-the-runner-live.md)**

§1 below picks a GitHub identity. That choice is downstream of *where the runner
executes*, and this document had it the wrong way round: a scheduled GitHub
Action supplies `github-actions[bot]` free, a daemon supplies nothing. Resolve
M2-08 before acting on §1.

The Linear half of §1 is unaffected — it is free and needed either way.

## 1. A separate machine identity  ·  **you**

Pick one:

| | GitHub App *(recommended)* | Bot account (`brett-ancile-ai`) |
|---|---|---|
| Seat cost | none | consumes a collaborator slot |
| Credential | short-lived installation token | long-lived PAT |
| Scope | per-repo, per-permission | whatever the PAT carries |
| Commit author | `expfactory-agent[bot]` | `brett-ancile-ai` |
| Revocation | uninstall the App | rotate the PAT everywhere |

The App is the better shape for the same reason Open SWE's dummy-token proxy is
(M2-07): the agent operates with a credential that is not the owner's, and it can
be scoped narrowly and revoked in one action.

**Permissions to grant — and the two to withhold:**

```
contents:        write   # push branches
pull_requests:   write   # open PRs
issues:          read    # read tickets
metadata:        read

administration:  NONE    # or it can disable its own branch protection
issues:          NOT write
```

`administration: none` is the load-bearing one. An agent that can edit branch
protection can unlock its own cage, and every other control here becomes
decorative.

`issues: write` is withheld so the agent cannot apply `agent-ready` to anything —
including a ticket it filed itself. The workflow in §4 is the backstop for when
this is wrong.

## 2. Branch protection  ·  **done**

The repo is public, which makes protection available, and it is set on `main`:

- pull request required before merging
- review from Code Owners required (1 approval)
- status checks required and up to date: `check (3.11)`, `check (3.13)`
- conversation resolution required
- force pushes and deletions blocked

**`enforce_admins` is deliberately off**, and this is the thing to understand
about the current state. GitHub will not let anyone approve their own PR, so with
admin enforcement on, a solo owner could never merge — the repo would deadlock on
its first red-lane change.

What that means in practice, measured rather than assumed: the normal merge path
is blocked for the owner too. `gh pr merge` refuses with *"the base branch policy
prohibits the merge"*. An admin can override, but only by asking explicitly with
`--admin`. So the owner's bypass is a **deliberate act, not a silent default** —
which is most of what a control like this is for.

A GitHub App is not an admin and has no such escape hatch: it opens a PR, CI must
pass, and a code owner must approve anything touching the verification substrate.
Do **not** grant the App an admin bypass, or this reverts to decorative.

Every `--admin` merge is recorded in the PR timeline, so the override is auditable
after the fact even though it is available.

## 3. CODEOWNERS stays owner-only  ·  done

`.github/CODEOWNERS` names `@bmr070` for the red lane — the verification
substrate, CI, and tool config. **The agent identity must never be added here.**
That is what makes "the agent may never self-certify" enforceable rather than
aspirational.

The protected set is kept in sync with `_HARNESS_PATHS` in
`src/expfactory/gates_v1.py` by a test, and a second test asserts every module in
the package is either protected or explicitly exempt — so a new module fails CI
until someone classifies it.

## 4. The `agent-ready` guard  ·  done

`.github/workflows/agent-ready-guard.yml` strips the label from anyone not on the
human allowlist and explains why on the ticket.

A backstop, not the primary control — the primary control is §1 withholding
`issues: write`. Both exist because the failure being prevented is the factory
acting on instructions it was tricked into receiving, and that deserves two
independent stops.

Note it races with polling: a label applied and removed seconds later can still
be seen by a poll in between. That is why `Runner.eligibility` separately checks
*who applied* the label rather than trusting its presence — that check does not
race.

Update the allowlist in the workflow if a second human ever gets the right.

## 5. Credentials live in the runner  ·  **you**

Tracker, model and GPU credentials go in the runner's secret store, never in an
agent workspace. The agent requests an action; a gated step performs it
(invariant 6, W-06).

For the GPU substrate specifically, `JobRegistry` holds the credential — the
agent asks it to submit and receives an artifact reference back.

## 6. Caps set before the first run  ·  **you**

`JobRegistry` takes `per_job_cap_usd` and `per_day_cap_usd` and refuses
fail-closed. Set both to real numbers before anything dispatches. W-12 puts these
on day one because the precedents were each retrofitted after a shock.

## 7. Adapters that do not exist yet

Three protocols ship with fakes and no production implementation. Each needs
writing before a live run, and each was left deliberately — getting the seam
right mattered more than reaching I/O:

| Protocol | Needs | Note |
|---|---|---|
| ~~`runner.Tracker`~~ | **done** — `github_tracker.GitHubTracker` | needs an `HttpTransport` carrying the App token |
| workspace isolation | **not built** | ticket 07 box 1; the runner trusts the AgentSession |
| secret store | **not built** | ticket 07 box 4; the token rides on the transport today |
| runner ↔ registry | **not wired** | the detach path M2-03 designed does not connect yet |
| `runner.AgentSession` | sandboxed agent | must construct its verifier with `require_prereg=True` (#4) |
| `registry.ComputeSubstrate` | Modal adapter | `spawn` → durable handle → poll |

---

## The shortest honest path

1. Create the free Linear agent (`actor=app`) — needed under every option
2. Resolve M2-08 (where the runner runs), then take the GitHub identity it implies
   and put its credential in that host's secret store
3. Set the two cost caps
4. Write the three adapters in §7
5. Apply `agent-ready` to exactly one ticket, by hand, and watch it

Step 1 is what converts the red lane from advisory to enforced. Nothing should
dispatch before it.

**And note what step 4 really covers.** The runner today calls an `AgentSession`
synchronously and waits for a verdict. That is fine for a minutes-long job and
wrong for the hours-long GPU path M2-03 was decided for — the registry exists to
be submitted to and detached from, and nothing wires the two together yet. A
first live run should be a short deterministic ticket, not a training run.
