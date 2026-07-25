"""
Fix for the code-review finding: the tamper gate was unreachable from the
verification path. These tests assert it fires THROUGH the Verifier boundary and
through run_and_record — i.e. a candidate that reports a great metric but tampers
with its tests is BLOCKED by the same call the runner makes.

Written RED: Candidate carries no diff today, and GateVerifier never consults one.
"""
from __future__ import annotations

from pathlib import Path

from verifier import Candidate, GateVerifier, Ledger
from gates_v1 import DiffEvidence
from pipeline import run_and_record


def _good_runs():
    return [dict(seed=s, val_metric=0.95, train_ids_hash="t", eval_ids_hash="e",
                 overlap_count=0, wall_seconds=0.0) for s in range(5)]


def test_tampering_candidate_blocked_through_verifier():
    """Great metric, no leakage, deterministic — but the diff removed an assertion.
    The verifier must block it. Before this fix, it promoted."""
    v = GateVerifier()
    cand = Candidate(
        hypothesis="green by cheating", config={}, code_hash="x",
        runs=_good_runs(), cost_usd=0.1,
        diff=DiffEvidence(added_lines=[], removed_lines=["    assert ap > 0.7"],
                          touched_paths=["test_model.py"]),
    )
    bundle = v.run(cand)
    assert bundle.promoted is False
    assert "no_test_tampering" in bundle.blocked_by


def test_clean_candidate_still_promotes_through_verifier():
    """A candidate with a clean diff is unaffected — no false positive."""
    v = GateVerifier()
    cand = Candidate(
        hypothesis="honest", config={}, code_hash="x",
        runs=_good_runs(), cost_usd=0.1,
        diff=DiffEvidence(added_lines=["    assert new_case()"], removed_lines=[],
                          touched_paths=["test_model.py"]),
    )
    assert v.run(cand).promoted is True


def test_candidate_without_diff_still_works():
    """Backward compatible: a candidate with no diff skips the tamper gate rather
    than crashing. (The runner always supplies one; older callers may not.)"""
    v = GateVerifier()
    cand = Candidate(hypothesis="no diff", config={}, code_hash="x",
                     runs=_good_runs(), cost_usd=0.1)
    assert v.run(cand).promoted is True


def test_tampering_blocked_end_to_end_through_pipeline(tmp_path: Path):
    """The full runner path: train -> verify -> ledger. A tampering experiment
    lands in the ledger as NOT promoted, with the reason recorded."""
    led = Ledger(tmp_path / "l.jsonl")

    def train_fn(cfg, seed):
        return dict(seed=seed, val_metric=0.95, train_ids_hash="t",
                    eval_ids_hash="e", overlap_count=0, wall_seconds=0.0)

    bundle = run_and_record(
        train_fn=train_fn, hypothesis="cheat", config={}, code_hash="x",
        seeds=(0, 1, 2, 3, 4), verifier=GateVerifier(), ledger=led, cost_usd=0.1,
        diff=DiffEvidence(added_lines=["@pytest.mark.skip"], removed_lines=[],
                          touched_paths=["test_model.py"]),
    )
    assert bundle.promoted is False
    assert "no_test_tampering" in bundle.blocked_by
    assert led.all()[0].promoted is False   # recorded, immutable
