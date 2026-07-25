"""
adversarial_suite — the factory's known-answer self-test (ticket 04).

Each fixture is a Candidate plus the verdict the harness MUST reach. Fixtures span
the four ways an empirical result gets faked, plus genuine improvements. A visible
partition tunes the gates; a held-out partition is consulted only to measure
whether tuning generalised — the same holdout discipline the factory enforces on
experiments, turned on the factory itself.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from expfactory.gates_v1 import DiffEvidence
from expfactory.harness import RunResult
from expfactory.prereg import Guardrail, Preregistration
from expfactory.verifier import Candidate, Verifier


class Expect(enum.Enum):
    PROMOTE = "promote"
    REJECT = "reject"


@dataclass(frozen=True)
class Fixture:
    id: str
    kind: str  # genuine | seed_noise | leakage | holdout_burn
    expect: Expect
    candidate: Candidate


@dataclass
class SuiteResult:
    total: int
    correct: int
    mismatches: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def is_pass(self) -> bool:
        # a pass is 'matched every assigned verdict' — NOT 'promoted something'.
        # rejecting every candidate is a pass if every candidate was meant to be
        # rejected (W-03's negative criterion).
        return not self.mismatches


def _runs(metric: float, seeds: int = 5, overlap: int = 0, jitter: float = 0.0) -> list[RunResult]:
    out: list[RunResult] = []
    for s in range(seeds):
        m = metric + (jitter if s == 0 else 0.0)  # jitter only on one lucky seed
        out.append(
            RunResult(
                seed=s,
                val_metric=m,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=overlap,
                wall_seconds=0.0,
            )
        )
    return out


class Suite:
    def __init__(self, fixtures: list[Fixture], heldout: list[Fixture]):
        self._visible = fixtures
        self._heldout = heldout

    def visible_fixtures(self) -> list[Fixture]:
        return list(self._visible)

    def heldout_fixtures(self) -> list[Fixture]:
        return list(self._heldout)

    def evaluate(self, verifier: Verifier, partition: str = "visible") -> SuiteResult:
        # Explicit dispatch: the old `if visible else heldout` form failed OPEN,
        # so any typo silently evaluated — and spent — the held-out partition.
        # That is the exact holdout-burn this suite exists to detect (invariant 5).
        if partition == "visible":
            fixtures = self._visible
        elif partition == "heldout":
            fixtures = self._heldout
        else:
            raise ValueError(f"unknown partition {partition!r}: expected visible|heldout")
        correct, mismatches = 0, []
        for f in fixtures:
            bundle = verifier.run(f.candidate)
            got = Expect.PROMOTE if bundle.promoted else Expect.REJECT
            if got == f.expect:
                correct += 1
            else:
                mismatches.append(
                    f"{f.id} ({f.kind}): expected {f.expect.value}, got {got.value} "
                    f"[blocked_by={list(bundle.blocked_by)}]"
                )
        return SuiteResult(total=len(fixtures), correct=correct, mismatches=mismatches)


def build_suite() -> Suite:
    clean = DiffEvidence(
        added_lines=["    assert ok"], removed_lines=[], touched_paths=["test_m.py"]
    )

    def cand(
        hyp: str,
        runs: list[RunResult],
        cost: float = 0.4,
        diff: DiffEvidence = clean,
    ) -> Candidate:
        return Candidate(
            hypothesis=hyp, config={"m": "x"}, code_hash=hyp, runs=runs, cost_usd=cost, diff=diff
        )

    visible = [
        # genuine: clean, tight, reproducible -> promote
        Fixture("v-genuine-1", "genuine", Expect.PROMOTE, cand("genuine strong", _runs(0.85))),
        Fixture("v-genuine-2", "genuine", Expect.PROMOTE, cand("genuine modest", _runs(0.78))),
        # leakage: train/eval overlap -> reject regardless of metric
        Fixture(
            "v-leak-1",
            "leakage",
            Expect.REJECT,
            cand("leaky great number", _runs(0.99, overlap=12)),
        ),
        # holdout_burn: tamper diff (proxy for reaching into the lockbox) -> reject
        Fixture(
            "v-burn-1",
            "holdout_burn",
            Expect.REJECT,
            cand(
                "removed assertion",
                _runs(0.95),
                diff=DiffEvidence(
                    added_lines=[], removed_lines=["    assert ap>0.7"], touched_paths=["test_m.py"]
                ),
            ),
        ),
        # seed_noise: single-seed spike, indistinguishable from noise band -> reject
        Fixture(
            "v-noise-1",
            "seed_noise",
            Expect.REJECT,
            cand("one lucky seed", _runs(0.50, jitter=0.45)),
        ),
    ]

    heldout = [
        Fixture("h-genuine-1", "genuine", Expect.PROMOTE, cand("held genuine", _runs(0.82))),
        Fixture("h-leak-1", "leakage", Expect.REJECT, cand("held leaky", _runs(0.97, overlap=8))),
        Fixture(
            "h-burn-1",
            "holdout_burn",
            Expect.REJECT,
            cand(
                "held tamper",
                _runs(0.9),
                diff=DiffEvidence(
                    added_lines=["@pytest.mark.skip"], removed_lines=[], touched_paths=["test_m.py"]
                ),
            ),
        ),
    ]
    return Suite(visible, heldout)


# --------------------------------------------------------------------------- #
# G-07 fixtures (ticket N-03)
#
# Invariant 4 says every gate traces to a fixture *in the adversarial suite*, not
# merely to a unit test. G-07 needs its own builder because its fixtures are only
# meaningful against a verifier configured with require_prereg=True and a store
# holding the preregistrations — a configuration the base suite deliberately does
# not use.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PreregSuiteSetup:
    """Everything needed to stand up the G-07/G-08 suite against a real ledger."""

    preregs: list[Preregistration]
    parents: list[tuple[str, float]]
    suite: Suite


def build_prereg_suite() -> PreregSuiteSetup:
    """Return the preregistrations that must be filed first, plus the suite.

    The caller records `parents`, files `preregs`, points a verifier at the ledger
    and evaluates. Both steps matter and for different reasons: filing order is
    what rule 2 tests (so `p_unfiled` is deliberately absent from `preregs`), and
    the recorded parent results are what rule 8 checks declared baselines against.
    """
    # Lineage ids the fixtures descend from. Recorded as real verdicts by the
    # caller before evaluation, because rule 8 checks each declared baseline
    # against what the parent actually scored.
    parent_ok = "sfx-parent"
    churn_lineage = "lineage-shopping"

    p_ok = Preregistration(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=0.70,
        minimum_effect=0.02,
        seeds=(0, 1, 2),
        parent_id=parent_ok,
    )
    p_guard = Preregistration(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=0.70,
        minimum_effect=0.02,
        seeds=(0, 1, 2),
        parent_id=parent_ok,
        guardrails=(Guardrail("latency_ms", "minimize"),),
    )
    p_churn = [
        Preregistration(
            primary_metric="val_metric",
            direction="maximize",
            baseline_value=0.70,
            minimum_effect=0.02,
            seeds=(0, 1, 2),
            parent_id=churn_lineage,
            decision_rule=f"attempt_{n}",
        )
        for n in range(4)
    ]
    p_unfiled = Preregistration(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=0.70,
        minimum_effect=0.02,
        seeds=(0, 1, 2),
        parent_id=parent_ok,
        decision_rule="never_filed",
    )

    def runs(
        metric: float, seeds: tuple[int, ...], extra: dict[str, float] | None = None
    ) -> list[RunResult]:
        return [
            RunResult(
                seed=s,
                val_metric=metric,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=0,
                wall_seconds=0.0,
                extra=dict(extra or {}),
            )
            for s in seeds
        ]

    def cand(hyp: str, metric: float, seeds: tuple[int, ...] = (0, 1, 2), **over: Any) -> Candidate:
        return Candidate(
            hypothesis=hyp,
            config={"m": "x"},
            code_hash=hyp,
            runs=runs(metric, seeds, over.pop("extra", None)),
            cost_usd=0.4,
            **over,
        )

    visible = [
        Fixture(
            "p-genuine-1",
            "prereg_clean",
            Expect.PROMOTE,
            cand("filed and met", 0.75, prereg_hash=p_ok.hash, parent_id=parent_ok),
        ),
        # the headline mode: primary flat against the declared baseline
        Fixture(
            "p-swap-1",
            "metric_swap",
            Expect.REJECT,
            cand(
                "primary flat, latency better",
                0.70,
                prereg_hash=p_ok.hash,
                parent_id=parent_ok,
                extra={"latency_ms": 5.0},
            ),
        ),
        Fixture(
            "p-hark-1",
            "harking",
            Expect.REJECT,
            cand("filed after the fact", 0.80, prereg_hash=p_unfiled.hash, parent_id=parent_ok),
        ),
        Fixture(
            "p-seedshop-1",
            "seed_shop",
            Expect.REJECT,
            cand(
                "reported the best two",
                0.80,
                seeds=(0, 1),
                prereg_hash=p_ok.hash,
                parent_id=parent_ok,
            ),
        ),
        Fixture(
            "p-explore-1",
            "exploratory",
            Expect.REJECT,
            cand("great number, exploratory", 0.99, exploratory=True),
        ),
        Fixture("p-nocite-1", "no_prereg", Expect.REJECT, cand("confirmatory with no rule", 0.95)),
        # G-08. Each of the four rules in this lineage is individually honest and
        # passes G-07; only counting across them reveals the shopping. Note the
        # numbers here are *good* — the rejection is about the pattern, not the
        # result.
        Fixture(
            "p-churn-1",
            "prereg_churn",
            Expect.REJECT,
            cand(
                "fourth rule filed, none landed",
                0.75,
                prereg_hash=p_churn[-1].hash,
                parent_id=churn_lineage,
            ),
        ),
    ]

    heldout = [
        Fixture(
            "hp-genuine-1",
            "prereg_clean",
            Expect.PROMOTE,
            cand(
                "held filed and met",
                0.78,
                prereg_hash=p_guard.hash,
                parent_id=parent_ok,
                extra={"latency_ms": 11.0},
            ),
        ),
        Fixture(
            "hp-guardrail-1",
            "guardrail",
            Expect.REJECT,
            cand(
                "real gain, latency blown",
                0.85,
                prereg_hash=p_guard.hash,
                extra={"latency_ms": 44.0},
            ),
        ),
        Fixture(
            "hp-explore-1",
            "exploratory",
            Expect.REJECT,
            cand("held exploratory", 0.97, exploratory=True),
        ),
    ]
    return PreregSuiteSetup(
        preregs=[p_ok, p_guard, *p_churn],
        # exp_id -> the metric it actually scored. Rule 8 reads baselines from
        # here, never from the preregistration.
        parents=[(parent_ok, 0.70), (churn_lineage, 0.70)],
        suite=Suite(visible, heldout),
    )
