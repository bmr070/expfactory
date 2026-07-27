"""
The scorer: who computes the number.

G-10 established that a run happened. It could not establish that the number was
right, because the agent wrote both the training code and the code that computed
the metric. T-01 splits those: the agent submits **predictions**, trusted code
holds the **labels** and computes the score.

The first tests are the security property, not the arithmetic. If labels can
reach an agent session, every other property here is decoration.
"""

from __future__ import annotations

import pytest

from expfactory.scorer import LabelStore, Ladder, SubmissionRefused, score


def test_a_label_store_never_returns_its_labels():
    """The one property everything else rests on.

    There is deliberately no accessor. Not a private one, not a debug one — a
    label store that can be read is a label store that will be read, and the
    agent session is the party asking.
    """
    store = LabelStore({"s1": 1, "s2": 0, "s3": 1})

    public = [name for name in dir(store) if not name.startswith("_")]
    for name in public:
        value = getattr(store, name)
        assert not isinstance(value, dict), f"{name} exposes a mapping; labels must not escape"

    assert "labels" not in public
    assert "y" not in public


def test_the_ids_are_available_but_the_labels_are_not():
    """The agent has to know *which* samples to predict, so ids are public. The
    answers are not."""
    store = LabelStore({"s1": 1, "s2": 0})

    assert set(store.sample_ids()) == {"s1", "s2"}
    with pytest.raises(AttributeError):
        _ = store.labels  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def test_a_perfect_submission_scores_one():
    """Expected value is a worked example, not a recomputation of the code."""
    store = LabelStore({"s1": 1, "s2": 0, "s3": 1, "s4": 0})
    result = score(store, {"s1": 1, "s2": 0, "s3": 1, "s4": 0})

    assert result.value == 1.0
    assert result.scored == 4


def test_half_right_scores_a_half():
    store = LabelStore({"s1": 1, "s2": 0, "s3": 1, "s4": 0})
    result = score(store, {"s1": 1, "s2": 0, "s3": 0, "s4": 1})

    assert result.value == 0.5


def test_a_missing_prediction_is_refused_not_scored_as_wrong():
    """Scoring a partial submission silently would let an agent improve its score
    by declining to predict the samples it finds hard, and the number would look
    like a real one."""
    store = LabelStore({"s1": 1, "s2": 0, "s3": 1})
    with pytest.raises(SubmissionRefused, match="missing"):
        score(store, {"s1": 1, "s2": 0})


def test_a_prediction_for_an_unknown_sample_is_refused():
    """An id the store has never heard of means the agent is predicting against
    something other than the holdout — a stale run, or a probe."""
    store = LabelStore({"s1": 1})
    with pytest.raises(SubmissionRefused, match="unknown"):
        score(store, {"s1": 1, "s9": 0})


def test_an_empty_submission_is_refused():
    store = LabelStore({"s1": 1})
    with pytest.raises(SubmissionRefused):
        score(store, {})


# --------------------------------------------------------------------------- #
# The Ladder (Blum & Hardt, ICML 2015)
#
# Predictions-only does not stop an agent fingerprinting the holdout inputs and
# overfitting by repeated submission. The leak is driven by *feedback*, not by
# queries: told the exact score every time, you overfit fast; told only "you
# improved", you learn far less per query.
# --------------------------------------------------------------------------- #


def test_the_first_submission_is_always_reported():
    """There is no incumbent to beat, so withholding would tell the agent
    nothing and cost a query for no information."""
    ladder = Ladder(step=0.05)
    answer = ladder.report(0.70)

    assert answer.improved
    assert answer.value == 0.70


def test_a_score_that_does_not_clearly_beat_the_incumbent_is_withheld():
    """The mechanism. The agent learns one bit — 'no' — instead of a number it
    can hill-climb against."""
    ladder = Ladder(step=0.05)
    ladder.report(0.70)

    answer = ladder.report(0.72)  # better, but inside the step

    assert not answer.improved
    assert answer.value is None


def test_a_clear_improvement_is_reported():
    ladder = Ladder(step=0.05)
    ladder.report(0.70)

    answer = ladder.report(0.80)

    assert answer.improved
    assert answer.value == 0.80


def test_a_worse_score_is_withheld_and_does_not_move_the_incumbent():
    """Reporting how *much* worse would leak the same gradient the mechanism
    exists to withhold."""
    ladder = Ladder(step=0.05)
    ladder.report(0.70)

    assert not ladder.report(0.10).improved
    # the incumbent is unchanged, so 0.74 is still inside the step from 0.70
    assert not ladder.report(0.74).improved


def test_tiny_gains_leak_far_fewer_numbers_than_they_would_under_exact_feedback():
    """The attack the Ladder is for, stated as what it actually does.

    It does **not** stop the incumbent rising — a genuinely better model should
    be recognised. It limits how *often* a number is handed back. Twenty
    submissions each 0.01 better would, under exact feedback, hand the agent
    twenty numbers to hill-climb against. Here it gets a handful.

    An earlier version of this test claimed none of them would be reported. That
    was wrong about the mechanism: 0.76 does clear a 0.05 step from 0.70. Worth
    recording, because a test asserting a stronger guarantee than the algorithm
    provides is how a mechanism comes to be trusted for something it never did.
    """
    ladder = Ladder(step=0.05)
    ladder.report(0.70)

    reported = [ladder.report(0.70 + i * 0.01) for i in range(1, 21)]
    disclosed = [a.value for a in reported if a.improved]

    assert len(disclosed) <= 5, f"leaked {len(disclosed)} of 20 scores"
    assert all(v is not None for v in disclosed)
    # and each disclosure really was a step-sized gain, not noise
    assert disclosed == sorted(disclosed)


def test_the_step_is_what_bounds_the_leak():
    """A step of zero turns this back into exact feedback, which is the thing the
    mechanism replaces. Refused at construction rather than silently degrading."""
    with pytest.raises(ValueError, match="step"):
        Ladder(step=0.0)
