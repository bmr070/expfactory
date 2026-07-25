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

from expfactory.adversarial_suite import SuiteResult, build_suite
from expfactory.verifier import GateVerifier


def run(partition: str = "visible") -> SuiteResult:
    return build_suite().evaluate(GateVerifier(), partition=partition)


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

    result = run(partition)
    print(f"partition : {partition}")
    print(f"fixtures  : {result.correct}/{result.total} classified correctly")
    for m in result.mismatches:
        print(f"  MISMATCH  {m}")

    # A pass is "matched every assigned verdict" — NOT "promoted something".
    # Rejecting every candidate is a pass when every candidate was meant to be
    # rejected (W-03's load-bearing negative criterion).
    print("verdict   :", "PASS" if result.is_pass else "FAIL")
    return 0 if result.is_pass else 1


if __name__ == "__main__":
    sys.exit(main())
