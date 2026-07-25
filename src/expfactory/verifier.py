"""
verifier — the plugin boundary (ticket 02).

The dispatcher sees exactly one contract: Verifier.run(candidate) -> VerdictBundle.
Whether the verdict came from the empirical gate harness or from a CI exit code is
invisible above this line. This is the seam that W-02 designed and everything
downstream (ledger, runner, review, ratchet) hangs off.

`promoted` is a derived, frozen property of the bundle. No caller can forge it.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

# reuse the prototype's gate functions and run record unchanged
from expfactory.harness import (
    Experiment,
    RunResult,
    DEFAULT_GATES,
    GateResult,
)


# --------------------------------------------------------------------------- #
# Candidate: what a caller submits for verification
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Candidate:
    hypothesis: str
    config: dict[str, Any]
    code_hash: str
    runs: Sequence[dict[str, Any]]          # each dict -> RunResult kwargs
    cost_usd: float = 0.0
    parent_id: str | None = None
    diff: Any = None                        # DiffEvidence | None; drives the tamper gate

    def _experiment(self, exp_id: str) -> Experiment:
        exp = Experiment(
            exp_id=exp_id,
            parent_id=self.parent_id,
            hypothesis=self.hypothesis,
            config=dict(self.config),
            code_hash=self.code_hash,
            cost_usd=self.cost_usd,
        )
        exp.runs = [RunResult(**r) for r in self.runs]
        return exp


# --------------------------------------------------------------------------- #
# VerdictBundle: what every verifier returns (frozen — promotion cannot be forged)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VerdictBundle:
    exp_id: str
    promoted: bool
    blocked_by: tuple[str, ...]
    config: dict[str, Any]
    code_hash: str
    seeds: tuple[int, ...]
    gate_names: tuple[str, ...]
    mean_metric: float
    cost_usd: float
    artifact: dict[str, Any]

    def with_exp_id(self, exp_id: str) -> "VerdictBundle":
        # returns a NEW bundle; the original stays immutable
        d = asdict(self)
        d["exp_id"] = exp_id
        d["artifact"] = {**self.artifact, "exp_id": exp_id}
        return VerdictBundle(**d)


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #

@runtime_checkable
class Verifier(Protocol):
    def run(self, candidate: Candidate) -> VerdictBundle: ...


# --------------------------------------------------------------------------- #
# Implementation 1: empirical gate harness
# --------------------------------------------------------------------------- #

class GateVerifier:
    """Wraps the prototype's gate set behind the plugin boundary."""

    def __init__(self, gates=DEFAULT_GATES, baseline: Experiment | None = None,
                 ledger_ctx=None):
        self._gates = gates
        self._baseline = baseline
        self._ledger_ctx = ledger_ctx

    def run(self, candidate: Candidate) -> VerdictBundle:
        exp = candidate._experiment(uuid.uuid4().hex[:12])
        ctx = dict(baseline=self._baseline, ledger=self._ledger_ctx)
        exp.gates = [g(exp, **ctx) for g in self._gates]
        # Baseline-free calibration gate (ticket 03): always runs, catches the
        # single-lucky-seed case the baseline-dependent seed_variance gate misses.
        from expfactory.gates_v1 import gate_no_single_seed_dominance
        exp.gates.append(gate_no_single_seed_dominance(exp))
        # Diff-level gates run only when the candidate carries diff evidence.
        # The runner always supplies one; a candidate without a diff simply skips
        # them rather than crashing (backward compatible).
        if candidate.diff is not None:
            from expfactory.gates_v1 import gate_no_test_tampering
            exp.gates.append(gate_no_test_tampering(candidate.diff))
        promoted = not exp.blocked_by     # derived, never set
        return VerdictBundle(
            exp_id=exp.exp_id,
            promoted=promoted,
            blocked_by=tuple(exp.blocked_by),
            config=exp.config,
            code_hash=exp.code_hash,
            seeds=tuple(r.seed for r in exp.runs),
            gate_names=tuple(g.name for g in exp.gates),
            mean_metric=exp.mean_metric,
            cost_usd=exp.cost_usd,
            artifact={
                "exp_id": exp.exp_id,
                "hypothesis": exp.hypothesis,
                "gates": [{"name": g.name, "passed": g.passed, "detail": g.detail}
                          for g in exp.gates],
            },
        )


# --------------------------------------------------------------------------- #
# Implementation 2: deterministic CI adapter (proves the seam admits two impls)
# --------------------------------------------------------------------------- #

class ExitCodeVerifier:
    """Shells out to a command; exit 0 -> promoted. The deterministic lane's
    verifier, satisfying the same contract as the empirical one. Per W-02 this
    exists to prove the interface holds two implementations, even when no v1
    workload drives it."""

    def __init__(self, command: Sequence[str]):
        self._command = list(command)

    def run(self, candidate: Candidate) -> VerdictBundle:
        exp_id = uuid.uuid4().hex[:12]
        proc = subprocess.run(self._command, capture_output=True, text=True)
        promoted = proc.returncode == 0
        return VerdictBundle(
            exp_id=exp_id,
            promoted=promoted,
            blocked_by=() if promoted else (f"exit_{proc.returncode}",),
            config=dict(candidate.config),
            code_hash=candidate.code_hash,
            seeds=tuple(r["seed"] for r in candidate.runs),
            gate_names=("ci_exit_code",),
            mean_metric=float("nan"),
            cost_usd=candidate.cost_usd,
            artifact={
                "exp_id": exp_id,
                "command": self._command,
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-500:],
                "stderr_tail": proc.stderr[-500:],
            },
        )


# --------------------------------------------------------------------------- #
# Ledger: append-only, reconstructs from row alone
# --------------------------------------------------------------------------- #

class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, bundle: VerdictBundle) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(bundle), sort_keys=True) + "\n")

    def all(self) -> list[VerdictBundle]:
        out: list[VerdictBundle] = []
        for line in self.path.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                d["blocked_by"] = tuple(d["blocked_by"])
                d["seeds"] = tuple(d["seeds"])
                d["gate_names"] = tuple(d["gate_names"])
                out.append(VerdictBundle(**d))
        return out
