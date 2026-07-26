"""
G-09, the leak with disjoint sample ids.

`gate_no_leakage` intersects train and eval *sample ids*. The most common leak in
sensor data does not touch that: a dataset segmented into clips, split at clip
level, puts adjacent slices of one continuous recording on both sides. Every id
is distinct, the id gate passes, and the model scores the session it memorised.

EchoHawk (arXiv:2606.29589, June 2026) documents this in a widely used public
drone-audio dataset and measures it: enforcing recording-session-grouped
cross-validation drops a random-forest baseline's detection probability at 1%
false-alarm rate from 0.796 to 0.745.

The first test is the load-bearing one. It runs both gates over the same evidence
and asserts they disagree — if they ever agreed, G-09 would be decoration.
"""

from __future__ import annotations

from expfactory.gates_v1 import DatasetGrouping, gate_no_group_leakage
from expfactory.harness import Experiment, RunResult, gate_no_leakage
from expfactory.verifier import Candidate, GateVerifier

GROUPING = DatasetGrouping(
    group_key="recording_session",
    rationale="clips are segmented from longer continuous captures",
    source="EchoHawk, arXiv:2606.29589",
)


def _exp(train: tuple[str, ...], ev: tuple[str, ...], declare: bool = True) -> Experiment:
    extra = {"train_groups": list(train), "eval_groups": list(ev)} if declare else {}
    return Experiment(
        exp_id="x",
        parent_id=None,
        hypothesis="h",
        config={},
        code_hash="c",
        runs=[
            RunResult(
                seed=s,
                val_metric=0.9,
                train_ids_hash="t",
                eval_ids_hash="e",
                # zero throughout: these fixtures must be invisible to the id gate
                overlap_count=0,
                wall_seconds=0.0,
                extra=dict(extra),
            )
            for s in range(3)
        ],
    )


def test_the_id_gate_cannot_see_this_and_the_group_gate_can():
    """The reason G-09 exists. Same evidence, opposite verdicts."""
    exp = _exp(("sess-1", "sess-2"), ("sess-2", "sess-9"))

    assert gate_no_leakage(exp).passed, "sample ids are disjoint, so the id gate must pass"

    result = gate_no_group_leakage(exp, grouping=GROUPING)
    assert not result.passed and result.blocking
    assert "sess-2" in result.detail


def test_disjoint_sessions_pass():
    result = gate_no_group_leakage(_exp(("a", "b"), ("c",)), grouping=GROUPING)
    assert result.passed and result.blocking


def test_no_declared_grouping_warns_without_blocking():
    """Most data has no group structure. Blocking there would make the gate
    something everyone routes around, so it warns — but it says plainly what it
    did not check, rather than reporting a clean bill of health."""
    result = gate_no_group_leakage(_exp(("a",), ("b",)), grouping=None)

    assert result.passed
    assert not result.blocking
    assert "does not exclude session-level leakage" in result.detail


def test_declared_grouping_with_no_recorded_groups_blocks():
    """Fail-closed. If omitting the field were a pass, omitting the field would
    be the technique."""
    result = gate_no_group_leakage(_exp((), (), declare=False), grouping=GROUPING)

    assert not result.passed and result.blocking
    assert "unproven" in result.detail


def test_a_bare_string_is_not_read_as_a_set_of_characters():
    """`"sess-1"` is iterable. Read naively it becomes {'s','e','1',...}, which
    almost always looks disjoint from another session name — a silent pass on
    exactly the evidence the gate exists to judge."""
    exp = _exp((), ())
    for r in exp.runs:
        r.extra = {"train_groups": "sess-1", "eval_groups": "sess-1"}

    result = gate_no_group_leakage(exp, grouping=GROUPING)
    assert not result.passed, "a string group must be treated as undeclared, never as a set"


def test_one_leaky_seed_among_clean_ones_still_blocks():
    exp = _exp(("a", "b"), ("c",))
    exp.runs[1].extra = {"train_groups": ["a", "b"], "eval_groups": ["b"]}

    assert not gate_no_group_leakage(exp, grouping=GROUPING).passed


def test_the_grouping_comes_from_the_verifier_not_the_candidate():
    """The trust boundary. A candidate that could supply its own grouping could
    also decline to, which would make the gate advisory. So the same candidate
    must be judged differently by two differently-configured verifiers."""
    runs = _exp(("s1", "s2"), ("s2",)).runs
    candidate = Candidate(hypothesis="h", config={}, code_hash="c", runs=runs)

    assert GateVerifier().run(candidate).promoted, "no grouping declared: warns, does not block"
    assert not GateVerifier(grouping=GROUPING).run(candidate).promoted


def test_the_gate_runs_on_every_candidate():
    """Non-blocking is not the same as absent. The verdict must record that the
    check happened and what it concluded, or a reader cannot tell an unchecked
    run from a clean one."""
    candidate = Candidate(hypothesis="h", config={}, code_hash="c", runs=_exp(("a",), ("b",)).runs)
    bundle = GateVerifier().run(candidate)

    assert "no_group_leakage" in bundle.to_dict()["gate_names"]
