"""
selfcheck — the boundary test: does the gate harness still classify correctly?

This is categorically different from the unit tests. The unit tests ask "does
each gate do what its author meant." This asks "does the *assembled* harness
reach the right verdict on known-answer cases" — which is the only question that
matters, because a gate set can be individually correct and collectively wrong.

## Why the held-out partition is not in CI

Invariant 5: never consult the held-out fixtures while tuning gates. If CI ran
the held-out partition on every commit, every red build would be a tuning signal
and the partition would be burnt within a week — the factory committing exactly
the holdout-burn offence it exists to detect.

So the split is enforced operationally, not just documented:

  visible  — runs in CI, blocking, every commit. Tune against this freely.
  heldout  — run deliberately and rarely, by a human, to measure whether tuning
             generalised. Each run costs a little of its value. Treat it like the
             experiment holdout budget, because it is the same thing.

Usage:
    python -m expfactory.selfcheck                 # visible partition (CI default)
    python -m expfactory.selfcheck --heldout       # spends holdout value; be sure
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from expfactory.adversarial_suite import (
    SuiteResult,
    build_attested_suite,
    build_group_suite,
    build_prereg_suite,
    build_suite,
)
from expfactory.harness import RunResult
from expfactory.verifier import Candidate, GateVerifier, Ledger, VerdictBundle


def run(partition: str = "visible") -> SuiteResult:
    return build_suite().evaluate(GateVerifier(), partition=partition)


def _recorded_parent(exp_id: str, metrics: dict[str, float]) -> VerdictBundle:
    """A minimal past result for a fixture lineage to descend from."""
    runs = [
        RunResult(
            seed=s,
            val_metric=metrics["val_metric"],
            train_ids_hash="t",
            eval_ids_hash="e",
            overlap_count=0,
            wall_seconds=0.0,
            extra={k: v for k, v in metrics.items() if k != "val_metric"},
        )
        for s in range(3)
    ]
    candidate = Candidate(hypothesis="ancestor", config={}, code_hash="a", runs=runs)
    return GateVerifier(id_factory=lambda: exp_id).run(candidate)


def run_prereg(partition: str = "visible") -> SuiteResult:
    """G-07's fixtures need a verifier configured for the hill-climb workflow —
    require_prereg=True, with the declared preregistrations already filed. Filing
    happens here rather than in the fixtures because *when* a prereg was filed is
    the thing under test."""
    setup = build_prereg_suite()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(Path(tmp) / "suite.jsonl")
        # Record the ancestors first: rule 8 checks each declared baseline against
        # what the parent actually scored, read from the ledger.
        for exp_id, metrics in setup.parents:
            ledger.append(_recorded_parent(exp_id, metrics))
        for prereg in setup.preregs:
            ledger.append_prereg(prereg)
        verifier = GateVerifier(require_prereg=True, prereg_store=ledger)
        return setup.suite.evaluate(verifier, partition=partition)


def run_group(partition: str = "visible") -> SuiteResult:
    """G-09's fixtures need a verifier the *task* configured with a grouping.

    Built here rather than carried on the fixtures because that is where the real
    declaration lives: if a candidate could supply its own grouping it could also
    decline to, and the gate would be advisory.
    """
    setup = build_group_suite()
    return setup.suite.evaluate(GateVerifier(grouping=setup.grouping), partition=partition)


def run_attested(partition: str = "visible") -> SuiteResult:
    """G-10's fixtures need a verifier pointed at a job log.

    Built here rather than carried on the fixtures for the same reason as G-09's
    grouping: a candidate that supplied its own attestation source could supply
    one that says yes to everything.
    """
    setup = build_attested_suite()
    return setup.suite.evaluate(GateVerifier(attestations=setup.jobs), partition=partition)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--heldout",
        action="store_true",
        help="evaluate the held-out partition (spends its value — see module docstring)",
    )
    args = ap.parse_args(argv)
    partition = "heldout" if args.heldout else "visible"

    if args.heldout:
        print("!! held-out partition: this is a measurement, not a tuning signal.")
        print("!! If it fails, do NOT tune until it passes. Record and think.\n")

    suites = [
        ("core gates", run(partition)),
        ("G-07 preregistration", run_prereg(partition)),
        ("G-09 group leakage", run_group(partition)),
        ("G-10 attested run", run_attested(partition)),
    ]

    print(f"partition : {partition}")
    ok = True
    for label, result in suites:
        print(f"  {label:<22} {result.correct}/{result.total} classified correctly")
        for m in result.mismatches:
            print(f"    MISMATCH  {m}")
        ok = ok and result.is_pass

    # A pass is "matched every assigned verdict" — NOT "promoted something".
    # Rejecting every candidate is a pass when every candidate was meant to be
    # rejected (W-03's load-bearing negative criterion).
    # Properties of the gate set, not points on it. The suite can be fully
    # correct while a gate is incapable of passing anything -- that happened on
    # 2026-07-27 -- and only a property sweep notices.
    from expfactory.gate_probe import sweep

    probe = sweep()
    print(
        f"  {'gate properties':<22} {probe.checked} probes, {len(probe.findings)} disagreement(s)"
    )
    for finding in probe.findings:
        print(f"    MISMATCH  {finding}")
    ok = ok and probe.is_clean

    print("verdict   :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
