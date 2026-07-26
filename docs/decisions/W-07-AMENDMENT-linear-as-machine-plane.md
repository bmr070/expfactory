---
id: W-07-AMENDMENT
amends: W-07
labels: [wayfinder:decision]
status: DECIDED
decided: 2026-07-26
---

# W-07 AMENDMENT — the runner reads Linear directly; no sync exists

## What changes

W-07 said: Linear = human board, GitHub Issues = machine control plane, one-way
Linear→Issues sync, **runners read GitHub only**.

Amended: **the runner reads Linear directly. There is no sync.** GitHub keeps
code, pull requests and CI, and stops being a work queue.

## Why W-07's reasoning does not survive contact

W-07's argument was *"one-way Linear→Issues sync (runners read GitHub only;
avoids two-way races)"*. The race is real — two systems both holding ticket state
will diverge — but the reasoning assumed the mirror had to exist and then picked
the safest way to run it.

Remove the mirror and the race cannot occur. That option was not considered.

## What reading Linear directly gains

**Real states.** Linear has `Backlog / Todo / In Progress / In Review / Done`.
GitHub Issues have open/closed and nothing else, so the GitHub adapter had to
encode runner states as `state:*` labels — which then forced a rule to be
loosened (see below).

**A typed distinction between bots and humans.** The runner's trust boundary
rests on *who* made a ticket dispatch-eligible. Linear's `issue.history` carries
an `actor`, and bot actors are typed as such rather than being a login string
that happens to end in `[bot]`. On GitHub the same check is a string comparison
against an allowlist — workable, weaker.

**One surface.** Work lives where the human already works. The previous split
meant tickets were written in Linear and polled from GitHub, with a sync that was
never built — so the board and the runner were simply disconnected, and that gap
sat unnoticed behind a closed provisioning ticket.

## What it costs

- `LinearTracker` has to be written. Cheap: `runner.Tracker` is a protocol and
  `GitHubTracker` already proved the seam admits an implementation.
- The nine open GitHub issues move to Linear.
- PR↔issue auto-linking is lost. Linear's GitHub integration recovers most of it
  via branch names and magic words; treat it as a convenience, not a control.

## What is deliberately kept

`GitHubTracker` stays. W-02's point was that the dispatcher is lane-agnostic and
the seam should admit two implementations; having a second real one is evidence
the abstraction is honest rather than a single implementation wearing a
protocol. It is also the natural fit if the deterministic lane is ever exercised,
since that lane's work already lives on GitHub next to its CI.

## A rule this sharpens, again

Building the GitHub adapter forced *"the runner never writes labels"* to become
*"the runner never writes **dispatch-granting** labels"*, because GitHub had no
other place to put state.

Under Linear the original, stricter rule can be restored: state is a state, and
the runner **never writes labels at all**. Labels stay purely the human's channel
for granting dispatch rights. The GitHub adapter keeps its allowlist, since it
still needs the looser form.

That is worth noticing. The looser rule was a concession to a storage limitation,
not a design improvement, and it is easy to mistake one for the other after the
fact.

## Consequences

- Build `LinearTracker` against the existing `runner.Tracker` protocol.
- The runner's `human_allowlist` is checked against Linear history actors, and
  should additionally require the actor be a user rather than a bot — a check
  GitHub could not offer.
- GH#2 (the Linear→Issues sync) is obsolete rather than done. Closed as
  superseded, not completed.
- `docs/agents/issue-tracker.md` records the operational detail.
