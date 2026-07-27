"""
scorer — who computes the number (T-01, GH#39).

G-10 established that a run *happened*. It could not establish that the number
was right, because the agent writes both the training code and the code that
computes the metric. Attestation proves the execution; it cannot prove the
semantics.

So this splits them. The agent's job receives holdout **inputs** and emits a
prediction per sample id. Trusted code here holds the **labels** and computes the
score. There is no field anywhere for an agent to report a metric.

## Predictions, not a model

The obvious alternative — the agent submits a trained model, the scorer loads and
runs it — was rejected on security grounds. Loading an agent-authored model
executes agent-authored code *inside the process holding the labels*. A pickle is
arbitrary code by construction; most checkpoint formats allow custom layers. That
hands the untrusted party a shell next to the one secret this module exists to
protect.

Predictions are data. The interface is a mapping from sample id to a value, it
admits any model in any framework, and nothing the agent produced is ever
executed here.

## The Ladder

Predictions-only does not stop the agent fingerprinting the holdout inputs across
runs and overfitting by repeated submission.

Blum & Hardt (ICML 2015) show the leak is driven by **feedback**, not by queries:
an analyst told their exact score every time overfits fast; one told only "you
improved" learns far less per query. So a score is reported only when it beats
the incumbent by more than a step, and otherwise the answer is "no improvement"
and nothing else.

The counting budget stays as a backstop, because the Ladder bounds the *error*
and does not bound the *spend*.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class SubmissionRefused(ValueError):
    """The predictions do not match the holdout they claim to answer.

    Raised rather than scored-around. Silently scoring a partial submission would
    let an agent raise its number by declining to predict the samples it finds
    hard, and the result would look like an ordinary one.
    """


@dataclass(frozen=True)
class ScoreResult:
    """What the scorer produced. The only source of a metric in the system."""

    value: float
    scored: int
    metric: str = "accuracy"


class LabelStore:
    """Ground truth, and no way to read it.

    There is deliberately no accessor — not a public one, not a `get_labels()`,
    not a debug helper. A label store that can be read is one that will be read,
    and the party asking is the agent session. `sample_ids()` is public because
    the agent must know *which* samples to predict; the answers are not.

    A test asserts no public attribute of this class returns a mapping, so adding
    a convenience accessor later fails loudly rather than quietly widening the
    boundary.
    """

    def __init__(self, labels: Mapping[str, Any]) -> None:
        if not labels:
            raise ValueError("a label store with no labels would score everything as perfect")
        # Name-mangled and copied. Mangling is not a security control — anyone
        # with the process can reach it — but it makes an accidental read
        # obviously deliberate, which is the honest thing a language can offer.
        self.__labels = dict(labels)

    def sample_ids(self) -> tuple[str, ...]:
        """The ids an agent must produce predictions for. Sorted, so a run is
        reproducible and the order carries no information."""
        return tuple(sorted(self.__labels))

    def __len__(self) -> int:
        return len(self.__labels)

    def __repr__(self) -> str:
        # Deliberately does not render the labels. A repr in a log or a traceback
        # is one of the easiest ways for a secret to escape.
        return f"LabelStore({len(self.__labels)} labels, hidden)"

    # -- scoring, which is the only thing that may read the labels -------------

    def _score(self, predictions: Mapping[str, Any]) -> ScoreResult:
        """Reads `self.__labels` and returns a number. Deliberately the only
        method that touches them, and it returns a scalar rather than anything
        the caller could invert back into ground truth."""
        if not predictions:
            raise SubmissionRefused("empty submission: nothing to score")

        expected = set(self.__labels)
        got = set(predictions)

        unknown = sorted(got - expected)
        if unknown:
            raise SubmissionRefused(
                f"predictions for {len(unknown)} unknown sample id(s), e.g. {unknown[:5]}: "
                "this submission is answering a different set than the holdout"
            )
        missing = sorted(expected - got)
        if missing:
            raise SubmissionRefused(
                f"{len(missing)} missing prediction(s), e.g. {missing[:5]}. "
                "A partial submission is refused rather than scored: skipping the "
                "hard samples would raise the number and look like a real result."
            )

        correct = sum(1 for sid, truth in self.__labels.items() if predictions[sid] == truth)
        return ScoreResult(value=correct / len(self.__labels), scored=len(self.__labels))


def score(store: LabelStore, predictions: Mapping[str, Any]) -> ScoreResult:
    """Score a submission against a label store.

    A module-level function rather than a public method, so that the call site
    reads as "trusted code scores this" rather than as something the submission
    does to itself.
    """
    return store._score(predictions)


# --------------------------------------------------------------------------- #
# The Ladder
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LadderAnswer:
    """What an agent is told about its submission.

    `value` is None whenever `improved` is False. That is the mechanism, not an
    error case: the whole point is that a submission which did not clearly win
    learns one bit rather than a number to hill-climb against.
    """

    improved: bool
    value: float | None = None


class Ladder:
    """Limited-feedback holdout, after Blum & Hardt (ICML 2015).

    Reports a score only when it beats the incumbent by more than `step`;
    otherwise the answer is "no improvement" and nothing else. Bounds leaderboard
    error at roughly log(k)^(2/3) / n^(1/3) in a fully adaptive model, where k is
    the number of submissions and n the holdout size.

    **What it does not do**, because a test here previously claimed otherwise: it
    does not stop the incumbent rising. A genuinely better model should be
    recognised. It limits how *often* a number is handed back — twenty
    submissions each a hair better yield a handful of disclosures rather than
    twenty.

    A randomised variant reaches O(1/n^0.4) (arXiv:1706.02733). Not implemented:
    it needs a seeded source this class does not have, and the deterministic form
    is already the large win over exact feedback.
    """

    def __init__(self, step: float = 0.01, best: float | None = None) -> None:
        if step <= 0:
            raise ValueError(
                f"step must be positive, got {step}: a step of zero is exact feedback, "
                "which is the thing this mechanism replaces"
            )
        self._step = step
        self._best = best

    @property
    def best(self) -> float | None:
        """The incumbent. Readable by the factory; never returned to an agent
        except through `report`."""
        return self._best

    def report(self, value: float) -> LadderAnswer:
        """Judge a submission and decide what the agent is allowed to learn."""
        if self._best is None:
            # Nothing to beat. Withholding here would cost a query and tell the
            # agent nothing.
            self._best = value
            return LadderAnswer(improved=True, value=value)

        if value > self._best + self._step:
            self._best = value
            return LadderAnswer(improved=True, value=value)

        # Deliberately silent about direction and distance. Reporting how far
        # short it fell would leak the same gradient the mechanism withholds.
        return LadderAnswer(improved=False, value=None)
