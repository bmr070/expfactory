# 03 — Harden the gate set to v1 (test-tamper, calibration, durable holdout)

**What to build:** The verifier can no longer be fooled by the three known bypasses: an experiment that tampers with its own tests, a miscalibrated too-good threshold, or a holdout budget that resets on restart.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] A diff-level gate fails on removed assertions, added skip/xfail, lowered coverage thresholds, or edits to the harness itself
- [ ] The too-good gate's threshold is calibrated against fixtures, not a hand-picked delta
- [ ] The holdout budget persists across process restart and is enforced by the runner, not the agent
- [ ] Each gate traces to at least one fixture
