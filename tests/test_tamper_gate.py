"""
Ticket 03 — the test-tamper gate (closes stress scenario #1).

Seam under test: gate_no_test_tampering(candidate_diff) -> GateResult
A candidate that reaches a green metric by mutilating its own verification —
deleting assertions, adding skip/xfail, lowering coverage thresholds, or editing
the harness itself — must be BLOCKED, regardless of the number it reports.

This is the empirical-lane analogue of "don't let the agent edit the verifier".
Written RED: the gate does not exist yet.
"""
from __future__ import annotations

from expfactory.gates_v1 import gate_no_test_tampering, DiffEvidence


def _clean_diff() -> DiffEvidence:
    return DiffEvidence(
        added_lines=["def train(cfg, seed):", "    return fit(cfg, seed)"],
        removed_lines=["# a stale comment"],
        touched_paths=["model.py"],
    )


def test_clean_diff_passes():
    g = gate_no_test_tampering(_clean_diff())
    assert g.passed is True


def test_removed_assertion_is_blocked():
    diff = DiffEvidence(
        added_lines=[],
        removed_lines=["    assert average_precision > 0.7"],
        touched_paths=["test_model.py"],
    )
    g = gate_no_test_tampering(diff)
    assert g.passed is False
    assert g.blocking is True


def test_added_skip_marker_is_blocked():
    diff = DiffEvidence(
        added_lines=["@pytest.mark.skip(reason='flaky')"],
        removed_lines=[],
        touched_paths=["test_model.py"],
    )
    assert gate_no_test_tampering(diff).passed is False


def test_added_xfail_marker_is_blocked():
    diff = DiffEvidence(
        added_lines=["@pytest.mark.xfail"],
        removed_lines=[],
        touched_paths=["test_checkout.py"],
    )
    assert gate_no_test_tampering(diff).passed is False


def test_lowered_coverage_threshold_is_blocked():
    diff = DiffEvidence(
        added_lines=["fail_under = 40"],
        removed_lines=["fail_under = 85"],
        touched_paths=[".coveragerc"],
    )
    assert gate_no_test_tampering(diff).passed is False


def test_editing_the_harness_itself_is_blocked():
    diff = DiffEvidence(
        added_lines=["    return GateResult('no_leakage', True, 'bypass')"],
        removed_lines=[],
        touched_paths=["harness.py"],
    )
    g = gate_no_test_tampering(diff)
    assert g.passed is False
    assert "harness" in g.detail.lower()


def test_adding_a_new_test_is_allowed():
    """Guard against false positives: adding assertions is good, not tampering."""
    diff = DiffEvidence(
        added_lines=["    assert model.predict(x) == expected"],
        removed_lines=[],
        touched_paths=["test_model.py"],
    )
    assert gate_no_test_tampering(diff).passed is True
