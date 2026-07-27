"""
G-08 fixtures (ticket N-04) — these calibrate the threshold; they do not merely
check it.

G-07 makes each preregistration individually honest: the rule was fixed before
the run. It structurally cannot see the pattern *across* preregistrations. An
agent can file eight rules naming eight primary metrics and promote on the
eighth, and every one of those eight passes G-07.

The boundary these fixtures pin: one revision is routine, two is plausible, and a
fourth attempt after three that never promoted is the S-hacking signature. That
is a judgement about honest research behaviour, encoded as a number, and it
should be re-calibrated once real lineages exist.
"""

from __future__ import annotations

from pathlib import Path

from expfactory.harness import Experiment, RunResult
from expfactory.prereg import DEFAULT_MAX_ATTEMPTS, PreregContext, Preregistration
from expfactory.prereg import gate_prereg_churn as churn
from expfactory.verifier import Candidate, GateVerifier, Ledger

LINEAGE = "exp-parent-1"


def _exp() -> Experiment:
    e = Experiment(exp_id="e", parent_id=LINEAGE, hypothesis="h", config={}, code_hash="c")
    e.runs = [
        RunResult(
            seed=s,
            val_metric=0.75,
            train_ids_hash="t",
            eval_ids_hash="e",
            overlap_count=0,
            wall_seconds=0.0,
        )
        for s in range(3)
    ]
    return e


def _prereg(n: int, parent: str | None = LINEAGE) -> Preregistration:
    """Distinct preregistrations sharing one lineage.

    Real metric-shopping would vary `primary_metric`; these vary the declared
    `minimum_effect` by a hair, so each is a distinct filing that is still
    *satisfiable* by the fixture runs. That matters: the end-to-end tests below
    have to reach G-08, and a prereg naming a metric the runs never reported is
    rejected by G-07 first — correct behaviour, but the wrong gate under test.

    These used to vary `decision_rule`, treating it as a scratch identifier.
    GH#36 made that field mean something, and the abuse surfaced immediately.
    """
    return Preregistration(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=0.70,
        minimum_effect=0.02 + n * 0.0001,
        seeds=(0, 1, 2),
        parent_id=parent,
    )


def _candidate(prereg_hash: str, parent: str | None = LINEAGE, metric: float = 0.75) -> Candidate:
    return Candidate(
        hypothesis="h",
        config={},
        code_hash="c",
        runs=[
            dict(
                seed=s,
                val_metric=metric,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=0,
                wall_seconds=0.0,
            )
            for s in range(3)
        ],
        cost_usd=0.4,
        parent_id=parent,
        prereg_hash=prereg_hash,
    )


# ---- the calibrated boundary -----------------------------------------------


def test_first_attempt_is_fine():
    assert churn(_exp(), prereg_ctx=PreregContext(lineage_attempts=1)).passed


def test_one_revision_is_routine():
    assert churn(_exp(), prereg_ctx=PreregContext(lineage_attempts=2)).passed


def test_two_revisions_are_still_plausible():
    assert churn(_exp(), prereg_ctx=PreregContext(lineage_attempts=3)).passed


def test_fourth_attempt_is_s_hacking():
    result = churn(_exp(), prereg_ctx=PreregContext(lineage_attempts=4))
    assert not result.passed
    assert "S-HACKING" in result.detail


def test_the_threshold_is_exactly_where_the_fixtures_put_it():
    """Guards the calibration itself: if someone edits the constant, the boundary
    fixtures above stop describing the behaviour and this fails loudly."""
    assert DEFAULT_MAX_ATTEMPTS == 3
    assert churn(_exp(), prereg_ctx=PreregContext(lineage_attempts=DEFAULT_MAX_ATTEMPTS)).passed
    assert not churn(
        _exp(), prereg_ctx=PreregContext(lineage_attempts=DEFAULT_MAX_ATTEMPTS + 1)
    ).passed


