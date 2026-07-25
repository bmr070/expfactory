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

from expfactory.gates_v1 import DiffEvidence
from expfactory.verifier import Candidate, Verifier


class Expect(enum.Enum):
    PROMOTE = "promote"
    REJECT = "reject"


@dataclass(frozen=True)
class Fixture:
    id: str
    kind: str            # genuine | seed_noise | leakage | holdout_burn
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


def _runs(metric, seeds=5, overlap=0, jitter=0.0):
    out = []
    for s in range(seeds):
        m = metric + (jitter if s == 0 else 0.0)   # jitter only on one lucky seed
        out.append(dict(seed=s, val_metric=m, train_ids_hash="t",
                        eval_ids_hash="e", overlap_count=overlap, wall_seconds=0.0))
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
        fixtures = self._visible if partition == "visible" else self._heldout
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
    clean = DiffEvidence(added_lines=["    assert ok"], removed_lines=[],
                         touched_paths=["test_m.py"])

    def cand(hyp, runs, cost=0.4, diff=clean):
        return Candidate(hypothesis=hyp, config={"m": "x"}, code_hash=hyp,
                         runs=runs, cost_usd=cost, diff=diff)

    visible = [
        # genuine: clean, tight, reproducible -> promote
        Fixture("v-genuine-1", "genuine", Expect.PROMOTE,
                cand("genuine strong", _runs(0.85))),
        Fixture("v-genuine-2", "genuine", Expect.PROMOTE,
                cand("genuine modest", _runs(0.78))),
        # leakage: train/eval overlap -> reject regardless of metric
        Fixture("v-leak-1", "leakage", Expect.REJECT,
                cand("leaky great number", _runs(0.99, overlap=12))),
        # holdout_burn: tamper diff (proxy for reaching into the lockbox) -> reject
        Fixture("v-burn-1", "holdout_burn", Expect.REJECT,
                cand("removed assertion", _runs(0.95),
                     diff=DiffEvidence(added_lines=[], removed_lines=["    assert ap>0.7"],
                                       touched_paths=["test_m.py"]))),
        # seed_noise: single-seed spike, indistinguishable from noise band -> reject
        Fixture("v-noise-1", "seed_noise", Expect.REJECT,
                cand("one lucky seed", _runs(0.50, jitter=0.45))),
    ]

    heldout = [
        Fixture("h-genuine-1", "genuine", Expect.PROMOTE,
                cand("held genuine", _runs(0.82))),
        Fixture("h-leak-1", "leakage", Expect.REJECT,
                cand("held leaky", _runs(0.97, overlap=8))),
        Fixture("h-burn-1", "holdout_burn", Expect.REJECT,
                cand("held tamper", _runs(0.9),
                     diff=DiffEvidence(added_lines=["@pytest.mark.skip"], removed_lines=[],
                                       touched_paths=["test_m.py"]))),
    ]
    return Suite(visible, heldout)
