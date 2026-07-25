"""
expfactory.harness — the research-mode verifier.

The deterministic factory (tickets -> PR -> CI green -> merge) works because CI is
ground truth. In research hill-climbing there is no CI: the agent proposes a change,
a number moves, and *nothing in the loop knows whether the number is real*.

This module is the replacement for CI. An experiment is only promoted if it survives
a set of adversarial gates designed around the specific ways ML results are fake:
seed noise, train/eval leakage, holdout over-querying, and irreproducibility.

Design rules:
  - The ledger is append-only. The agent may write experiments; it may not edit history.
  - Gates are deterministic functions of recorded evidence, not LLM judgement.
  - The holdout set has a query budget. Peeking is accounted for, not prevented.
  - Promotion is a *derived* property, never a field the agent sets.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


@dataclass
class RunResult:
    """One seed of one experiment."""

    seed: int
    val_metric: float
    train_ids_hash: str
    eval_ids_hash: str
    overlap_count: int
    wall_seconds: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass
class Experiment:
    """A node in the search tree. Parent linkage gives you the hill-climb history."""

    exp_id: str
    parent_id: str | None
    hypothesis: str
    config: dict[str, Any]
    code_hash: str
    runs: list[RunResult] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)
    promoted: bool = False
    holdout_metric: float | None = None
    cost_usd: float = 0.0
    created_at: float = field(default_factory=time.time)

    @property
    def mean_metric(self) -> float:
        return statistics.fmean(r.val_metric for r in self.runs) if self.runs else float("nan")

    @property
    def std_metric(self) -> float:
        vals = [r.val_metric for r in self.runs]
        return statistics.stdev(vals) if len(vals) > 1 else 0.0

    @property
    def blocked_by(self) -> list[str]:
        return [g.name for g in self.gates if g.blocking and not g.passed]


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #


@runtime_checkable
class HoldoutSource(Protocol):
    """What gate_holdout_budget actually needs: a count of holdout queries spent.

    Typed as a protocol rather than a concrete Ledger so that passing the wrong
    ledger is a type error. It used to be `Any`, and since two different classes
    were both called `Ledger`, handing over the one without this method
    type-checked cleanly and blew up at runtime.
    """

    def holdout_queries_used(self) -> int: ...


class ExperimentLedger:
    """Append-only JSONL experiment log. This is the proof-of-work artifact a human
    reviews instead of re-reading the agent's narrative summary."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, exp: Experiment) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(exp), sort_keys=True) + "\n")

    def all(self) -> list[Experiment]:
        out: list[Experiment] = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            d["runs"] = [RunResult(**r) for r in d["runs"]]
            d["gates"] = [GateResult(**g) for g in d["gates"]]
            out.append(Experiment(**d))
        return out

    def best_promoted(self) -> Experiment | None:
        promoted = [e for e in self.all() if e.promoted]
        return max(promoted, key=lambda e: e.mean_metric) if promoted else None

    def holdout_queries_used(self) -> int:
        return sum(1 for e in self.all() if e.holdout_metric is not None)


# --------------------------------------------------------------------------- #
# Gates — each returns GateResult; all are pure functions of recorded evidence
# --------------------------------------------------------------------------- #


def gate_no_leakage(exp: Experiment, **_: Any) -> GateResult:
    """Train/eval index sets must be disjoint. The single most common way an agent
    manufactures a breakthrough — usually by 'fixing' a preprocessing step that
    happens to fit the scaler on the full dataset."""
    total_overlap = sum(r.overlap_count for r in exp.runs)
    ok = total_overlap == 0
    return GateResult(
        "no_leakage",
        ok,
        "train/eval disjoint" if ok else f"LEAK: {total_overlap} shared sample ids",
    )


def gate_seed_variance(
    exp: Experiment,
    baseline: Experiment | None = None,
    min_seeds: int = 3,
    z: float = 2.0,
    **_: Any,
) -> GateResult:
    """An improvement must exceed the noise band, not just the baseline mean.
    Most reported gains in an unsupervised hill-climb are seed lottery."""
    if len(exp.runs) < min_seeds:
        return GateResult("seed_variance", False, f"need >={min_seeds} seeds, got {len(exp.runs)}")
    if baseline is None:
        return GateResult("seed_variance", True, "no baseline; recorded as reference")

    delta = exp.mean_metric - baseline.mean_metric
    pooled = (exp.std_metric**2 + baseline.std_metric**2) ** 0.5
    band = z * pooled / (len(exp.runs) ** 0.5)
    ok = delta > band
    return GateResult(
        "seed_variance",
        ok,
        f"delta={delta:+.4f} vs noise band {band:.4f} "
        f"({'real' if ok else 'INDISTINGUISHABLE FROM NOISE'})",
    )


