"""
Ticket 02 — seam tests for the verifier plugin interface.

Seams under test (confirmed before writing):
  1. Verifier.run(candidate) -> VerdictBundle          [the plugin boundary]
  2. Ledger append-only + reconstruct-from-row          [durability behavior]
  3. promoted is derived, never settable by a caller     [anti-forgery invariant]
  4. two implementations satisfy one interface           [substitutability]

Written RED: the prototype has no Verifier protocol and no deterministic adapter yet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from expfactory.verifier import (
    Candidate,
    ExitCodeVerifier,
    GateVerifier,
    Ledger,
    VerdictBundle,
    Verifier,
)

# ---- seam 1: the plugin boundary -------------------------------------------


def test_verifier_run_returns_a_verdict_bundle():
    """The one contract the dispatcher sees: give a candidate, get a bundle
    with a boolean verdict and an artifact record. Nothing about gates leaks."""
    v = GateVerifier()
    cand = Candidate(
        hypothesis="baseline",
        config={"model": "logreg"},
        code_hash="abc123",
        # a trivially good candidate: 5 identical seeds, no leakage
        runs=[
            dict(
                seed=s,
                val_metric=0.80,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=0,
                wall_seconds=0.0,
            )
            for s in range(5)
        ],
        cost_usd=0.4,
    )
    bundle = v.run(cand)
    assert isinstance(bundle, VerdictBundle)
    assert isinstance(bundle.promoted, bool)
    assert bundle.artifact is not None


def test_gate_verifier_rejects_leakage():
    """Behavior, not implementation: a candidate with train/eval overlap must
    not be promoted, whatever the metric says."""
    v = GateVerifier()
    cand = Candidate(
        hypothesis="leaky",
        config={},
        code_hash="x",
        runs=[
            dict(
                seed=s,
                val_metric=0.99,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=17,
                wall_seconds=0.0,
            )
            for s in range(5)
        ],
        cost_usd=0.1,
    )
    bundle = v.run(cand)
    assert bundle.promoted is False
    assert "no_leakage" in bundle.blocked_by


# ---- seam 2: append-only ledger + reconstruction ---------------------------


def test_ledger_is_append_only(tmp_path: Path):
    led = Ledger(tmp_path / "l.jsonl")
    b1 = _promoted_bundle("exp1")
    b2 = _promoted_bundle("exp2")
    led.append(b1)
    led.append(b2)
    # history preserved in order, nothing overwritten
    rows = led.all()
    assert [r.exp_id for r in rows] == ["exp1", "exp2"]


def test_promoted_experiment_reconstructs_from_row_alone(tmp_path: Path):
    """No agent narrative needed: config, code hash, seeds, verdicts all present."""
    led = Ledger(tmp_path / "l.jsonl")
    led.append(_promoted_bundle("exp1"))
    row = led.all()[0]
    assert row.config == {"model": "logreg"}
    assert row.code_hash == "abc123"
    assert len(row.seeds) == 5
    assert row.gate_names  # verdicts recorded


# ---- seam 3: promoted is derived, never forged -----------------------------


def test_caller_cannot_forge_promotion():
    """A caller must not be able to hand-set promoted=True on a failing candidate."""
    v = GateVerifier()
    leaky = Candidate(
        hypothesis="forge",
        config={},
        code_hash="x",
        runs=[
            dict(
                seed=s,
                val_metric=0.99,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=5,
                wall_seconds=0.0,
            )
            for s in range(5)
        ],
        cost_usd=0.1,
    )
    bundle = v.run(leaky)
    # even if a caller mutates the field, it should not be honored as a pass —
    # promotion is a property of the verdict, and VerdictBundle exposes no setter
    with pytest.raises((AttributeError, Exception)):
        bundle.promoted = True  # frozen


# ---- seam 4: two implementations, one interface ----------------------------


def test_gate_and_exitcode_verifiers_are_substitutable():
    """The deterministic CI adapter satisfies the same Verifier contract as the
    empirical gate verifier. This is the W-02 'prove the seam admits two impls'."""
    for v in (GateVerifier(), ExitCodeVerifier(command=_ok_cmd())):
        assert isinstance(v, Verifier)
        bundle = v.run(_trivial_good_candidate())
        assert isinstance(bundle, VerdictBundle)
        assert isinstance(bundle.promoted, bool)


def test_exitcode_verifier_maps_exit_code_to_verdict():
    passing = ExitCodeVerifier(command=_ok_cmd()).run(_trivial_good_candidate())
    failing = ExitCodeVerifier(command=_fail_cmd()).run(_trivial_good_candidate())
    assert passing.promoted is True
    assert failing.promoted is False


# ---- helpers ---------------------------------------------------------------


def _trivial_good_candidate() -> Candidate:
    return Candidate(
        hypothesis="baseline",
        config={"model": "logreg"},
        code_hash="abc123",
        runs=[
            dict(
                seed=s,
                val_metric=0.80,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=0,
                wall_seconds=0.0,
            )
            for s in range(5)
        ],
        cost_usd=0.4,
    )


def _promoted_bundle(exp_id: str) -> VerdictBundle:
    # Pin the id through the verifier's seam rather than rewriting the bundle
    # afterwards: a VerdictBundle is frozen precisely so that nothing edits a
    # verdict once it has been reached.
    v = GateVerifier(id_factory=lambda: exp_id)
    return v.run(_trivial_good_candidate())


def _ok_cmd() -> list[str]:
    """Exit-0 command that exists on every platform (unlike coreutils `true`)."""
    return [sys.executable, "-c", ""]


def _fail_cmd() -> list[str]:
    return [sys.executable, "-c", "raise SystemExit(1)"]
