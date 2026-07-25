"""
G-07 fixtures (ticket N-03).

Written before trusting the gate, per the standing rule that every gate traces to
a fixture — and because the dominance gate was wrong on first implementation and
passed nothing until a fixture caught it. Expect the same here.

Each test is one documented fooling mode. The headline case is `metric swap`:
primary flat, secondary up, secondary reported as the win. Every other gate in the
harness is silent on it.
"""

from __future__ import annotations

import pytest

from expfactory.harness import Experiment, RunResult
from expfactory.prereg import (
    Guardrail,
    PreregContext,
    Preregistration,
    gate_preregistration,
)

BASELINE = 0.70
PARENT = "exp-parent"


def _prereg(**over: object) -> Preregistration:
    base: dict[str, object] = dict(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=BASELINE,
        minimum_effect=0.02,
        seeds=(0, 1, 2),
        parent_id=PARENT,
    )
    base.update(over)
    return Preregistration(**base)  # type: ignore[arg-type]


def _exp(
    metrics: list[float], seeds: list[int] | None = None, extra: dict[str, float] | None = None
) -> Experiment:
    seeds = seeds if seeds is not None else list(range(len(metrics)))
    exp = Experiment(
        exp_id="e1",
        parent_id=None,
        hypothesis="h",
        config={},
        code_hash="c",
    )
    exp.runs = [
        RunResult(
            seed=s,
            val_metric=m,
            train_ids_hash="t",
            eval_ids_hash="e",
            overlap_count=0,
            wall_seconds=0.0,
            extra=dict(extra or {}),
        )
        for s, m in zip(seeds, metrics, strict=True)
    ]
    return exp


def _ctx(
    prereg: Preregistration,
    *,
    filed: bool = True,
    parent_metric: float | None = BASELINE,
    **over: object,
) -> PreregContext:
    # parent_metric stands in for what the ledger recorded for the parent. It is
    # deliberately a separate argument from prereg.baseline_value so a test can
    # make them disagree — that disagreement is rule 8's whole subject.
    return PreregContext(
        prereg=prereg,
        cited_hash=prereg.hash,
        parent_metric=parent_metric,
        filed_hashes=frozenset({prereg.hash}) if filed else frozenset(),
        **over,  # type: ignore[arg-type]
    )


# ---- the case that must pass ------------------------------------------------


def test_clean_confirmatory_run_passes():
    p = _prereg()
    result = gate_preregistration(_exp([0.75, 0.74, 0.76]), prereg_ctx=_ctx(p))
    assert result.passed, result.detail


# ---- the headline fooling mode ---------------------------------------------


def test_metric_swap_is_blocked():
    """Primary flat, latency improved, latency reported as the win.

    Declared: maximise val_metric by >= 0.02. Observed: val_metric unmoved while a
    secondary got better. Must reject — a secondary is never sufficient.
    """
    p = _prereg(secondary_metrics=("latency_ms",))
    exp = _exp([0.70, 0.70, 0.70], extra={"latency_ms": 12.0})
    result = gate_preregistration(exp, prereg_ctx=_ctx(p))
    assert not result.passed
    assert "does not meet the declared minimum" in result.detail


def test_a_metric_cannot_be_both_promoter_and_guardrail():
    """The asymmetry is enforced at construction, not left to reviewer vigilance."""
    try:
        _prereg(guardrails=(Guardrail("val_metric", "maximize"),))
    except ValueError as exc:
        assert "never both" in str(exc)
    else:
        raise AssertionError("expected construction to fail")


# ---- HARKing: filing after the fact ----------------------------------------


def test_post_hoc_filing_is_blocked():
    """A preregistration not already in the ledger did not precede the run."""
    p = _prereg()
    result = gate_preregistration(_exp([0.75, 0.74, 0.76]), prereg_ctx=_ctx(p, filed=False))
    assert not result.passed
    assert "HARKING" in result.detail


def test_citation_must_match_the_supplied_record():
    """A citation that does not hash to the record it travels with is decorative."""
    p = _prereg()
    ctx = PreregContext(prereg=p, cited_hash="deadbeef", filed_hashes=frozenset({"deadbeef"}))
    result = gate_preregistration(_exp([0.75, 0.74, 0.76]), prereg_ctx=ctx)
    assert not result.passed
    assert "does not match" in result.detail


# ---- seed shopping ----------------------------------------------------------


def test_seed_shopping_is_blocked():
    """Declared three seeds, reported the best two."""
    p = _prereg(seeds=(0, 1, 2))
    result = gate_preregistration(_exp([0.80, 0.79], seeds=[0, 1]), prereg_ctx=_ctx(p))
    assert not result.passed
    assert "SEED SHOPPING" in result.detail


def test_extra_seeds_are_also_blocked():
    p = _prereg(seeds=(0, 1, 2))
    result = gate_preregistration(
        _exp([0.80, 0.79, 0.81, 0.95], seeds=[0, 1, 2, 3]), prereg_ctx=_ctx(p)
    )
    assert not result.passed
    assert "SEED SHOPPING" in result.detail


# ---- guardrails block, never promote ---------------------------------------


def test_guardrail_regression_blocks_a_genuine_primary_gain():
    """Primary genuinely improved; latency blew its declared bound. Still rejected."""
    p = _prereg(guardrails=(Guardrail("latency_ms", "minimize"),))
    exp = _exp([0.80, 0.81, 0.79], extra={"latency_ms": 35.0})
    result = gate_preregistration(exp, prereg_ctx=_ctx(p, parent_metrics={"latency_ms": 20.0}))
    assert not result.passed
    assert "GUARDRAIL REGRESSION" in result.detail


def test_guardrail_within_bound_allows_promotion():
    p = _prereg(guardrails=(Guardrail("latency_ms", "minimize"),))
    exp = _exp([0.80, 0.81, 0.79], extra={"latency_ms": 15.0})
    assert gate_preregistration(exp, prereg_ctx=_ctx(p, parent_metrics={"latency_ms": 20.0})).passed


def test_undeclared_guardrail_metric_blocks():
    p = _prereg(guardrails=(Guardrail("latency_ms", "minimize"),))
    result = gate_preregistration(
        _exp([0.80, 0.81, 0.79]), prereg_ctx=_ctx(p, parent_metrics={"latency_ms": 20.0})
    )
    assert not result.passed
    assert "not reported" in result.detail


# ---- exploratory runs are structurally unpromotable -------------------------


def test_exploratory_run_never_promotes_even_with_a_great_number():
    p = _prereg()
    ctx = _ctx(p)
    ctx.exploratory = True
    result = gate_preregistration(_exp([0.99, 0.98, 0.99]), prereg_ctx=ctx)
    assert not result.passed
    assert "EXPLORATORY" in result.detail


def test_exploratory_run_needs_no_preregistration():
    """Exploration stays free: no prereg required, and it still cannot promote."""
    result = gate_preregistration(
        _exp([0.99, 0.98, 0.99]), prereg_ctx=PreregContext(exploratory=True)
    )
    assert not result.passed
    assert "EXPLORATORY" in result.detail


def test_confirmatory_run_without_any_prereg_is_blocked():
    result = gate_preregistration(_exp([0.99, 0.98, 0.99]), prereg_ctx=PreregContext())
    assert not result.passed
    assert "no preregistration cited" in result.detail


def test_gate_blocks_when_no_context_supplied_at_all():
    """Fail closed: a confirmatory run with no prereg context is not a free pass."""
    assert not gate_preregistration(_exp([0.99, 0.98, 0.99])).passed


# ---- direction is honoured --------------------------------------------------


def test_minimize_direction_treats_a_lower_number_as_the_gain():
    p = _prereg(
        primary_metric="loss", direction="minimize", baseline_value=1.00, minimum_effect=0.10
    )
    exp = _exp([0.0, 0.0, 0.0], extra={"loss": 0.85})
    assert gate_preregistration(exp, prereg_ctx=_ctx(p, parent_metric=1.00)).passed


def test_minimize_direction_rejects_an_increase():
    p = _prereg(
        primary_metric="loss", direction="minimize", baseline_value=1.00, minimum_effect=0.10
    )
    exp = _exp([0.0, 0.0, 0.0], extra={"loss": 1.20})
    assert not gate_preregistration(exp, prereg_ctx=_ctx(p, parent_metric=1.00)).passed


# ---- the hash is the mechanism ---------------------------------------------


def test_hash_is_stable_for_identical_content():
    assert _prereg().hash == _prereg().hash


def test_hash_changes_when_any_declared_term_changes():
    base = _prereg().hash
    assert _prereg(minimum_effect=0.03).hash != base
    assert _prereg(direction="minimize").hash != base
    assert _prereg(seeds=(0, 1, 2, 3)).hash != base
    assert _prereg(baseline_value=0.71).hash != base


# ---- rule 8: the declared baseline must match what the parent scored --------


def test_forged_baseline_is_blocked():
    """The hole this rule closes: the agent writes its own preregistration, so
    without a check it can declare baseline_value=0.0 and promote garbage."""
    p = _prereg(baseline_value=0.0)
    result = gate_preregistration(_exp([0.05, 0.05, 0.05]), prereg_ctx=_ctx(p))
    assert not result.passed
    assert "FORGED BASELINE" in result.detail


def test_a_worthless_result_cannot_promote_against_a_zero_baseline():
    """End of the same thread, stated as behaviour: 0.05 macro-F1 is worse than
    random and must never promote, whatever baseline was declared."""
    p = _prereg(baseline_value=0.0, minimum_effect=0.02)
    assert not gate_preregistration(_exp([0.05, 0.05, 0.05]), prereg_ctx=_ctx(p)).passed


def test_confirmatory_run_without_a_parent_is_blocked():
    """You cannot claim an improvement over nothing. The first run of a lineage
    is exploratory by construction, and it establishes the baseline."""
    p = _prereg(parent_id=None)
    result = gate_preregistration(_exp([0.99, 0.99, 0.99]), prereg_ctx=_ctx(p))
    assert not result.passed
    assert "no parent" in result.detail


def test_unrecorded_parent_is_blocked():
    p = _prereg()
    result = gate_preregistration(_exp([0.75, 0.75, 0.75]), prereg_ctx=_ctx(p, parent_metric=None))
    assert not result.passed
    assert "no recorded result" in result.detail


# ---- #9: guardrails are regression checks, with a direction ----------------


def test_a_maximize_guardrail_blocks_a_drop_not_an_improvement():
    """The case the bound-based form could not express at all.

    "Recall must not drop" is a guardrail whose good direction is up. Under a
    hardcoded lower-is-better bound it would fire on every improvement, so it was
    simply not expressible — and a guardrail you cannot state is one that does
    not protect you.
    """
    p = _prereg(guardrails=(Guardrail("recall", "maximize"),))
    improved = _exp([0.75, 0.75, 0.75], extra={"recall": 0.80})
    assert gate_preregistration(
        improved, prereg_ctx=_ctx(p, parent_metrics={"recall": 0.70})
    ).passed

    dropped = _exp([0.75, 0.75, 0.75], extra={"recall": 0.60})
    result = gate_preregistration(dropped, prereg_ctx=_ctx(p, parent_metrics={"recall": 0.70}))
    assert not result.passed
    assert "GUARDRAIL" in result.detail


def test_a_minimize_guardrail_blocks_a_rise():
    p = _prereg(guardrails=(Guardrail("latency_ms", "minimize"),))
    worse = _exp([0.75, 0.75, 0.75], extra={"latency_ms": 30.0})
    result = gate_preregistration(worse, prereg_ctx=_ctx(p, parent_metrics={"latency_ms": 20.0}))
    assert not result.passed
    assert "GUARDRAIL" in result.detail


def test_the_guardrail_bound_comes_from_the_parent_not_the_prereg():
    """Same forgery as rule 8, one field over. If the agent names the threshold
    its guardrail is decorative."""
    p = _prereg(guardrails=(Guardrail("latency_ms", "minimize"),))
    # parent was genuinely fast; this run is far slower and must not pass
    result = gate_preregistration(
        _exp([0.75, 0.75, 0.75], extra={"latency_ms": 900.0}),
        prereg_ctx=_ctx(p, parent_metrics={"latency_ms": 10.0}),
    )
    assert not result.passed


def test_a_guardrail_tolerance_permits_declared_slack():
    """Some regression is often acceptable and should be stated in advance."""
    p = _prereg(guardrails=(Guardrail("latency_ms", "minimize", tolerance=5.0),))
    ctx = _ctx(p, parent_metrics={"latency_ms": 20.0})
    assert gate_preregistration(_exp([0.75] * 3, extra={"latency_ms": 24.0}), prereg_ctx=ctx).passed
    assert not gate_preregistration(
        _exp([0.75] * 3, extra={"latency_ms": 26.0}), prereg_ctx=ctx
    ).passed


def test_an_unrecorded_guardrail_on_the_parent_blocks():
    """No parent value means no regression can be computed. Fail closed."""
    p = _prereg(guardrails=(Guardrail("latency_ms", "minimize"),))
    result = gate_preregistration(
        _exp([0.75] * 3, extra={"latency_ms": 5.0}), prereg_ctx=_ctx(p, parent_metrics={})
    )
    assert not result.passed


def test_guardrail_direction_must_be_valid():
    with pytest.raises(ValueError):
        Guardrail("latency_ms", "sideways")  # type: ignore[arg-type]
