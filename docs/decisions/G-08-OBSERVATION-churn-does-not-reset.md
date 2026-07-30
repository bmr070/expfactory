---
id: G-08-OBSERVATION
parent: decisions/M2-04-RESOLVED-preregistration.md
labels: [wayfinder:finding]
mode: HITL
status: OPEN — recorded, deliberately not fixed
observed: 2026-07-30
supersedes: nothing
---

# G-08 — the churn counter does not reset on a promotion

## Status: this records an observation. It does not change the gate.

`gate_prereg_churn`'s own comment asks for exactly this:

> Re-calibrate against real lineages once there are any — a threshold tuned on
> synthetic fixtures is a starting point, not a finding.

There is now one. This file is that observation written down. **Nothing is
tuned to it**, because tuning a threshold to a single lineage is the error the
comment exists to prevent, in a new coat.

## What was run

`examples/h2_hill_climb.py` against the provisioned `DroneAudioDataset`, on the
session-grouped split, 2026-07-30. Five attempts, all chosen before any were run,
each preregistered before it executed. Minimum effect `+0.010`, seeds `(0, 1, 2)`.

The ancestor was measured, not typed: `examples/h1_drone_audio.py` recorded
**Pd@1%FAR 0.8762 ± 0.0114** `[0.8610 0.8791 0.8884]` session-grouped, and the
leaky clip-level split of the same data scores 0.9193 — an inflation of
**+0.0431**, against EchoHawk's reported +0.051. Different features, different
absolute values, effect reproduces.

| # | Attempt | Pd@1%FAR | Δ | Verdict |
|---|---|---|---|---|
| 0 | baseline (H1 config) | 0.8762 | — | ancestor |
| 1 | `more_trees` | 0.8831 | +0.0069 | rejected — under the declared minimum |
| 2 | `balanced_classes` | 0.8871 | +0.0109 | **PROMOTED** |
| 3 | `shallow_forest` | 0.8530 | −0.0231 | rejected — prereg, seed dominance |
| 4 | `more_features_per_split` | 0.8854 | +0.0092 | rejected — prereg, seed dominance |
| 5 | `extra_trees` | 0.8634 | −0.0128 | rejected — prereg, **churn** |

## The observation

**This lineage promoted honestly at attempt 2 and was flagged as metric-shopping
at attempt 5.**

`Ledger.non_promoting_prereg_count` excludes preregistrations that a promoted
verdict cites, but the count it returns is over the whole lineage and **a
promotion does not reset it**. Attempts 1, 3, 4 and 5 are four non-promoting
preregistrations under one `parent_id`, so attempt 5 reports 4 against
`DEFAULT_MAX_ATTEMPTS = 3`.

So continuing to test ideas *after* a genuine win scores identically to shopping
for one.

## Why this is a design question and not a bug

It is defensible as written, and that is what makes it worth recording rather
than patching.

**The case for the current behaviour.** The gate asks "does this lineage look
like it is searching for a number?" A researcher who lands one real improvement
and then files eleven more preregistrations is still doing something that
produces a spurious result eventually — the promotion at attempt 2 does not make
attempts 6 through 16 any less of a search. Resetting on success hands every
lineage a fresh budget for the price of one real win, which is a cheap price.

**The case against.** Hill-climbing *is* repeated attempts, and the whole factory
exists to run them unattended. A gate that blocks the sixth honest idea in a
lineage that has already produced a promoted one is measuring persistence, not
dishonesty. Under the current rule the safest strategy is to start a new lineage
after every promotion — which is legitimate, achievable, and makes the gate
advisory in practice while looking blocking on paper. **A rule that is trivially
routed around by an honest actor is worse than no rule**, because it produces a
false sense of enforcement.

**The third option nobody has argued yet.** Count *consecutive* non-promoting
attempts rather than lineage-total. That flags a run of failures — the actual
S-hacking signature the literature describes — and does not penalise a productive
lineage for being long. It is not obviously right either: eight alternating
promote/fail attempts would never trip it.

## What would settle it

Not more synthetic fixtures. That is the same data with more rows, which is
`GH#5`'s original objection and it applies to this question too.

Two real inputs would move it:

1. **More real lineages.** This is one, on one task, with one baseline. Three or
   four across different tasks would show whether "promoted then kept going" is
   the common shape or an artifact of how `h2_hill_climb.py` picked its five.
2. **A stated policy on what G-08 is for.** The two cases above are not an
   empirical disagreement — they are a disagreement about whether the gate polices
   *the lineage* or *the search*. That is the owner's call, and it decides the
   implementation rather than following from it.

## Deliberately not done

- Threshold not changed. `DEFAULT_MAX_ATTEMPTS` stays 3.
- Counting rule not changed.
- No fixture added that encodes either answer, because a fixture is a claim about
  which behaviour is correct and that is precisely what is open. Invariant 4 cuts
  both ways: every gate traces to a fixture, and a fixture asserts a decision has
  been made.

The run is reproducible today:

```bash
PYTHONPATH=src python examples/h1_drone_audio.py
PYTHONPATH=src python examples/h2_hill_climb.py
```
