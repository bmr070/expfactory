# Dispatch readiness — what has to be true before a real agent runs here

The factory's trust model assumes the agent and the harness owner are **different
identities**. Right now they are not: every commit in this repo, agent-authored
or not, is authored as `bmr070`. Until that changes, CODEOWNERS cannot tell an
agent's change from a human's, and the red lane is advisory.

This is the checklist. Items marked **you** need an account action.

---

## Blocking defects · found 2026-07-28 · **none of these are account actions**

The identity gap below was, until this review, the only thing standing between
here and a live run. It is not any more. An external review
([`reference/code-review-insights-2026-07-28.html`](reference/code-review-insights-2026-07-28.html))
reproduced four paths that let an untrusted party bypass a stated control or
leave an expensive run unaccounted for.

Its verdict is worth quoting exactly, because it is not a criticism of the
design: *keep the architecture, do not dispatch a real empirical agent yet.*

| | Defect | Ticket |
|---|---|---|
| **P0** | Non-finite metrics promote. Three runs at `inf` yield `promoted=True`; dominance arithmetic goes `nan` and every comparison is false. | BRE-28 |
| **P0** | `JobRegistry.submit()` accepts `-100.0` and `NaN` cost estimates. A negative value *lowers* the trailing-day total and manufactures budget. | BRE-29 |
| **P1** | Submit-then-record leaves a crash window with a live billable job and no registry record. `reconcile()` cannot find it. No multi-writer guard. | BRE-30 |
| **P1** | G-10 proves a handle exists, not the artifact or ticket it claims. A handle from another ticket is not bound in the production verifier. | BRE-31 |
| **P2** | The detach path cannot reach `Running Unattended` through either production tracker, and both adapters read only the first page. | BRE-32 |

**BRE-32's pagination half was live, not theoretical.** Checked against the real
API on 2026-07-28: `issues(first: 3)` returns `hasNextPage: true` on this
workspace. Invariant 7's check — *which human applied the label* — was therefore
being decided on a truncated history, which is an authorization decision made on
partial data. **Fixed:** both adapters now walk every collection to its end and
raise rather than return a prefix. The table above records what the review found,
not what is still open.

Order matters here. The numerical and cost boundaries (BRE-28, BRE-29) are cheap
and close direct bypasses, so they go first. Do not build a second substrate
adapter on the current submit-then-record shape; fix BRE-30 first or the new
adapter inherits the crash window.

---

## 0. Where the runner runs  ·  **[M2-08](decisions/M2-08-where-does-the-runner-live.md)** · resolved

§1 picks a GitHub identity, and that choice is downstream of *where the runner
executes* — a scheduled GitHub Action supplies `github-actions[bot]` free, a
daemon supplies nothing. This document originally had it the wrong way round.

M2-08 is now resolved (BRE-20), so §1 is unblocked.

The Linear half of §1 is unaffected — it is free and needed either way.

**Correction carried from BRE-21.** That ticket recorded that Open SWE "opens PRs
as the triggering human", and treated it as inherent. Reading
`docs/INSTALLATION.md` §4b showed it is **configuration-dependent and the opt-in
path**: leave `GITHUB_OAUTH_PROVIDER_ID` unset and every PR and commit carries
the GitHub App bot identity, which is the separation §1 wants. The bot-identity
mode is the default.

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
- status checks required and up to date: `check (3.11)`, `check (3.13)`,
  **`substrate-guard`**
- conversation resolution required
- force pushes and deletions blocked

> This list omitted `substrate-guard` until 2026-07-28 and that omission cost
> real confusion. Read against the live API, not the docs:
> `required_status_checks.contexts` is
> `['check (3.11)', 'check (3.13)', 'substrate-guard']` with `strict: true`.

### The guard is a required check that can never pass. That is the design.

Worth stating plainly, because the behaviour looks broken and is not.

`substrate-guard` fails on **any** PR touching `_HARNESS_PATHS`, whether the
change strengthens the harness or weakens it. There is no version of a
verification-layer change that turns it green, because editing the verification
layer is the trigger. BRE-28 strengthened the gate set and the guard failed it,
correctly.

Since it is also a **required** check, a harness PR has no normal merge path at
all. `--admin` is the only route, and that is the intended mechanism rather than
a workaround:

```bash
gh pr merge <N> --squash --delete-branch --admin
```

**Why an unsatisfiable check rather than a required approval.** The override is
tied to *repo admin permission*, which the agent identity will never hold (§1
withholds it explicitly, and §2 says do not grant the App an admin bypass). An
approval-based rule, or a rule satisfied by a magic string in the PR body, can be
met by anything that can write — including the agent. This one cannot. The
strength is precisely that it is not satisfiable by anything the agent can do.

