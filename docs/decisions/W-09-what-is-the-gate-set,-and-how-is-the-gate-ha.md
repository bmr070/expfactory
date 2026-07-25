---
id: W-09
parent: wayfinder:map
labels: [wayfinder:prototype]
mode: HITL
blocked-by: [W-01, W-03]
assignee: driver
status: closed
---

# W-09 — What is the gate set, and how is the gate harness itself evaluated?

## Question

Specify the verifier for the chosen lane, and — separately — how that verifier is
tested.

The expfactory prototype supplies six gates for the empirical lane. Its known flaw is that its own
scenarios were miscalibrated: a case planted as seed-noise was correctly judged real, which means
the demo validated nothing. A gate harness with no eval of its own is exactly the failure mode the
harness exists to prevent.

Produce: the gate set for the chosen lane, plus a set of known-bad and known-good fixtures the
harness must classify correctly. Link the prototype as an asset.

<!-- blocked by: W-01, W-03 -->

## Resolution

**Verdict: the six prototype gates are the v1 empirical gate set, hardened; the gate harness is
evaluated against the labeled adversarial suite from W-03, including held-out fixtures.**

**Gate set (from `expfactory/`, retained):** no-leakage, reproducibility, seed-variance,
too-good-to-be-true (escalates, non-blocking), holdout-budget, cost. Three hardenings required
before v1:
- **Add a diff-level test-tamper gate.** The prototype has no defense against the flaky-test failure
  mode (stress scenario #1): removed assertions, added skip/xfail, lowered coverage thresholds,
  mutations to the harness itself. This is the empirical-lane analogue of the deterministic lane's
  "don't let the agent edit the verifier."
- **Fix the too-good gate's calibration.** The prototype's own demo miscalibrated it (Notice to
  Mariners #1). Recalibrate against the labeled suite rather than a hand-picked delta.
- **Make holdout-budget durable.** Currently counts ledger rows; must survive restart and be
  enforced by the runner, not the agent.

**How the harness evaluates itself** (this is the part the prototype lacked, and the reason W-09
existed): the labeled suite from W-03 is the eval set. Known-good and known-bad fixtures with
pre-assigned verdicts; the harness must classify every one correctly. Held-out fixtures the gate
author never sees during tuning prevent the harness being overfit to its own test set — the harness
held to the same holdout discipline it enforces on experiments.

**Standing rule:** a new gate is added only when a fixture demonstrates a failure the current set
misses. Gates are not speculative; each one traces to a caught (or missed) fixture. This keeps the
set from bloating into unmaintainability.

## AMENDMENT (post preregistration research)

The v1 gate set grows from six to **seven**. G-07 `preregistration` closes metric-shopping — an agent
reporting whichever number happened to move. Named in the literature as HARKing (hypothesising after
results known) and S-hacking (trialling many metrics, reporting the favourable one), and documented as
real behaviour of autonomous research systems.

Every existing gate is silent on it: leakage, seed variance, holdout budget and tamper detection all
pass cleanly on a result whose metric was chosen retroactively.

The adversarial suite therefore needs a **fifth fixture class** beyond genuine / seed_noise / leakage /
holdout_burn: `metric_shopped`, expecting REJECT, plus a held-out variant. Design is open (M2-04);
build is M2-05. Standing rule unchanged: every gate traces to a fixture.
