---
name: add-gate
description: Add or change a gate in the verification harness, with the fixtures that justify it and the partition discipline that keeps it honest. Use when adding a check, changing a threshold, or hardening the gate set. Trigger words - add a gate, new check, tighten the gates, threshold, fixture, harden verification.
---

# Add a gate

The gate set is the thing this factory is. A gate added carelessly is worse than
no gate, because it reads as protection.

## Steps

1. **Write the fixtures first.** Every gate traces to a fixture (invariant 4).
   No fixture, no gate. Write the case it must reject *and* the case it must
   still promote, before the implementation exists.

2. **Split them across both partitions.** Visible fixtures run in CI and you may
   tune against them freely. Held-out fixtures are a measurement, spent rarely,
   by a human. Never consult the held-out partition while tuning (invariant 5) —
   if CI ran it, every red build would be a tuning signal and the partition would
   be burnt in a week.

3. **Implement it.** Return a `GateResult(name, passed, detail, blocking)`. The
   `detail` is read by a human deciding whether to trust the run, so it says what
   was seen and what that means, not just "failed".

4. **Expect it to be wrong the first time.** The dominance gate shipped with an
   inverted ratio and could never pass anything; a fixture caught it. Verify
   against both partitions before believing it.

5. **Add it to the probe.** `gate_probe.py` sweeps properties the fixtures cannot
   state: every blocking gate can pass, can fail, noise does not flip a verdict,
   more leakage never helps. A gate with no declared trigger is reported as a
   coverage gap rather than silently uncovered.

6. **Classify the module if it is new.** `_HARNESS_PATHS` or `_NOT_SUBSTRATE` in
   `gates_v1.py`, plus a line in `.github/CODEOWNERS`. The test suite fails until
   you do, which is deliberate.

7. **Run both.**
   ```bash
   python -m expfactory.selfcheck
   python -m expfactory.llm_probe
   ```

## Calibrating a threshold

- **Never guess it.** Calibrate against visible fixtures, then measure once on
  held-out. A guessed threshold is a speculative gate.
- **Pin it with a test** so editing the constant fails loudly.
- **Synthetic fixtures are a starting point, not a finding.** `DEFAULT_MAX_ATTEMPTS`
  is still open on GH#5 for exactly this reason, and one real lineage did not
  settle it either.

## Traps

- **A gate that fires on nothing is decoration.** A gate that fires on everything
  is worse: it will be disabled, and then nothing is checked.
- **False alarms teach people to skim.** `gate_probe`'s first can-fail sweep
  reported six false positives against a healthy gate set. Six is worse than
  zero, because that is how a wall becomes a formality.
- **A conditional gate is silent unless armed.** G-09 needs a `DatasetGrouping`,
  G-10 needs an attestation source, G-07/G-08 need `require_prereg=True`. Silence
  from an unarmed gate is not a pass.
- **Do not gate on who.** Every control here keys on *what changed* or *what the
  evidence shows*. A control keyed on identity fails when identity is wrong, and
  it has been wrong four times in this repo.

## Related

`src/expfactory/gates_v1.py`, `src/expfactory/adversarial_suite.py`,
`src/expfactory/gate_probe.py`, `docs/SPEC.md`.
