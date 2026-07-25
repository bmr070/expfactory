---
id: M2-04
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
status: RESOLVED
resolved: 2026-07-25
implements: G-07
---

# M2-04 — How do you preregister an inherently exploratory hill-climb?

## The failure being closed

An agent runs an experiment, sees the primary metric didn't move but latency
improved, and reports latency as the win. Every gate currently built is silent:
leakage, seed variance, holdout budget and tamper detection all pass, because
nothing about the result is *fake* — only the **claim** is. Named in the
literature as HARKing (hypothesising after results are known) and S-hacking
(trialling many metrics, reporting the favourable one).

## Resolving the tension

Preregistration assumes a confirmatory study; a hill-climb is exploration. The
literature's sketch — "preregister the selection methodology, not the hypothesis"
— is directionally right but under-specified. Two moves make it concrete.

### Move 1 — preregister the *decision rule*, not the hypothesis

What must be immutable before the data is seen is not "I predict X will improve."
It is **"here is the rule by which I will decide whether anything improved."** A
hill-climb can explore freely as long as the adjudication rule was fixed first.

### Move 2 — split runs into two classes, and let only one promote

| | Exploratory | Confirmatory |
|---|---|---|
| Preregistration | not required | required, hashed, filed first |
| Seeds | any | must match the declared set |
| Can promote? | **structurally never** | yes, if the rule is met |
| Purpose | learn what to confirm | earn a promotion |
| Cost | cheap, unlimited | one prereg per attempt |

This is how competent empirical work already runs: explore freely, then confirm
against a pre-committed rule on fresh seeds. The agent gets full freedom to
search; it simply cannot cash a search result as a finding without filing first.

## The preregistration record

Declared **before** any confirmatory run executes:

| Field | Why it is there |
|---|---|
| `primary_metric` | Exactly one. The only metric that can promote. |
| `direction` | maximize / minimize. Prevents reading a regression as a win. |
| `minimum_effect` | Smallest change that counts. Kills "+0.001 is an improvement." |
| `seeds` | The exact seed set. Prevents running 20 and reporting the best 5. |
| `decision_rule` | How metric + effect + noise band combine into promote/reject. |
| `secondary_metrics` | Recorded, reported — **never sufficient for promotion**. |
| `guardrail_metrics` | May only **block**, never promote. Latency lives here. |
| `parent_id` | Lineage, so churn is countable. |

**The asymmetry is the whole mechanism.** A metric can be allowed to promote, or
allowed to block, but never both. The opening failure dissolves: latency is a
guardrail or a secondary, so "primary flat, latency improved" cannot produce a
promotion under any reading of the rule.

## What may change mid-climb, and by whom

- **Nothing inside a filed preregistration.** It is content-hashed; the hash is
  recorded in the ledger row.
- **A new preregistration may be filed at any time, by the agent.** Changing your
  mind is legitimate science; doing it silently is not.
- **Amendment = a new record with `supersedes: <hash>`.** Both stay in the ledger.
  The agent may create; it may never edit or delete.

### The honest claim about what this buys

Preregistration does not make metric-shopping *impossible*. An agent can file
eight preregistrations naming eight primary metrics and promote on the eighth.
What preregistration does is make that **legible** — it converts an invisible
narrative choice into a countable ledger artifact.

Legibility is only worth something if something counts it. Hence a second gate.

## The gates

### G-07 — preregistration compliance (blocking)

1. A confirmatory candidate carries a `prereg_hash`.
2. That preregistration appears in the ledger at a **strictly earlier position**
   than this run. The ledger is append-only, so ordering *is* the proof that the
   prediction preceded the result. This is the anti-HARKing mechanism.
3. The reported primary metric **name** matches the declared one.
4. The observed effect meets `minimum_effect` in the declared `direction`.
5. The run's seeds match the declared seed set exactly.
6. No guardrail metric regressed.
7. `exploratory=True` ⇒ **never promoted**, unconditionally.

### G-08 — preregistration churn (blocking)

Count filed preregistrations sharing a lineage that did not promote. Past a
threshold, block and escalate: that pattern is S-hacking whatever each individual
prereg looked like. Threshold to be calibrated against fixtures, **not guessed** —
per W-09, every gate traces to a fixture.

## Answers to the ticket's explicit questions

- **What is declared?** Primary metric, direction, minimum effect, seed set,
  decision rule, and the secondary/guardrail split. Not the hypothesis.
- **What may change, by whom?** Nothing within a record. The agent may file new
  ones; supersession is explicit and both survive in the ledger.
- **How are exploratory runs marked unpromotable?** A flag the gate hard-blocks
  on, not a convention. Exploration is unlimited and free.
- **May secondary metrics ever justify promotion?** **No.** Recorded, never
  sufficient. If a secondary is what you actually care about, file a new
  preregistration naming it primary and run a fresh confirmatory. That costs one
  cycle — and that friction is the point.

## Consequences

- Unblocks **M2-05** (build the gate and its fixtures).
- Fixtures needed before implementation: a clean confirmatory promote; a
  metric-swap (primary flat, secondary up) that must reject; a post-hoc filing
  (prereg after run) that must reject on ordering; a seed-shop; a guardrail
  regression; and a churn sequence. Both suite partitions, per invariant 5.
- `Candidate` gains `prereg_hash` and `exploratory`. The refactor already made
  `Candidate` the validating boundary, so both land there cleanly.
- **Note the ordering dependency:** G-07 rule 2 requires the ledger to expose
  position. `Ledger.all()` returns insertion order today, which is sufficient,
  but the guarantee must become explicit and tested before G-07 relies on it.
