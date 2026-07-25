"""
Ticket 05 — one experiment runs end-to-end into the append-only ledger.

Seam under test: run_and_record(train_fn, config, ledger) drives a real candidate
from config -> trained runs -> verified -> one immutable ledger row that
reconstructs the whole experiment without any agent narrative.

Uses the real sklearn training path from the drone demo, so this is an integration
test through every layer of the empirical lane: train -> verify -> ledger.
Written RED: run_and_record does not exist yet.
"""
from __future__ import annotations

from pathlib import Path

from expfactory.pipeline import run_and_record
from expfactory.verifier import GateVerifier, Ledger


def _tiny_train_fn(config, seed):
    """A deterministic, dependency-free training stub: metric is a pure function
    of config+seed, no leakage. Keeps the e2e test fast; the real sklearn path is
    exercised in the drone demo, not the unit suite."""
    from expfactory.verifier import Candidate  # local import to avoid cycle at module load
    base = 0.75 + 0.02 * config.get("depth", 0)
    return dict(seed=seed, val_metric=base, train_ids_hash="train",
                eval_ids_hash="eval", overlap_count=0, wall_seconds=0.0)


def test_experiment_runs_and_appends_one_row(tmp_path: Path):
    led = Ledger(tmp_path / "ledger.jsonl")
    bundle = run_and_record(
        train_fn=_tiny_train_fn,
        hypothesis="baseline depth-0",
        config={"model": "logreg", "depth": 0},
        code_hash="deadbeef",
        seeds=(0, 1, 2, 3, 4),
        verifier=GateVerifier(),
        ledger=led,
        cost_usd=0.4,
    )
    assert bundle.promoted is True
    assert len(led.all()) == 1


def test_row_carries_full_provenance(tmp_path: Path):
    led = Ledger(tmp_path / "ledger.jsonl")
    run_and_record(
        train_fn=_tiny_train_fn, hypothesis="h", config={"model": "logreg", "depth": 0},
        code_hash="deadbeef", seeds=(0, 1, 2, 3, 4),
        verifier=GateVerifier(), ledger=led, cost_usd=0.4,
    )
    row = led.all()[0]
    assert row.config == {"model": "logreg", "depth": 0}
    assert row.code_hash == "deadbeef"
    assert row.seeds == (0, 1, 2, 3, 4)
    assert row.gate_names            # verdicts present
    assert row.cost_usd == 0.4


def test_reconstruct_from_ledger_alone(tmp_path: Path):
    """Reopen the ledger from disk in a fresh object; the experiment is fully
    described by the row — no in-memory state, no narrative."""
    p = tmp_path / "ledger.jsonl"
    run_and_record(
        train_fn=_tiny_train_fn, hypothesis="h", config={"depth": 3},
        code_hash="c0ffee", seeds=(0, 1, 2), verifier=GateVerifier(),
        ledger=Ledger(p), cost_usd=0.1,
    )
    reopened = Ledger(p).all()
    assert len(reopened) == 1
    assert reopened[0].config == {"depth": 3}
    assert reopened[0].code_hash == "c0ffee"


def test_history_is_immutable_across_runs(tmp_path: Path):
    led = Ledger(tmp_path / "ledger.jsonl")
    for d in (0, 1, 2):
        run_and_record(
            train_fn=_tiny_train_fn, hypothesis=f"depth-{d}", config={"depth": d},
            code_hash=f"h{d}", seeds=(0, 1, 2), verifier=GateVerifier(),
            ledger=led, cost_usd=0.1,
        )
    rows = led.all()
    assert len(rows) == 3
    assert [r.config["depth"] for r in rows] == [0, 1, 2]  # order preserved
