"""
verifier — the plugin boundary (ticket 02).

The dispatcher sees exactly one contract: Verifier.run(candidate) -> VerdictBundle.
Whether the verdict came from the empirical gate harness or from a CI exit code is
invisible above this line. This is the seam that W-02 designed and everything
downstream (ledger, runner, review, ratchet) hangs off.

`promoted` is a derived, frozen property of the bundle. No caller can forge it.

The seam is assumed to be a *process* boundary, not an in-process Python call
(MAP.md, post-map note on W-08: the runner may not be Python). So a bundle must
round-trip through JSON without losing or silently altering a field — see
`to_dict`/`from_dict` and the NaN handling there.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from math import isnan
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# reuse the prototype's gate functions and run record unchanged
from expfactory.harness import (
    DEFAULT_GATES,
    Experiment,
    HoldoutSource,
    RunResult,
)
from expfactory.prereg import (
    PreregContext,
    Preregistration,
    gate_prereg_churn,
    gate_preregistration,
)

IdFactory = Callable[[], str]


def new_exp_id() -> str:
    """Default experiment id.

    Injected rather than called inline so a test, a replay, or a resumed run can
    pin the id *through the seam* instead of rewriting a bundle after the fact.
    """
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
# Candidate: what a caller submits for verification
# --------------------------------------------------------------------------- #


def _coerce_run(value: RunResult | Mapping[str, Any], index: int) -> RunResult:
    """Normalise one run record, naming the offending index if it is malformed.

    Callers at the edge (a train_fn, a JSON artifact from a subprocess) naturally
    produce mappings. They are accepted and converted exactly once, here, so that
    everything downstream of construction sees a typed RunResult. A bad record
    fails at this boundary with its index in the message, rather than as an
    AttributeError deep inside gate evaluation.
    """
    if isinstance(value, RunResult):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(
            f"Candidate.runs[{index}]: expected RunResult or mapping, got {type(value).__name__}"
        )
    try:
        return RunResult(**value)
    except TypeError as exc:
        raise TypeError(f"Candidate.runs[{index}]: {exc}") from exc


@dataclass(frozen=True)
class Candidate:
    hypothesis: str
    config: dict[str, Any]
    code_hash: str
    runs: Sequence[RunResult]
    cost_usd: float = 0.0
    parent_id: str | None = None
    diff: Any = None  # DiffEvidence | None; drives the tamper gate
    # Preregistration (N-02). `exploratory` runs are free and unlimited but can
    # never be promoted; a confirmatory run must cite a prereg filed beforehand.
    prereg_hash: str | None = None
    exploratory: bool = False

    def __post_init__(self) -> None:
        # frozen dataclass: normalise through object.__setattr__, exactly once
        object.__setattr__(self, "runs", tuple(_coerce_run(r, i) for i, r in enumerate(self.runs)))

    def experiment(self, exp_id: str) -> Experiment:
        """Project this candidate into the harness's Experiment record."""
        exp = Experiment(
            exp_id=exp_id,
            parent_id=self.parent_id,
            hypothesis=self.hypothesis,
            config=dict(self.config),
            code_hash=self.code_hash,
            cost_usd=self.cost_usd,
        )
        exp.runs = list(self.runs)
        return exp


# --------------------------------------------------------------------------- #
# VerdictBundle: what every verifier returns (frozen — promotion cannot be forged)
# --------------------------------------------------------------------------- #


