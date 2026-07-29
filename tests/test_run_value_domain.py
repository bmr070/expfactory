"""
BRE-28 — the value domain of a run record, refused at the construction boundary.

`Candidate.__post_init__` normalised every run through `_coerce_run`, which
validated SHAPE and never VALUES. A `RunResult` carrying `float("inf")` is
perfectly well-formed, so it reached the gate set intact — and the gate set
promoted it, because every comparison against NaN is false and the dominance
arithmetic on two infinities produces NaN. Nothing rejected it; promotion
happened by the absence of a rejection.

The fixtures below are adversarial in the same sense as `adversarial_suite`'s:
each is a record with a pre-assigned verdict. The difference is that this
verdict is REFUSAL rather than rejection, so they cannot be `Fixture` objects —
a `Fixture` holds a `Candidate`, and the whole point is that these candidates
cannot be constructed at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from expfactory.adversarial_suite import Kind, build_suite
from expfactory.harness import RunResult
from expfactory.prereg import Guardrail, Preregistration
from expfactory.verifier import Candidate, GateVerifier

_WELL_FORMED: dict[str, Any] = {
    "seed": 0,
    "val_metric": 0.80,
    "train_ids_hash": "t",
    "eval_ids_hash": "e",
    "overlap_count": 0,
    "wall_seconds": 1.5,
}


def _run(**over: Any) -> dict[str, Any]:
    """A record that differs from a good one only in the fields named."""
    return {**_WELL_FORMED, **over}


def _candidate(runs: list[Any], **over: Any) -> Candidate:
    return Candidate(hypothesis="fx", config={}, code_hash="x", runs=runs, **over)


# ---- the fixture table ------------------------------------------------------
#
# (id, record, exception class, the substring the message MUST name). The last
# column is load-bearing: an error that does not say which field of which run is
# wrong sends a reader back into gate evaluation to find out, which is the
# failure mode `_coerce_run`'s indexed messages already exist to prevent.

_REFUSED_RUNS: tuple[tuple[str, dict[str, Any], type[Exception], str], ...] = (
    # Non-finite primary metric. `nan` and both infinities, separately: they
    # take different paths through the dominance arithmetic and only `inf`
    # reproduces the review's promotion.
    ("nan-metric", _run(val_metric=float("nan")), ValueError, "val_metric"),
    ("pos-inf-metric", _run(val_metric=float("inf")), ValueError, "val_metric"),
    ("neg-inf-metric", _run(val_metric=float("-inf")), ValueError, "val_metric"),
    # Non-finite secondary metric. `_mean_metrics` averages every numeric entry
    # in `extra` into the verdict, and a guardrail may be measured on one, so an
    # infinity here is the same defect one field over.
    ("nan-extra-metric", _run(extra={"latency_ms": float("nan")}), ValueError, "latency_ms"),
    ("inf-extra-metric", _run(extra={"latency_ms": float("inf")}), ValueError, "latency_ms"),
    # Duration.
    ("nan-duration", _run(wall_seconds=float("nan")), ValueError, "wall_seconds"),
    ("inf-duration", _run(wall_seconds=float("inf")), ValueError, "wall_seconds"),
    ("negative-duration", _run(wall_seconds=-1.0), ValueError, "wall_seconds"),
    # Overlap count: integral and not below zero. A negative overlap would
    # cancel a real leak out of `gate_no_leakage`'s sum across runs.
    ("negative-overlap", _run(overlap_count=-3), ValueError, "overlap_count"),
    ("fractional-overlap", _run(overlap_count=2.5), TypeError, "overlap_count"),
    # bool is an int subclass, so it clears every numeric test and would be
    # averaged into the ledger as 1.0.
    ("bool-metric", _run(val_metric=True), TypeError, "val_metric"),
    ("bool-overlap", _run(overlap_count=True), TypeError, "overlap_count"),
    ("string-metric", _run(val_metric="0.9"), TypeError, "val_metric"),
)


@pytest.mark.parametrize(
    ("record", "error", "field_name"),
    [(r, e, f) for _, r, e, f in _REFUSED_RUNS],
    ids=[i for i, _, _, _ in _REFUSED_RUNS],
)
def test_malformed_values_are_refused_naming_the_run(
    record: dict[str, Any], error: type[Exception], field_name: str
) -> None:
    """Refuse, do not sanitize — and name the offending run index."""
    with pytest.raises(error) as exc:
        _candidate([_run(), record, _run(seed=2)])
    message = str(exc.value)
    assert "runs[1]" in message, message
    assert field_name in message, message


@pytest.mark.parametrize(
    ("record", "error", "field_name"),
    [(r, e, f) for _, r, e, f in _REFUSED_RUNS],
    ids=[i for i, _, _, _ in _REFUSED_RUNS],
)
def test_refusal_applies_to_already_typed_run_results(
    record: dict[str, Any], error: type[Exception], field_name: str
) -> None:
    """A `RunResult` handed over already typed is validated too.

    `RunResult` is a plain dataclass and enforces nothing at runtime, so every
    record in the table above constructs happily as one. Waving typed records
    through would leave the hole open for every caller that builds `RunResult`
    itself — which is `pipeline.run_and_record`, the one entry the runner uses.
    """
    with pytest.raises(error) as exc:
        _candidate([RunResult(**record)])
    assert "runs[0]" in str(exc.value)
    assert field_name in str(exc.value)


@pytest.mark.parametrize(
    ("cost", "error"),
    [
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        (float("-inf"), ValueError),
        (-0.01, ValueError),
        ("free", TypeError),
    ],
    ids=["nan", "pos-inf", "neg-inf", "negative", "string"],
)
def test_malformed_cost_is_refused(cost: Any, error: type[Exception]) -> None:
    """`gate_cost` asks `cost_usd <= max_usd`, which is True for -inf and for
    every negative number, so the gate reports a clean check on spend that was
    never really measured."""
    with pytest.raises(error) as exc:
        _candidate([_run(seed=s) for s in range(3)], cost_usd=cost)
    assert "Candidate.cost_usd" in str(exc.value)


# ---- the regression: the review's exact scenario ----------------------------


def test_three_inf_runs_are_refused_at_construction_not_promoted() -> None:
    """The reproduction from the external review, verbatim.

    At HEAD this constructed cleanly, reached `GateVerifier`, and came back
    `promoted=True` with `mean_metric=inf`: dominance arithmetic went to NaN,
    every NaN comparison was false, and no gate rejected. The candidate must now
    fail to exist rather than fail a gate — a gate that rejects it would still
    have had to be reached through arithmetic that NaN silently defeats.
    """
    runs = [_run(seed=s, val_metric=float("inf")) for s in range(3)]

    with pytest.raises(ValueError) as exc:
        _candidate(runs, cost_usd=0.1)

    assert "Candidate.runs[0].val_metric" in str(exc.value)
    assert "finite" in str(exc.value)


def test_the_same_three_runs_with_a_finite_metric_still_reach_the_verifier() -> None:
    """Positive control for the regression above.

    Without it the test would still pass if construction refused everything, and
    a boundary that refuses all input is not stricter, it is broken — the exact
    shape of the 2026-07-27 dominance bug.
    """
    bundle = GateVerifier().run(
        _candidate([_run(seed=s, val_metric=0.80) for s in range(3)], cost_usd=0.1)
    )
    assert bundle.promoted is True
    assert bundle.mean_metric == pytest.approx(0.80)


def test_a_good_candidate_is_unaffected() -> None:
    """Zero duration, zero cost and zero overlap are all legal values."""
    candidate = _candidate(
        [
            _run(seed=s, wall_seconds=0.0, extra={"latency_ms": 12.0, "note": "ok"})
            for s in range(3)
        ],
        cost_usd=0.0,
    )
    assert len(candidate.runs) == 3
    assert candidate.runs[0].extra["note"] == "ok"


# ---- preregistration and guardrail parameters -------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_baseline_is_refused(bad: float) -> None:
    """A NaN baseline defeats G-07 twice, both times silently.

    Rule 8 asks `abs(parent - declared) > BASELINE_TOLERANCE`, which is False
    against NaN, so the forged-baseline check reports agreement with a parent it
    never compared to. Rule 4 then asks `effect < minimum_effect` on the NaN
    effect that follows, which is also False, so the run promotes.
    """
    with pytest.raises(ValueError, match="baseline_value must be finite"):
        Preregistration(
            primary_metric="val_metric",
            direction="maximize",
            baseline_value=bad,
            minimum_effect=0.02,
            seeds=(0, 1, 2),
            parent_id="p",
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_minimum_effect_is_refused(bad: float) -> None:
    """`nan < 0` is False, so the existing magnitude check passes NaN through."""
    with pytest.raises(ValueError, match="minimum_effect must be finite"):
        Preregistration(
            primary_metric="val_metric",
            direction="maximize",
            baseline_value=0.70,
            minimum_effect=bad,
            seeds=(0, 1, 2),
            parent_id="p",
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_guardrail_tolerance_is_refused(bad: float) -> None:
    """A NaN tolerance makes `regressed` return False for every comparison: a
    guardrail that is filed, hashed and recorded, and can never block."""
    with pytest.raises(ValueError, match="tolerance must be finite"):
        Guardrail("latency_ms", "minimize", tolerance=bad)


def test_finite_guardrail_tolerance_still_accepted() -> None:
    guard = Guardrail("latency_ms", "minimize", tolerance=0.5)
    assert guard.regressed(observed=2.0, parent=1.0) is True
    assert guard.regressed(observed=1.4, parent=1.0) is False


# ---- duplicate seeds: adjudicated, not refused ------------------------------


def test_duplicate_seeds_are_rejected_by_the_gate_not_the_boundary() -> None:
    """Duplicate seeds stay constructible on purpose.

    `gate_reproducible` detects unseeded augmentation by comparing two runs of
    the *same* seed, so refusing duplicates at the boundary would delete the
    gate's only input. The refusal belongs to the gate, and the rejection must
    trace to that gate alone.
    """
    fixture = next(f for f in build_suite().visible_fixtures() if f.id == "v-repeat-1")
    bundle = GateVerifier().run(fixture.candidate)
    assert bundle.promoted is False
    assert bundle.blocked_by == ("reproducible",), bundle.blocked_by


def test_duplicate_seed_fixture_is_in_the_visible_suite() -> None:
    """Invariant 4: every gate traces to a fixture. `gate_reproducible` had none."""
    kinds = {f.kind for f in build_suite().visible_fixtures()}
    assert Kind.NONDETERMINISM in kinds
