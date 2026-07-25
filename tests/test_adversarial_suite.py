"""
Ticket 04 — the labeled adversarial suite (= W-09 eval set).

This is the factory's known-answer test of its own judgement. Each fixture is a
candidate with a PRE-ASSIGNED verdict; the gate harness must reach that verdict.
Fixtures come in a visible partition (used to tune gates) and a held-out partition
(never consulted during tuning) — the factory held to the same holdout discipline
it enforces on experiments.

Seam under test: suite.evaluate(verifier) -> SuiteResult with per-fixture verdicts
and an accuracy, and the held-out partition is only reachable via an explicit call.

Written RED: the suite module does not exist.
"""
from __future__ import annotations

from expfactory.adversarial_suite import Expect, build_suite


def test_suite_has_all_four_failure_classes():
    """The suite must cover the ways ML results are faked, not just happy paths."""
    suite = build_suite()
    kinds = {f.kind for f in suite.visible_fixtures()}
    assert {"genuine", "seed_noise", "leakage", "holdout_burn"} <= kinds


def test_suite_has_a_heldout_partition():
    suite = build_suite()
    assert len(suite.heldout_fixtures()) > 0
    # held-out fixtures are NOT returned by the visible accessor
    visible_ids = {f.id for f in suite.visible_fixtures()}
    heldout_ids = {f.id for f in suite.heldout_fixtures()}
    assert visible_ids.isdisjoint(heldout_ids)


def test_gate_harness_classifies_visible_fixtures_correctly():
    """The core acceptance check: the real GateVerifier must reach the assigned
    verdict on every visible fixture."""
    from expfactory.verifier import GateVerifier
    suite = build_suite()
    result = suite.evaluate(GateVerifier(), partition="visible")
    assert result.accuracy == 1.0, result.mismatches


def test_genuine_fixtures_expect_promotion():
    suite = build_suite()
    for f in suite.visible_fixtures():
        if f.kind == "genuine":
            assert f.expect == Expect.PROMOTE


def test_fake_fixtures_expect_rejection():
    suite = build_suite()
    for f in suite.visible_fixtures():
        if f.kind in ("seed_noise", "leakage", "holdout_burn"):
            assert f.expect == Expect.REJECT


def test_reject_all_is_a_passing_outcome():
    """W-03's load-bearing negative criterion: a run that rejects every proposed
    gain is a PASS. The suite must not treat 'rejected everything' as failure —
    only a MISMATCH against an assigned verdict is failure."""
    from expfactory.verifier import GateVerifier
    suite = build_suite()
    result = suite.evaluate(GateVerifier(), partition="visible")
    # accuracy is about matching assigned verdicts, never about promotion rate
    assert result.is_pass is True
