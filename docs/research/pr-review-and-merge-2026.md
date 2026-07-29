# How software factories actually handle PR review and merge

Read 2026-07-28. The question is narrow and operational: **when an agent opens a
pull request, who reviews it, and what is allowed to merge it?** Not "do they
have code review" but "where exactly does the merge authority sit, and is an LLM
ever holding it."

Asked because BRE-35 proposed a review fleet with LLM judges deciding whether a
human is needed. That design should be checked against what the field does
before it is built, not after.

Sources read as code and normative text:

| System | What was read |
|---|---|
| [github/gh-aw](https://github.com/github/gh-aw) | `docs/adr/27193-gated-merge-pull-request-safe-output.md` in full, `pkg/workflow/merge_pull_request.go`, `specs/compiler-threat-detection-spec.md`, safe-outputs reference and glossary |
| [Factory-AI/droid-action](https://github.com/Factory-AI/droid-action) | `README.md`, permission blocks |
| [langchain-ai/open-swe](https://github.com/langchain-ai/open-swe) | `README.md`, PR and feedback-loop behaviour |
| [openai/symphony](https://github.com/openai/symphony) | §11.5, already read in the earlier survey |
| Tessl | context lifecycle and evaluation docs, already read |

---

## The headline

**Nobody lets a language model decide whether a pull request may merge.**

Every system surveyed either declines to merge at all, or gates the merge behind
a deterministic policy evaluation that runs *after* the agent has finished and
*outside* the agent's reach. The agent's review output is advisory in all of
them, and in the one system that ships a merge capability it is the handler, not
the model, that decides.

That is invariant 9 restated by the field: **the untrusted party returns
evidence, never a verdict.** BRE-35's "if the judge determines it needs human
review, flag it, else merge" has no upstream precedent, and the reason is
consistent across four independent implementations.

---

## gh-aw — the only real merge implementation, and it is fully deterministic

`merge-pull-request` exists, is marked **experimental**, and emits a warning at
compile time. [ADR-27193](https://github.com/github/gh-aw/blob/main/docs/adr/27193-gated-merge-pull-request-safe-output.md)
(2026-04-19) is the design record and it is worth reading in full.

The agent does not merge. The agent emits a *request* to merge, and a separate
runtime handler evaluates **ten sequential gates**, aborting on the first
failure. Verbatim from the normative section:

> a. Draft state — the PR **MUST NOT** be a draft.
> b. Mergeability — the PR **MUST** be in a mergeable state.
> c. CI checks — all required check runs **MUST** be passing.
> d. Review decision — **MUST NOT** be `CHANGES_REQUESTED` or `REVIEW_REQUIRED`.
> e. Unresolved review threads — **MUST** be zero.
> f. Required labels — every label in `required-labels` **MUST** be present.
> g. Allowed labels — at least one PR label **MUST** match.
> h. Allowed branches — the source branch **MUST** match a configured glob.
> i. Default-branch protection — the PR target branch **MUST NOT** be the
>    repository default branch.

Not one of those is a judgement call. Every gate is a property of the PR that
code can evaluate, and the model contributes nothing to the decision.

### Gate (i) is the finding that matters most here

**An agent may never merge into the default branch. Not configurably — the gate
is unconditional.**

Every PR in this repository targets `main`, which is the default branch. So
under gh-aw's own rules, **zero expfactory PRs would be agent-mergeable**, and
that is before any of our substrate concerns enter the picture. The most mature
safe-output implementation in the field draws the line in a place that excludes
our entire use case.

That is not a reason to abandon BRE-35. It is a reason to stop describing its
goal as "the agent merges" and start describing it as "the agent merges *what
policy permits*, and policy currently permits nothing into `main`."

### The alternatives they rejected, and why it maps onto us

**Alternative 2 was "thin merge wrapper with no policy gates, rely on GitHub
branch protection."** Rejected, and the reason is one we should adopt verbatim:

> This reduces code but shifts policy configuration to GitHub UI settings, making
> it invisible to code reviewers and hard to version-control. Policy gates
> expressed in workflow frontmatter are auditable, diffable, and scoped to the
> specific workflow rather than globally to the repo.

Our merge policy currently lives entirely in GitHub branch-protection settings,
which is precisely the state they rejected. `DISPATCH-READINESS.md` §2 described
those settings from memory and **got them wrong** — it omitted `substrate-guard`
from the required checks — which is exactly the failure mode "invisible to code
reviewers" predicts. That mistake would have been impossible against a
version-controlled policy file.

### Other mechanisms worth taking

- **`staged: true`** performs dry-run gate evaluation without calling the merge
  API. That is shadow mode, already shipped, and it is how the judge's agreement
  gets measured before it is trusted with anything.
- **Idempotency is a MUST**: if the PR is already merged, return success. Re-runs
  are safe by construction.
- **`required-labels`** is the eligibility grant, and it is invariant 7's shape:
  a human applies `automerge`, and the label is what makes the PR eligible.
- **CTR-015**, a compile-time threat rule, rejects a bare `*` in
  `allowed-labels` because it "renders the label restriction ineffective." A
  wildcard in an allowlist is treated as a *compile error*, not a lint warning.
  That is the ratchet, upstream.
- **Compile-time permission enforcement**: a workflow using merge MUST declare
  `contents: write` and `pull-requests: write`, checked when the workflow is
  compiled rather than when it runs.

### And on review, separately

`submit-pull-request-review` exists but defaults to `allowed-events: [COMMENT]`.
The agent comments; approving is not the default posture. There is no
review-dismissal output at all.

---

## Factory / droid-action — review yes, authority no

The published action does three things on a PR: code review with inline
comments, a STRIDE-based security review, and PR description enhancement.

It does **not** merge and does **not** approve. Its own framing is advisory:
findings are posted as inline comments and a summary, and a human decides.

Worth noting for BRE-35's routing question: these are three *distinct lenses*
shipped as separate behaviours rather than one general "review this" prompt.
That is the multi-lens design, in production.

---

## Open SWE — draft PRs, and a feedback loop instead of authority

Open SWE "commits changes and opens a **draft** PR when done, linked back to
your ticket." Draft is the default state, which is a soft version of the same
boundary: the PR arrives not-ready-to-merge and a human promotes it.

Two things to take:

- **No auto-merge.** Its own comparison table lists auto-merge as something
  *another* system (Coinbase's) has, explicitly distinguishing itself from it.
  So auto-merge exists somewhere in the field, and Open SWE treats not having it
  as a positioning choice rather than a gap.
- **The agent is re-invokable on review feedback.** Tag `@openswe` in a PR
  comment and it addresses the feedback and pushes to the same branch. That is
  the useful half of "the agent participates in review" without giving it any
  authority over the outcome, and it is a better answer to review latency than
  letting it merge.

---

## What this changes about BRE-35

The proposal survives, with its authority model confirmed rather than assumed,
and with three corrections.

**1. The design was right and is now evidenced.** Deterministic policy decides;
the LLM advises. Four systems, four independent arrivals at the same split. What
was a design argument from our invariants is now also an observation about the
field.

**2. Policy belongs in the repo, not in GitHub settings.** Adopt gh-aw's
Alternative-2 reasoning. Our merge policy should be a version-controlled file
that CI reads, so it is diffable and reviewable, and so a doc describing it
cannot silently drift from it the way §2 did. This is a new requirement that was
not in BRE-35.

**3. "The agent merges into `main`" is outside what anyone does.** Gate (i) is
unconditional upstream. Options, in increasing order of how far they depart from
precedent:

- **Stay inside precedent**: agent-eligible PRs target a staging branch, never
  `main`. Costs a branch model we do not have.
- **Depart deliberately**: allow agent merge to `main` for PRs touching nothing
  in `_HARNESS_PATHS`, with the full ten-gate evaluation plus our own
  protected-path gate, and record that we are knowingly going further than
  gh-aw ships. Defensible, because our protected-path gate is a control gh-aw
  has no equivalent of, and because `required-labels` keeps a human in the grant.
- **Do neither yet**: build the fleet as advisory-only, measure it in shadow
  mode, and revisit merge authority once there is data.

The third is the cheapest and loses nothing, because the fleet's value is the
findings, and the merge automation is a separate benefit that can land later.

## What nobody has

- **No LLM merge authority anywhere.** Not one system.
- **No agent merge into a default branch.** Explicitly forbidden in the only
  implementation that merges at all.
- **No verification of an empirical claim**, which remains the thing this project
  exists for and which no amount of PR review machinery addresses. A perfect
  review fleet still cannot tell whether a reported metric is real.

## Sources

- [ADR-27193, gated merge-pull-request](https://github.com/github/gh-aw/blob/main/docs/adr/27193-gated-merge-pull-request-safe-output.md)
- [gh-aw safe outputs reference](https://github.github.com/gh-aw/reference/safe-outputs/)
- [gh-aw compiler threat detection spec](https://github.com/github/gh-aw/blob/main/specs/compiler-threat-detection-spec.md)
- [Factory-AI/droid-action](https://github.com/Factory-AI/droid-action)
- [langchain-ai/open-swe](https://github.com/langchain-ai/open-swe)