def gate_too_good(
    exp: Experiment, baseline: Experiment | None = None, implausible_delta: float = 0.15, **_: Any
) -> GateResult:
    """A single change yielding a huge jump is more often a bug than a discovery.
    Non-blocking: it escalates to a human rather than killing the branch."""
    if baseline is None:
        return GateResult("too_good_to_be_true", True, "no baseline", blocking=False)
    delta = exp.mean_metric - baseline.mean_metric
    ok = delta <= implausible_delta
    return GateResult(
        "too_good_to_be_true",
        ok,
        f"delta={delta:+.4f}"
        if ok
        else f"delta={delta:+.4f} exceeds {implausible_delta} — ESCALATE, do not auto-promote",
        blocking=False,
    )


def gate_reproducible(exp: Experiment, tolerance: float = 1e-6, **_: Any) -> GateResult:
    """Same seed must give the same number. Catches nondeterministic data ordering,
    unseeded augmentation, and 'I only ran it once and it was good'."""
    by_seed: dict[int, list[float]] = {}
    for r in exp.runs:
        by_seed.setdefault(r.seed, []).append(r.val_metric)
    bad = {s: v for s, v in by_seed.items() if len(v) > 1 and (max(v) - min(v)) > tolerance}
    ok = not bad
    return GateResult(
        "reproducible", ok, "deterministic" if ok else f"nondeterministic seeds: {sorted(bad)}"
    )


def gate_holdout_budget(
    exp: Experiment, ledger: HoldoutSource | None = None, budget: int = 10, **_: Any
) -> GateResult:
    """The lockbox. Every look at the true holdout leaks a little information into
    your model-selection process. Budget the looks; when they're gone, they're gone."""
    if ledger is None:
        return GateResult("holdout_budget", True, "no ledger context", blocking=False)
    used = ledger.holdout_queries_used()
    ok = used < budget
    return GateResult(
        "holdout_budget",
        ok,
        f"{used}/{budget} holdout queries used"
        if ok
        else f"HOLDOUT BURNED ({used}/{budget}) — freeze and collect new data",
    )


def gate_cost(exp: Experiment, max_usd: float = 25.0, **_: Any) -> GateResult:
    ok = exp.cost_usd <= max_usd
    return GateResult("cost", ok, f"${exp.cost_usd:.2f} (cap ${max_usd:.2f})")


DEFAULT_GATES: tuple[Callable[..., GateResult], ...] = (
    gate_no_leakage,
    gate_reproducible,
    gate_seed_variance,
    gate_too_good,
    gate_holdout_budget,
    gate_cost,
)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def code_fingerprint(*sources: str) -> str:
    h = hashlib.sha256()
    for s in sources:
        h.update(s.encode())
    return h.hexdigest()[:16]


def run_experiment(
    hypothesis: str,
    config: dict[str, Any],
    train_fn: Callable[[dict[str, Any], int], RunResult],
    ledger: ExperimentLedger,
    seeds: Sequence[int] = (0, 1, 2),
    parent: Experiment | None = None,
    baseline: Experiment | None = None,
    gates: Iterable[Callable[..., GateResult]] = DEFAULT_GATES,
    code_hash: str = "unknown",
    cost_usd: float = 0.0,
) -> Experiment:
    """Execute one node of the hill-climb and adjudicate it.

    `promoted` is derived from the gates. There is deliberately no way for the
    caller (or an agent writing calls) to set it directly.
    """
    exp = Experiment(
        exp_id=uuid.uuid4().hex[:12],
        parent_id=parent.exp_id if parent else None,
        hypothesis=hypothesis,
        config=dict(config),
        code_hash=code_hash,
        cost_usd=cost_usd,
    )

    for seed in seeds:
        t0 = time.time()
        result = train_fn(config, seed)
        result.wall_seconds = time.time() - t0
        exp.runs.append(result)

    ctx = dict(baseline=baseline, ledger=ledger)
    exp.gates = [g(exp, **ctx) for g in gates]
    exp.promoted = not exp.blocked_by

    ledger.append(exp)
    return exp


def report(exp: Experiment, baseline: Experiment | None = None) -> str:
    """The human-facing proof-of-work block. This is what goes in the PR body."""
    lines = [
        f"experiment {exp.exp_id}  (parent: {exp.parent_id or 'root'})",
        f"  hypothesis : {exp.hypothesis}",
        f"  config     : {json.dumps(exp.config, sort_keys=True)}",
        f"  metric     : {exp.mean_metric:.4f} ± {exp.std_metric:.4f}  over {len(exp.runs)} seeds",
    ]
    if baseline is not None:
        lines.append(f"  vs baseline: {exp.mean_metric - baseline.mean_metric:+.4f}")
    lines.append("  gates:")
    for g in exp.gates:
        mark = "PASS" if g.passed else ("FAIL" if g.blocking else "WARN")
        lines.append(f"    [{mark}] {g.name}: {g.detail}")
    verdict = "PROMOTED" if exp.promoted else f"REJECTED ({', '.join(exp.blocked_by)})"
    lines.append(f"  verdict    : {verdict}")
    return "\n".join(lines)