**Measured firing rate**, last 20 merged PRs: 0 of 8 docs/chore PRs, 12 of 12
code PRs. So today it sorts by "is this Python", not by "does this weaken
verification". That is a real observation and it is BRE-34's question, not a
licence to weaken the guard — see that ticket for why the noise is BRE-18's
fault and disappears when BRE-18 lands.

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

`JobRegistry` takes `per_job_cap_usd` and `per_day_cap_usd`. Set both to real
numbers before anything dispatches. W-12 puts these on day one because the
precedents were each retrofitted after a shock.

> **This section used to say the caps "refuse fail-closed". That was not true and
> the claim is withdrawn (BRE-29).** `submit()` tests only `estimate > cap`, so
> `NaN` and negative estimates pass, and a negative estimate lowers the
> trailing-day total and manufactures budget. Worse, a caller-supplied estimate is
> not a cap at all when the untrusted caller picks the number — W-12's own
> conclusion, arrived at again from the other end.
>
> Setting the two numbers is still necessary and is no longer sufficient. The cap
> is real once BRE-29 lands a trusted pricing function keyed by the **substrate's
> rate card** — not by GPU SKU, because compute here is pluggable across edge,
> local GPU and infra.

A doc asserting a safety property the code does not have is worse than silence,
because it is the kind of claim someone later builds on. Recorded rather than
quietly edited.

## 7. Adapters and their real state

Most of this table used to read "not built". Updated against the code rather
than against memory:

| Protocol | State | Note |
|---|---|---|
| `runner.Tracker` (GitHub) | **done** — `github_tracker.GitHubTracker` | BRE-32: walks the `Link` header to the end, `per_page` capped at 100, ascending creation order enforced on arrival. A page that fails mid-walk raises rather than returning the prefix |
| `runner.Tracker` (Linear) | **done** — `linear_tracker.LinearTracker` | the queue the runner actually reads (W-07 amendment). BRE-32: cursor pagination over all four connections — issues, history, labels, workflow states — same all-or-nothing rule |
| workspace isolation | **done** | `Runner(workspaces=...)`; names refused, not sanitized. Filesystem isolation only, not a security boundary |
| runner ↔ registry detach | **done** | `Submitted` → park → `collect_finished` → same `_adjudicate`. BRE-32: both adapters now reach `Running Unattended`, detachment needs **both** registry and collector, and the runner asks `writable_states()` at construction so an unreachable state is a wiring error rather than a refusal after the spend |
| `registry.ComputeSubstrate` | **done** — `local_substrate.LocalGpuSubstrate` | **Not Modal.** C-01 superseded the Modal assumption with the local GPU; edge and infra remain open behind the same seam |
| secret store | **half** | `SecretStore` exists and satisfies SPEC §15.3's scrub MUST, but is not yet the source of tracker credentials — the runner does not construct trackers. Ticket 07's last open box |
| `runner.AgentSession` | **fake only** | the real sandboxed agent; must construct its verifier with `require_prereg=True` (#4), and nothing yet enforces that it does |
| trusted completion record | **not built** | BRE-31. Until it exists, G-10 establishes that a job existed and no more |

The seam was always the point: `ComputeSubstrate` is `submit` / `poll` /
`fetch_artifact` with no hardware in any signature, which is why the local GPU
dropped in without changing anything above it, and why edge or infra will too.

---

## The shortest honest path

1. **Close BRE-28 and BRE-29.** Both P0, both cheap, both close direct bypasses.
   Nothing else on this list is worth doing while a candidate can promote itself
   with an infinity.
2. **Create the free Linear agent** — an OAuth application with `actor=app`.
3. Take the GitHub identity M2-08 implies and put its credential in that host's
   secret store.
4. **Close BRE-30** before writing a second substrate adapter, or the new adapter
   inherits the crash window.
5. Set the two cost caps, and know they are advisory until BRE-29 lands.
6. Close ticket 07's last box — the secret store becoming the actual source of
   tracker credentials.
7. Apply `agent-ready` to exactly one ticket, **by hand**, and watch it.

**On step 2, a trap found the expensive way.** A Linear **personal API key**
(`lin_api_…`) is *not* the agent identity. Checked against the live API on
2026-07-28: it returns `viewer.isMe: true` under the owner's own name and cannot
populate `botActor`. The runner's load-bearing check is `label_actor`, which
distinguishes `actor` from `botActor` structurally — and a personal key is
indistinguishable from a human at a keyboard, so invariant 7 would be satisfied
by a lie. Only an OAuth app with `actor=app` populates `botActor`. A personal key
is fine for reads; it must never become the dispatch credential.

Step 2 is what converts the red lane from advisory to enforced. Nothing should
dispatch before it.

**The detach path is now wired**, unlike when this list was first written:
`AgentSession.run` may return `Submitted`, the ticket parks, and
`collect_finished` brings the artifact back to the same `_adjudicate`. BRE-32
closed the last gap — both production trackers support the parked state, and
the runner refuses to start against one that does not.

A first live run should be a short deterministic ticket, not a training run.
