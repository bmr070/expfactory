"""expfactory — a software factory for empirical work.

CI cannot be the verifier here: there are no tests to pass, only numbers that may
or may not be real. This package is the replacement for CI — an append-only ledger
plus deterministic anti-fooling gates that adjudicate whether a reported gain is a
finding or an artifact of seed noise, leakage, holdout burn, or tampering.

The load-bearing rule: `promoted` is derived from the gates and can never be set
by a caller. See docs/SPEC.md for the layered L0/L1/L2 verification model.
"""

from expfactory.harness import Experiment, GateResult, RunResult
from expfactory.holdout import HoldoutBudget, HoldoutExhausted
from expfactory.pipeline import run_and_record
from expfactory.verifier import (
    Candidate,
    ExitCodeVerifier,
    GateVerifier,
    Ledger,
    VerdictBundle,
    Verifier,
)

__all__ = [
    "Candidate",
    "Experiment",
    "ExitCodeVerifier",
    "GateResult",
    "GateVerifier",
    "HoldoutBudget",
    "HoldoutExhausted",
    "Ledger",
    "RunResult",
    "VerdictBundle",
    "Verifier",
    "run_and_record",
]
