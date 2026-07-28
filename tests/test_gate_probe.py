"""
The probe that checks the gates, and the test that checks the probe.

W-03 makes the adversarial suite the acceptance criterion. On 2026-07-27 it
reported 5/5 correct while a blocking gate could not pass anything. A fixture
asserts a point; "this gate can sometimes pass" is a property, and no point
asserts it.

The load-bearing test here is `test_the_probe_catches_the_bug_it_was_written_for`,
which restores the broken arithmetic and requires the probe to object. A probe
that cannot catch its own motivating bug is theatre, and there is no way to know
that without putting the bug back.
"""

from __future__ import annotations

import pytest

from expfactory.gate_probe import (
    ProbeFinding,
    ProbeReport,
    candidate,
    probe_every_blocking_gate_can_fail,
    probe_every_blocking_gate_can_pass,
    probe_group_leakage_is_caught_when_declared,
    probe_more_leakage_never_helps,
    probe_noise_does_not_flip_a_verdict,
    runs,
    sweep,
    uncovered_gates,
)
from expfactory.gates_v1 import DatasetGrouping
from expfactory.harness import GateResult
from expfactory.verifier import GateVerifier


def test_the_shipped_gate_set_passes_every_property():
    """The whole point. If this fails, a gate disagrees with something true by
    construction rather than with a fixture someone wrote."""
    report = sweep()
    assert report.is_clean, str(report)


def test_the_probe_catches_the_bug_it_was_written_for(monkeypatch):
    """Restores the pre-fix arithmetic and requires the probe to object.

    The original compared lift to the top seed's contribution, and those are the
    same quantity — so the ratio was identically 1.0 and the gate rejected
    everything whose seeds were not bit-identical.

    Without this test there is no evidence the probe would have caught it, only a
    claim. A probe that cannot catch its own motivating bug is theatre.
    """

    def broken(exp, dominance: float = 0.5, **_):
        vals = sorted((r.val_metric for r in exp.runs), reverse=True)
        if len(vals) < 3:
            return GateResult("no_single_seed_dominance", False, "need >=3 seeds", blocking=True)
        n = len(vals)
        best, rest = vals[0], vals[1:]
        rest_mean = sum(rest) / len(rest)
        lift = sum(vals) / n - rest_mean
        contribution = (best - rest_mean) / n
        frac = (contribution / lift) if lift > 1e-12 else 0.0
        dominated = lift > 1e-9 and frac > dominance
        return GateResult("no_single_seed_dominance", not dominated, "restored bug", blocking=True)

    monkeypatch.setattr("expfactory.gates_v1.gate_no_single_seed_dominance", broken)

    report = sweep()

    assert not report.is_clean, "the probe did not notice the restored bug"
    assert any(f.gate == "no_single_seed_dominance" for f in report.findings)
    assert any(f.probe == "noise-does-not-flip-a-verdict" for f in report.findings)


def test_the_can_pass_probe_names_a_gate_that_never_passes(monkeypatch):
    """The other half of the same bug class, caught by the other probe."""

    def always_blocks(exp, **_):
        return GateResult("no_single_seed_dominance", False, "never passes", blocking=True)

    monkeypatch.setattr("expfactory.gates_v1.gate_no_single_seed_dominance", always_blocks)

    findings = list(probe_every_blocking_gate_can_pass(GateVerifier()))
    assert [f.gate for f in findings] == ["no_single_seed_dominance"]
    assert "rejects every experiment forever" in findings[0].detail


def test_the_can_fail_probe_names_a_gate_that_never_fires(monkeypatch):
    """A gate that fires on nothing is decoration."""

    def always_passes(exp, **_):
        return GateResult("no_single_seed_dominance", True, "never fires", blocking=True)

    monkeypatch.setattr("expfactory.gates_v1.gate_no_single_seed_dominance", always_passes)

    findings = list(probe_every_blocking_gate_can_fail(GateVerifier()))
    assert any(f.gate == "no_single_seed_dominance" for f in findings)


def test_the_can_fail_probe_does_not_cry_wolf():
    """Its first version reported six false positives against a healthy gate set,
    because it assumed three bad inputs should trip every gate — but most gates
    are conditional on evidence a bare candidate does not carry.

    Six false alarms is worse than none: it teaches a reader to skim, which is
    how a wall becomes a formality.
    """
    assert list(probe_every_blocking_gate_can_fail(GateVerifier())) == []


def test_gates_without_a_trigger_are_reported_as_coverage_not_as_bugs():
    """The honest handling of the gates the sweep cannot provoke: a to-do list,
    not a finding. Conflating the two is what produced the false positives."""
    uncovered = uncovered_gates(GateVerifier())

    assert "cost" in uncovered, "cost needs a cap breach this sweep does not build"
    assert "no_single_seed_dominance" not in uncovered, "this one has a trigger"


# --------------------------------------------------------------------------- #
# The individual properties
# --------------------------------------------------------------------------- #


def test_noise_probe_is_quiet_on_a_healthy_gate_set():
    assert list(probe_noise_does_not_flip_a_verdict(GateVerifier())) == []


def test_leakage_monotonicity_holds():
    assert list(probe_more_leakage_never_helps(GateVerifier())) == []


def test_group_leakage_probe_needs_a_grouping_to_bite():
    """With no grouping the gate warns rather than blocks, so the probe must be
    run against a configured verifier — the same trust boundary G-09 has."""
    grouping = DatasetGrouping("recording_session", "probe")

    assert list(probe_group_leakage_is_caught_when_declared(GateVerifier(grouping=grouping))) == []
    # and it *would* object if the gate stopped biting
    findings = list(probe_group_leakage_is_caught_when_declared(GateVerifier()))
    assert findings, "with no grouping declared the leak is not blocked, and the probe says so"


# --------------------------------------------------------------------------- #
# Generator
# --------------------------------------------------------------------------- #


def test_spread_and_outlier_are_independent_knobs():
    """The gate that broke conflated ordinary noise with a lottery. A generator
    that could not express one without the other could not have exposed it."""
    spread_only = [r.val_metric for r in runs(0.8, seeds=5, spread=0.1)]
    outlier_only = [r.val_metric for r in runs(0.8, seeds=5, outlier=0.4)]

    # evenly fanned, no single seed detached
    gaps = sorted(spread_only)
    assert max(gaps) - sorted(gaps)[-2] < 0.05

    # one seed clearly detached from a tight cluster
    top = sorted(outlier_only)
    assert top[-1] - top[-2] > 0.3


def test_a_probe_report_reads_usefully():
    empty = ProbeReport(checked=3)
    assert "no disagreements" in str(empty)

    loud = ProbeReport(checked=3, findings=[ProbeFinding("p", "g", "because")])
    assert "1 disagreement" in str(loud) and "because" in str(loud)


@pytest.mark.parametrize("seeds", [3, 5, 20])
def test_an_innocuous_candidate_is_promoted_at_any_seed_count(seeds: int):
    """Sanity on the generator itself: if these were secretly rejectable, every
    probe above would be vacuous."""
    assert GateVerifier().run(candidate("plain", metric=0.8, seeds=seeds, spread=0.02)).promoted
