# 04 — Build the labeled adversarial suite (acceptance = W-09 eval set)

**What to build:** The factory has a known-answer test of its own judgement: N candidate experiments with pre-assigned verdicts — real gains, seed-noise mimics, leakage traps, holdout-burn attempts — including held-out fixtures the gate author never tunes against.

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] Suite contains genuine-improvement, seed-noise, leakage, and holdout-burn cases with known verdicts
- [ ] The gate harness classifies every visible fixture correctly
- [ ] A held-out fixture partition exists that is never consulted during gate tuning
- [ ] A run that correctly rejects every proposed gain is reported as PASS
