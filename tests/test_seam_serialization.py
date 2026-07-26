"""
Seam guarantees introduced by the queued refactor (handoff §5, candidates 1 & 2).

Two things the prior code left untested, both load-bearing once the runner is
assumed to sit behind a process boundary rather than an in-process Python call:

  1. A malformed run record must fail at Candidate construction, naming which
     record was bad — not as an AttributeError deep inside gate evaluation.
  2. A VerdictBundle must survive a JSON round-trip byte-for-byte in meaning,
     including the deterministic lane's NaN metric, and the ledger it lands in
     must be *valid* JSON that a non-Python reader can parse.
"""

from __future__ import annotations

import json
import sys
from math import isnan
from pathlib import Path

import pytest

from expfactory.harness import RunResult
from expfactory.verifier import (
    Candidate,
    ExitCodeVerifier,
    GateVerifier,
    Ledger,
    VerdictBundle,
)


def _run(seed: int, metric: float = 0.80) -> dict[str, object]:
    return dict(
        seed=seed,
        val_metric=metric,
        train_ids_hash="t",
        eval_ids_hash="e",
        overlap_count=0,
        wall_seconds=0.0,
    )


def _candidate(**over: object) -> Candidate:
    base = dict(
        hypothesis="h",
        config={"model": "logreg"},
        code_hash="abc123",
        runs=[_run(s) for s in range(5)],
        cost_usd=0.4,
    )
    base.update(over)
    return Candidate(**base)  # type: ignore[arg-type]


# ---- candidate 1: malformed data fails at the boundary ---------------------


def test_mapping_runs_are_normalised_to_runresult():
    """Dicts are accepted at the edge but stored as typed records, so no gate
    downstream ever has to know which form the caller used."""
    cand = _candidate()
    assert all(isinstance(r, RunResult) for r in cand.runs)


def test_runresult_instances_are_accepted_directly():
    cand = _candidate(runs=[RunResult(**_run(s)) for s in range(3)])  # type: ignore[arg-type]
    assert [r.seed for r in cand.runs] == [0, 1, 2]


def test_malformed_run_names_its_index():
    """The whole point of the change: the error says *which* record is bad."""
    bad = _run(1)
    del bad["val_metric"]
    with pytest.raises(TypeError, match=r"runs\[1\]"):
        _candidate(runs=[_run(0), bad, _run(2)])


def test_unknown_field_in_run_is_rejected_at_the_boundary():
    with pytest.raises(TypeError, match=r"runs\[0\]"):
        _candidate(runs=[{**_run(0), "nonsense": 1}])


def test_non_mapping_run_is_rejected_with_its_type():
    with pytest.raises(TypeError, match=r"runs\[0\].*got str"):
        _candidate(runs=["not a run"])


# ---- candidate 2: the bundle is a serialization contract -------------------


def test_bundle_round_trips_through_json():
    bundle = GateVerifier(id_factory=lambda: "fixed-id").run(_candidate())
    restored = VerdictBundle.from_json(bundle.to_json())
    assert restored == bundle


def test_deterministic_lane_nan_metric_survives_the_round_trip():
    """ExitCodeVerifier has no metric. NaN must come back as NaN, not None."""
    bundle = ExitCodeVerifier(command=[sys.executable, "-c", ""]).run(_candidate())
    assert isnan(bundle.mean_metric)
    assert isnan(VerdictBundle.from_json(bundle.to_json()).mean_metric)


def test_ledger_rows_are_strictly_valid_json(tmp_path: Path):
    """A bare NaN token is what Python emits by default and is *not* valid JSON.
    The ledger is read by whatever the runner happens to be written in, so it is
    encoded as null instead. Parsed here with strict mode to prove it."""
    led = Ledger(tmp_path / "l.jsonl")
    led.append(ExitCodeVerifier(command=[sys.executable, "-c", ""]).run(_candidate()))
    raw = (tmp_path / "l.jsonl").read_text().strip()
    assert "NaN" not in raw
    json.loads(raw, parse_constant=_reject_constant)  # raises on NaN/Infinity


def test_ledger_reconstructs_nan_metric_from_row(tmp_path: Path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(ExitCodeVerifier(command=[sys.executable, "-c", ""]).run(_candidate()))
    assert isnan(led.all()[0].mean_metric)


def _reject_constant(name: str) -> object:
    raise AssertionError(f"ledger emitted non-JSON constant {name!r}")


# ---- GH#12: a frozen verdict must not alias what it was built from ----------


def test_a_bundle_does_not_alias_the_experiment_it_was_built_from():
    """`from_experiment` passed `config=exp.config` straight through while
    `from_exit_code` copied it. So an empirical verdict shared a dict with the
    Experiment, and mutating that experiment afterwards silently rewrote a bundle
    which advertises itself as frozen."""
    from expfactory.harness import RunResult
    from expfactory.verifier import Candidate, GateVerifier

    runs = [RunResult(s, 0.8, "t", "e", 0, 0.0) for s in range(3)]
    candidate = Candidate(hypothesis="h", config={"lr": 0.01}, code_hash="c", runs=runs)
    experiment = candidate.experiment("e1")

    bundle = GateVerifier(id_factory=lambda: "e1").run(candidate)
    experiment.config["lr"] = 999
    experiment.config["injected"] = True

    assert bundle.config == {"lr": 0.01}


def test_both_named_constructors_copy_their_mappings():
    """The two lanes differed, which is how the aliasing went unnoticed: the
    deterministic one was already correct."""
    from expfactory.harness import RunResult
    from expfactory.verifier import Candidate, VerdictBundle

    runs = [RunResult(0, 0.8, "t", "e", 0, 0.0)]
    config = {"lr": 0.01}
    candidate = Candidate(hypothesis="h", config=config, code_hash="c", runs=runs)

    ci = VerdictBundle.from_exit_code("e1", candidate, ("pytest",), 0, "out", "err")
    config["lr"] = 999

    assert ci.config == {"lr": 0.01}