def _mean_metrics(runs: Sequence[RunResult]) -> dict[str, float]:
    """Mean of every metric reported across runs, keyed by name.

    A metric missing from some runs is averaged over the runs that reported it;
    the gate separately requires a declared metric to be present in all of them,
    so a partial average can never satisfy a preregistration by accident.
    """
    totals: dict[str, list[float]] = {}
    for r in runs:
        totals.setdefault("val_metric", []).append(r.val_metric)
        for key, value in r.extra.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals.setdefault(key, []).append(float(value))
    return {k: sum(v) / len(v) for k, v in totals.items()}


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
    # Which preregistration governed this verdict, if any. Recorded so the ledger
    # row remains self-contained, and so G-08 can tell which filed rules never
    # produced a promotion.
    prereg_hash: str | None = None
    # Mean of every metric the runs reported, primary and otherwise. Without this
    # the row cannot answer "what did this score on latency", and a guardrail
    # regression cannot be measured against a parent at all.
    metrics: dict[str, float] = field(default_factory=dict)

    # -- named constructors: one per lane, so neither verifier hand-rolls the shape

    @classmethod
    def from_experiment(cls, exp: Experiment, prereg_hash: str | None = None) -> VerdictBundle:
        """Build the bundle for an adjudicated experiment.

        `promoted` is derived here from the gate results, and this is the only
        place it is decided for the empirical lane.
        """
        return cls(
            exp_id=exp.exp_id,
            promoted=not exp.blocked_by,  # derived, never set
            blocked_by=tuple(exp.blocked_by),
            config=exp.config,
            code_hash=exp.code_hash,
            seeds=tuple(r.seed for r in exp.runs),
            gate_names=tuple(g.name for g in exp.gates),
            mean_metric=exp.mean_metric,
            cost_usd=exp.cost_usd,
            prereg_hash=prereg_hash,
            metrics=_mean_metrics(exp.runs),
            artifact={
                "exp_id": exp.exp_id,
                "hypothesis": exp.hypothesis,
                "gates": [
                    {"name": g.name, "passed": g.passed, "detail": g.detail} for g in exp.gates
                ],
            },
        )

    @classmethod
    def from_exit_code(
        cls,
        exp_id: str,
        candidate: Candidate,
        command: Sequence[str],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> VerdictBundle:
        """Build the bundle for a deterministic-lane (CI) verdict."""
        promoted = returncode == 0
        return cls(
            exp_id=exp_id,
            promoted=promoted,
            blocked_by=() if promoted else (f"exit_{returncode}",),
            config=dict(candidate.config),
            code_hash=candidate.code_hash,
            seeds=tuple(r.seed for r in candidate.runs),
            gate_names=("ci_exit_code",),
            mean_metric=float("nan"),  # no metric in the deterministic lane
            cost_usd=candidate.cost_usd,
            artifact={
                "exp_id": exp_id,
                "command": list(command),
                "returncode": returncode,
                "stdout_tail": stdout[-500:],
                "stderr_tail": stderr[-500:],
            },
        )

    # -- serialization: the seam may be a subprocess/artifact-file boundary -----

    def to_dict(self) -> dict[str, Any]:
        """Plain-JSON form.

        `mean_metric` is NaN in the deterministic lane. Bare `NaN` is not valid
        JSON — Python emits and re-reads it happily, but any non-Python reader of
        the ledger would choke. It is encoded as null and restored by `from_dict`.
        """
        d = asdict(self)
        if isnan(self.mean_metric):
            d["mean_metric"] = None
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> VerdictBundle:
        row = dict(d)
        row["blocked_by"] = tuple(row["blocked_by"])
        row["seeds"] = tuple(row["seeds"])
        row["gate_names"] = tuple(row["gate_names"])
        if row.get("mean_metric") is None:
            row["mean_metric"] = float("nan")
        row.setdefault("prereg_hash", None)  # rows written before G-07 existed
        row.setdefault("metrics", {})
        return cls(**row)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> VerdictBundle:
        return cls.from_dict(json.loads(s))


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #


@runtime_checkable
class Verifier(Protocol):
    def run(self, candidate: Candidate) -> VerdictBundle: ...


# --------------------------------------------------------------------------- #
# Implementation 1: empirical gate harness
# --------------------------------------------------------------------------- #


@runtime_checkable
class PreregStore(Protocol):
    """The slice of the ledger G-07 needs. `Ledger` satisfies it.

    Kept separate from `ledger_ctx` rather than overloading it: `ledger_ctx` is
    passed to every gate and the holdout gate expects a different shape, so one
    parameter cannot serve both without the gates having to guess.
    """

    def prereg_hashes(self) -> frozenset[str]: ...
    def get_prereg(self, prereg_hash: str) -> Preregistration | None: ...
    def non_promoting_prereg_count(self, parent_id: str | None) -> int: ...
    def get_verdict_metric(self, exp_id: str) -> float | None: ...
    def get_verdict_metrics(self, exp_id: str) -> dict[str, float]: ...
    def position_of_prereg(self, prereg_hash: str) -> int | None: ...
    def position_of_verdict(self, exp_id: str) -> int | None: ...


class GateVerifier:
    """Wraps the prototype's gate set behind the plugin boundary."""

    def __init__(
        self,
        gates: Sequence[Callable[..., Any]] = DEFAULT_GATES,
        baseline: Experiment | None = None,
        ledger_ctx: HoldoutSource | None = None,
        id_factory: IdFactory = new_exp_id,
        require_prereg: bool = False,
        prereg_store: PreregStore | None = None,
    ) -> None:
        """
        `require_prereg` turns G-07 on. It is off by default because this same
        gate set adjudicates one-off candidates that have no hill-climb lineage —
        the adversarial fixtures among them — and requiring a preregistration
        there would reject everything and destroy their diagnostic value.

        **The hill-climb runner must construct this with require_prereg=True.**
        That is the production configuration; see docs/SPEC.md §6. This is a
        workflow switch, not a security toggle that may be left off.
        """
        self._gates = gates
        self._baseline = baseline
        self._ledger_ctx = ledger_ctx
        self._id_factory = id_factory
        self._require_prereg = require_prereg
        self._prereg_store = prereg_store

    def _prereg_ctx(self, candidate: Candidate, exp_id: str) -> PreregContext:
        store = self._prereg_store
        cited = candidate.prereg_hash
        record = store.get_prereg(cited) if (store and cited) else None
        return PreregContext(
            prereg=record,
            exploratory=candidate.exploratory,
            filed_hashes=store.prereg_hashes() if store else frozenset(),
            cited_hash=cited,
            lineage_attempts=(
                store.non_promoting_prereg_count(candidate.parent_id) if store else 0
            ),
            # Read from the ledger, deliberately not from the prereg: the whole
            # point of rule 8 is that the agent does not get to supply this.
            parent_metrics=(
                store.get_verdict_metrics(record.parent_id)
                if (store and record is not None and record.parent_id)
                else {}
            ),
            prereg_position=(store.position_of_prereg(cited) if (store and cited) else None),
            verdict_position=(store.position_of_verdict(exp_id) if store else None),
        )

    def run(self, candidate: Candidate) -> VerdictBundle:
        exp = candidate.experiment(self._id_factory())
        ctx = dict(baseline=self._baseline, ledger=self._ledger_ctx)
        exp.gates = [g(exp, **ctx) for g in self._gates]
        # G-07 (M2-04). Runs before the diff gates so a run that had no business
        # being promoted is rejected on its claim, not only on its diff.
        if self._require_prereg:
            prereg_ctx = self._prereg_ctx(candidate, exp.exp_id)
            exp.gates.append(gate_preregistration(exp, prereg_ctx=prereg_ctx))
            # G-08 sees across preregistrations, which G-07 structurally cannot.
            exp.gates.append(gate_prereg_churn(exp, prereg_ctx=prereg_ctx))
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
        return VerdictBundle.from_experiment(exp, prereg_hash=candidate.prereg_hash)


# --------------------------------------------------------------------------- #
# Implementation 2: deterministic CI adapter (proves the seam admits two impls)
# --------------------------------------------------------------------------- #


class ExitCodeVerifier:
    """Shells out to a command; exit 0 -> promoted. The deterministic lane's
    verifier, satisfying the same contract as the empirical one. Per W-02 this
    exists to prove the interface holds two implementations, even when no v1
    workload drives it."""

    def __init__(
        self,
        command: Sequence[str],
        id_factory: IdFactory = new_exp_id,
    ) -> None:
        self._command = list(command)
        self._id_factory = id_factory

    def run(self, candidate: Candidate) -> VerdictBundle:
        proc = subprocess.run(self._command, capture_output=True, text=True)
        return VerdictBundle.from_exit_code(
            exp_id=self._id_factory(),
            candidate=candidate,
            command=self._command,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )


# --------------------------------------------------------------------------- #
# Ledger: append-only, reconstructs from row alone
# --------------------------------------------------------------------------- #

KIND_VERDICT = "verdict"
KIND_PREREG = "prereg"


@dataclass(frozen=True)
class LedgerRow:
    position: int
    kind: str
    payload: VerdictBundle | Preregistration


class Ledger:
    """Append-only log holding both verdicts and preregistrations.

    Both kinds share **one** log deliberately (ticket N-01). G-07 proves a
    preregistration preceded its run, and positions cannot be compared across two
    files. Ordering is therefore part of the contract, not an accident of how
    JSONL happens to read:

        position == line index, and lines are only ever appended.

    **Single-writer is assumed.** Two processes appending to one path can
    interleave partial lines, which would corrupt both the ordering guarantee and
    the rows themselves. The runner owns the ledger; agents submit through it and
    never hold it open. If that ever stops being true, G-07's ordering proof stops
    being sound and needs a different mechanism (hash chaining).

    Rows are wrapped as {"kind": ..., "row": {...}}. A bare, unwrapped object is
    read as a verdict, so ledgers written before preregistration existed still
    load.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    # -- writing -----------------------------------------------------------

    def append(self, bundle: VerdictBundle) -> None:
        self._write(KIND_VERDICT, bundle.to_dict())

    def append_prereg(self, prereg: Preregistration) -> None:
        """File a preregistration. Must happen before the run it governs."""
        self._write(KIND_PREREG, prereg.to_dict())

    def _write(self, kind: str, row: dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"kind": kind, "row": row}, sort_keys=True) + "\n")

    # -- reading -----------------------------------------------------------

    def rows(self) -> list[LedgerRow]:
        """Every row, in append order, with its position."""
        out: list[LedgerRow] = []
        for position, line in enumerate(self.path.read_text().splitlines()):
            if not line.strip():
                continue
            raw = json.loads(line)
            if "kind" not in raw:  # pre-preregistration ledger: a bare verdict
                out.append(LedgerRow(position, KIND_VERDICT, VerdictBundle.from_dict(raw)))
                continue
            payload = (
                Preregistration.from_dict(raw["row"])
                if raw["kind"] == KIND_PREREG
                else VerdictBundle.from_dict(raw["row"])
            )
            out.append(LedgerRow(position, raw["kind"], payload))
        return out

    def all(self) -> list[VerdictBundle]:
        """Verdict rows only, in order."""
        return [r.payload for r in self.rows() if isinstance(r.payload, VerdictBundle)]

    def preregs(self) -> list[Preregistration]:
        return [r.payload for r in self.rows() if isinstance(r.payload, Preregistration)]

    # -- the PreregStore contract G-07 depends on ---------------------------

    def prereg_hashes(self) -> frozenset[str]:
        """Hashes already filed. Membership is G-07's anti-HARKing proof: the log
        is append-only and a verdict is written *after* verification, so anything
        in here necessarily precedes the run being judged."""
        return frozenset(p.hash for p in self.preregs())

    def get_prereg(self, prereg_hash: str) -> Preregistration | None:
        for p in self.preregs():
            if p.hash == prereg_hash:
                return p
        return None

    def non_promoting_prereg_count(self, parent_id: str | None) -> int:
        """Preregistrations filed under `parent_id` that no promoted verdict cites.

        Counts the current attempt too: its prereg is already filed (G-07 requires
        that) and has not promoted yet, so a lineage on its fourth try reports 4.
        """
        if parent_id is None:
            # No lineage, nothing to count. Lumping every parentless prereg into a
            # single bucket would block unrelated root experiments as "shopping".
            return 0
        rows = self.rows()
        promoted = {
            r.payload.prereg_hash
            for r in rows
            if isinstance(r.payload, VerdictBundle) and r.payload.promoted and r.payload.prereg_hash
        }
        return sum(
            1
            for r in rows
            if isinstance(r.payload, Preregistration)
            and r.payload.parent_id == parent_id
            and r.payload.hash not in promoted
        )

    def get_verdict_metric(self, exp_id: str) -> float | None:
        """The metric an experiment actually recorded. Rule 8's source of truth.

        Returns the most recent verdict for `exp_id`; None if it never landed one.
        """
        for row in reversed(self.rows()):
            if isinstance(row.payload, VerdictBundle) and row.payload.exp_id == exp_id:
                metric = row.payload.mean_metric
                return None if isnan(metric) else metric
        return None

    def get_verdict_metrics(self, exp_id: str) -> dict[str, float]:
        """Every metric the experiment recorded.

        Guardrail thresholds come from here, never from the preregistration —
        an agent-named threshold is decorative, the same defect rule 8 closed
        for the baseline.
        """
        for row in reversed(self.rows()):
            if isinstance(row.payload, VerdictBundle) and row.payload.exp_id == exp_id:
                return dict(row.payload.metrics)
        return {}

    def position_of_verdict(self, exp_id: str) -> int | None:
        """Where this experiment's verdict sits, or None if never recorded.

        Paired with position_of_prereg, this is what turns rule 2 from an
        assumption about append order into a comparison.
        """
        for row in self.rows():
            if isinstance(row.payload, VerdictBundle) and row.payload.exp_id == exp_id:
                return row.position
        return None

    def position_of_prereg(self, prereg_hash: str) -> int | None:
        for row in self.rows():
            if isinstance(row.payload, Preregistration) and row.payload.hash == prereg_hash:
                return row.position
        return None
