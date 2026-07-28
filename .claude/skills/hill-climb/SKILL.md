---
name: hill-climb
description: Run a preregistered improvement attempt against a recorded baseline, so the result is adjudicated rather than asserted. Use when trying to beat a number, tuning a model, proposing an improvement, or running a confirmatory experiment. Trigger words - hill climb, beat the baseline, improve the metric, try a variant, experiment, preregister.
---

# Hill-climb

An attempt only counts if the decision rule was fixed before the number was seen.
Everything here exists to make that checkable rather than promised.

**A run where every proposed improvement is correctly rejected is a passing run.**
If you find yourself trying to make something get promoted, stop.

## Steps

1. **Find the recorded parent.** The baseline must be a ledger row, not a number
   you typed. G-07 rule 8 checks a declared baseline against what the parent
   actually scored. A number typed into a constant is worth exactly what an
   invented one is worth, and `h2_hill_climb.py` was rejected five times before
   this was done properly.

   No parent? Run the baseline first, append it, then continue.

2. **Choose every attempt before running any of them.** Write the list down.
   Choosing the next variant after seeing the last result is the search this
   whole apparatus exists to catch.

3. **File the preregistration before the run.** `Preregistration(primary_metric,
   direction, baseline_value, minimum_effect, seeds, parent_id)` appended to the
   ledger. G-07 rule 2 compares ledger positions, so the ordering is the
   mechanism, not a formality.

   `minimum_effect` is declared once, up front, for the whole lineage. A
   per-attempt effect size chosen after seeing the number is metric-shopping.

4. **Run it.** Seeds must match the declared set exactly. Reporting the best 5 of
   20 is caught by rule 5, and should be.

5. **Adjudicate on a verifier you did not build inside the run.**
   `require_prereg=True`, a `PreregStore`, and a `DatasetGrouping` if the data is
   segmented. `promoted` comes only from the gates.

6. **Record every attempt, including the failures.** Pruning the lineage to look
   tidier is exactly the behaviour G-08 counts.

## Traps

- **Do not tune against the held-out partition** (invariant 5). The factory is
  held to the discipline it enforces.
- **Do not quote a published bar from memory.** Read the target doc. The
  EchoHawk figure was wrong in five files because it came from an abstract.
- **G-08 does not reset on a success.** A lineage that promoted at attempt 2 and
  kept exploring can still be flagged at attempt 5. That is recorded as a finding
  in GH#5, not fixed, and it is not a reason to prune the lineage.
- **Beating an external target is an outcome, never an acceptance criterion**
  (L-01).

## Worked example

`examples/h2_hill_climb.py` — five attempts, one promotion, one G-08 flag, and
the baseline reproduced exactly against the pinned dataset commit.

## Related

`docs/decisions/M2-04-RESOLVED-preregistration.md`, `src/expfactory/prereg.py`,
`docs/research/acoustic-drone-detection.md`.
