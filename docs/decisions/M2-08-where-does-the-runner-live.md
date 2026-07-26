---
id: M2-08
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
blocked-by: []
blocks: [agent-identity]
status: OPEN
raised: 2026-07-26
---

# M2-08 — Where does the runner live, and is it polled or pushed?

## Why this exists

I spent several exchanges recommending a GitHub App as the agent identity without
having decided **where the runner executes**. That is backwards. Each hosting
option supplies a different identity — some for free — so hosting is upstream and
identity falls out of it.

Picking an identity first means picking it in the dark.

## The constraint that does most of the work

> **Invariant 6 — the agent never holds GPU or tracker credentials.**

This eliminates the most tempting shape immediately: a GitHub Action that polls
*and runs the agent inline*. Anything executing in that job can read the job's
environment, so an agent running there holds the runner's tokens by construction.

It survives only if the Action is **just the poller** and the agent session runs
in a separate sandbox it dispatches to. Which is the two-substrate split (W-06)
applied one level up, and is probably the right shape regardless.

## Options

| | Identity | Secrets | Scheduling | CI on agent PRs |
|---|---|---|---|---|
| **A. Scheduled GitHub Action** | `github-actions[bot]`, free | Actions secrets, free | cron, built in | **needs a click** |
| **B. Daemon on the owner's machine** | GitHub App or bot PAT | local store | always on | automatic |
| **C. Daemon on a VM / container** | GitHub App | cloud secret manager | always on | automatic |
| **D. Webhook-driven, no poller** | depends on host | depends on host | **none needed** | depends |

### A — scheduled Action

Attractive because it collapses three problems into one move: identity, secret
storage and scheduling all come free, and each tick is short, which fits M2-03's
detach model exactly — poll, sweep, resolve, exit.

Two real costs. Since [11 Jun 2026][bot-pr] a bot-created PR runs CI only **after
a human approves the run**, so every agent PR costs a click before it can even go
green. And GitHub's cron is best-effort: delays of tens of minutes are normal
under load, and scheduled workflows are disabled after 60 days of repo inactivity.

For a factory whose stated throughput ceiling is human review bandwidth, a click
per PR is not obviously a cost. The cron unreliability matters more.

### B — daemon on the owner's machine

Simplest to reason about and to debug. Costs: the machine has to be on, the
environment is not reproducible, and long-lived credentials sit on a personal
laptop.

### C — daemon on a VM or container

The conventional answer. Reliable, reproducible, credentials in a real secret
manager. Costs money and is one more thing to operate for a solo factory.

### D — webhook-driven

Linear emits webhooks, so a poller may be unnecessary. This changes the shape of
`Runner.tick()` rather than just where it runs: the eligibility check stays, but
"find work" becomes "receive work". Worth deciding deliberately rather than
inheriting the poll loop because that is what got built first.

## Sub-question RESOLVED — Open SWE does not subsume the Runner

Read against the source (`agent/webhooks/linear.py`, `agent/dispatch.py`) rather
than the feature list. Possibility 2 below is the correct one.

**Open SWE's Linear path is invocation-driven, not a queue poll.** The handler is
`process_linear_issue(issue_data, repo_config)`, called from a webhook and built
entirely around `triggering_comment_id` / `comment_author`. It reacts 👀 to the
comment that summoned it, assembles a prompt from title + description + comments,
and dispatches a LangGraph run. There is no polling of an allowlisted queue, and
**no eligibility check of any kind** — it acts on whoever commented.

So the Runner stands. What it provides that Open SWE does not is precisely the
trust boundary: *who* made this dispatch-eligible.

## A conflict this turned up, which M2-07 did not catch

Open SWE resolves the Linear user's email to a GitHub login and, in its own
words, opens PRs **"as the triggering user"**.

M2-07 approved Open SWE partly because "its credential pattern matches the
standing rule: GitHub operations run with a dummy token inside the sandbox,
backed by a proxy, so the agent never holds a real credential." That reading was
right about invariant 6 and wrong about what follows from it. The agent indeed
never holds the credential — but the proxy then **attributes the work to the
human who triggered it**.

Which defeats the thing the identity work exists for. If agent PRs are authored
as `@bmr070`, CODEOWNERS cannot distinguish them from the owner's own, and the
red lane is decorative no matter which identity we provision. Open SWE is
*designed* to impersonate, because for its intended use — a developer asking a
bot for help — attribution to the asker is the desirable behaviour.

**This does not un-adopt Open SWE**, but it means adopting it as the agent
runtime requires either overriding that mapping, or accepting that PR authorship
cannot carry the trust separation and moving that separation somewhere else
(a required status check keyed on the branch, rather than CODEOWNERS on the
author). Raised as its own question rather than settled here.

## Original framing, retained

**Does Open SWE already do this?**

[M2-07](M2-07-RESOLVED-open-swe.md) adopted Open SWE at "L3 + the dispatch half of
L5", and listed among its capabilities: webhook routing, **Linear invocation**,
label triggers, and PR creation.

Our `Runner` implements poll → eligibility → claim → dispatch → post verdict. On
its face that overlaps what Open SWE was adopted to provide, and I built it
without re-reading M2-07's boundary. Two possibilities:

1. **Open SWE subsumes the loop**, and what remains ours is the *eligibility
   check* — Open SWE's label trigger almost certainly does not ask **who applied
   the label**, which is the whole trust boundary. Then our runner shrinks to a
   gate in front of, or inside, Open SWE's dispatch.
2. **Open SWE's Linear integration is invocation-only** (a human @-mentions it),
   not an autonomous poll of an allowlisted queue. Then our runner stands, and
   Open SWE is only the agent runtime.

Resolving this may delete code. That is a good outcome and a cheap one now;
it gets expensive once a runner is deployed and being depended on.

## Resolve

- Where the runner executes, and poll versus webhook.
- Whether Open SWE's dispatch subsumes `Runner`, and if so what is left of it.
- Then, and only then, which GitHub identity — because A supplies one free and
  B/C do not.

Linear's agent identity is orthogonal and already decided by cost: it is free,
consumes no seat, and `label_actor` needs it. Create it regardless of how this
ticket resolves.

[bot-pr]: https://github.blog/changelog/2026-06-11-bot-created-pull-requests-can-run-workflows-if-approved/