def test_exploration_is_exempt():
    """Exploration is supposed to be unlimited, and it cannot promote anyway."""
    assert churn(_exp(), prereg_ctx=PreregContext(lineage_attempts=99, exploratory=True)).passed


# ---- counting, against a real ledger ---------------------------------------


def _seed_parent(led: Ledger, metric: float = 0.70) -> None:
    """Record the lineage's parent result, so rule 8 has a baseline to check.

    Scores 0.70 — the value every prereg here declares as its baseline. The
    children score 0.75, clearing the 0.02 minimum effect.
    """
    led.append(
        GateVerifier(id_factory=lambda: LINEAGE).run(_candidate("", parent=None, metric=metric))
    )


def test_ledger_counts_only_the_matching_lineage(tmp_path: Path):
    led = Ledger(tmp_path / "l.jsonl")
    for n in range(3):
        led.append_prereg(_prereg(n))
    led.append_prereg(_prereg(9, parent="somewhere-else"))
    assert led.non_promoting_prereg_count(LINEAGE) == 3
    assert led.non_promoting_prereg_count("somewhere-else") == 1


def test_a_promoted_prereg_stops_counting_against_the_lineage(tmp_path: Path):
    """The gate targets *failure* to promote. A lineage that landed a result has
    not been shopping, however many rules it filed getting there."""
    led = Ledger(tmp_path / "l.jsonl")
    _seed_parent(led)
    p0 = _prereg(0)
    led.append_prereg(p0)
    led.append_prereg(_prereg(1))
    assert led.non_promoting_prereg_count(LINEAGE) == 2

    verifier = GateVerifier(require_prereg=True, prereg_store=led, id_factory=lambda: "e1")
    bundle = verifier.run(_candidate(p0.hash))
    led.append(bundle)
    assert bundle.promoted, bundle.blocked_by
    assert led.non_promoting_prereg_count(LINEAGE) == 1


def test_churn_blocks_end_to_end_through_the_verifier(tmp_path: Path):
    led = Ledger(tmp_path / "l.jsonl")
    preregs = [_prereg(n) for n in range(4)]
    for p in preregs:
        led.append_prereg(p)

    verifier = GateVerifier(require_prereg=True, prereg_store=led, id_factory=lambda: "e1")
    bundle = verifier.run(_candidate(preregs[-1].hash))
    assert not bundle.promoted
    assert "prereg_churn" in bundle.blocked_by


def test_a_clean_lineage_passes_end_to_end(tmp_path: Path):
    """The same machinery must not block ordinary work."""
    led = Ledger(tmp_path / "l.jsonl")
    _seed_parent(led)
    p = _prereg(0)
    led.append_prereg(p)

    verifier = GateVerifier(require_prereg=True, prereg_store=led, id_factory=lambda: "e1")
    bundle = verifier.run(_candidate(p.hash))
    assert bundle.promoted, bundle.blocked_by
    assert "prereg_churn" in bundle.gate_names


# ---- hash stability across processes ---------------------------------------


def test_prereg_hash_is_stable_across_a_fresh_interpreter():
    """N-02 called this out specifically, and it is the whole mechanism.

    A confirmatory run cites a preregistration filed by a *different* process —
    the runner files it, an agent session cites it later. If the hash depended on
    anything process-local (address-based hashing, dict iteration order, PYTHONHASHSEED)
    every confirmatory run would fail to match its own rule, and the failure would
    look like tampering rather than a bug.

    Same-process equality cannot catch that, so this re-derives the hash in a
    subprocess with hash randomisation explicitly enabled.
    """
    import os
    import subprocess
    import sys

    expected = _prereg(0).hash

    script = (
        "from expfactory.prereg import Preregistration;"
        "print(Preregistration("
        "primary_metric='val_metric', direction='maximize', baseline_value=0.70,"
        "minimum_effect=0.02, seeds=(0, 1, 2), parent_id='exp-parent-1'"
        ").hash)"
    )
    env = {**os.environ, "PYTHONHASHSEED": "random", "PYTHONPATH": "src"}
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected
