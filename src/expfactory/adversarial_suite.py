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

from expfactory.gates_v1 import DatasetGrouping, DiffEvidence
from expfactory.harness import RunResult
from expfactory.prereg import Guardrail, Preregistration
from expfactory.registry import RunAttestation
from expfactory.verifier import Candidate, Verifier


class Expect(enum.Enum):
    PROMOTE = "promote"
    REJECT = "reject"


class Kind(enum.StrEnum):
    """What a fixture is an example of.

    An enum rather than a bare str with the valid values in a trailing comment
    (GH#12). `Expect` directly above it was already an enum, and the comment had
    drifted badly: it listed four kinds while twelve were in use, so eight ways a
    fixture could be miscategorised were invisible to a reader and to the type
    checker.
    """

    # core gate set
    GENUINE = "genuine"
    SEED_NOISE = "seed_noise"
    LEAKAGE = "leakage"
    HOLDOUT_BURN = "holdout_burn"
    # G-07 / G-08 preregistration
    PREREG_CLEAN = "prereg_clean"
    NO_PREREG = "no_prereg"
    HARKING = "harking"
    METRIC_SWAP = "metric_swap"
    SEED_SHOP = "seed_shop"
    GUARDRAIL = "guardrail"
    EXPLORATORY = "exploratory"
    PREREG_CHURN = "prereg_churn"
    # G-10: evidence for a run that never happened
    FABRICATED = "fabricated"


@dataclass(frozen=True)
class Fixture:
    id: str
    kind: Kind
    expect: Expect
    candidate: Candidate

    def __post_init__(self) -> None:
        # StrEnum accepts its own members and equal strings; this rejects a typo
        # at construction rather than letting it sit in the suite as a category
        # nothing counts.
        object.__setattr__(self, "kind", Kind(self.kind))


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


def _runs(
    metric: float,
    seeds: int = 5,
    overlap: int = 0,
    jitter: float = 0.0,
    spread: float = 0.0,
) -> list[RunResult]:
    """Runs for a fixture.

    `jitter` lifts one seed -- a lottery. `spread` fans all seeds out evenly
    around the metric, which is what ordinary seed noise looks like.

    `spread` was added after the first real dataset run. Every fixture here
    produced *bit-identical* metrics across seeds, and
    `gate_no_single_seed_dominance` happened to pass vacuously on that input
    while being algebraically incapable of passing anything else. Five of five
    correct, against a gate that could not work. Fixtures that never vary cannot
    catch a gate that mishandles variation.
    """
    out: list[RunResult] = []
    for s in range(seeds):
        m = metric + (jitter if s == 0 else 0.0)  # jitter only on one lucky seed
        if spread and seeds > 1:
            # evenly fanned, deterministic: ordinary noise, no single outlier
            m += spread * (s / (seeds - 1) - 0.5)
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
        Fixture("v-genuine-1", Kind.GENUINE, Expect.PROMOTE, cand("genuine strong", _runs(0.85))),
        Fixture("v-genuine-2", Kind.GENUINE, Expect.PROMOTE, cand("genuine modest", _runs(0.78))),
        # Ordinary seed noise must PROMOTE. Absent until the first real dataset
        # run, which is why gate_no_single_seed_dominance shipped rejecting every
        # experiment whose seeds were not bit-identical.
        Fixture(
            "v-genuine-spread-1",
            Kind.GENUINE,
            Expect.PROMOTE,
            cand("genuine with realistic seed noise", _runs(0.85, spread=0.02)),
        ),
        # leakage: train/eval overlap -> reject regardless of metric
        Fixture(
            "v-leak-1",
            Kind.LEAKAGE,
            Expect.REJECT,
            cand("leaky great number", _runs(0.99, overlap=12)),
        ),
        # holdout_burn: tamper diff (proxy for reaching into the lockbox) -> reject
        Fixture(
            "v-burn-1",
            Kind.HOLDOUT_BURN,
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
            Kind.SEED_NOISE,
            Expect.REJECT,
            cand("one lucky seed", _runs(0.50, jitter=0.45)),
        ),
    ]

    heldout = [
        Fixture("h-genuine-1", Kind.GENUINE, Expect.PROMOTE, cand("held genuine", _runs(0.82))),
        Fixture(
            "h-genuine-spread-1",
            Kind.GENUINE,
            Expect.PROMOTE,
            cand("held genuine, realistic noise", _runs(0.80, spread=0.03)),
        ),
        Fixture(
            "h-leak-1", Kind.LEAKAGE, Expect.REJECT, cand("held leaky", _runs(0.97, overlap=8))
        ),
        Fixture(
            "h-burn-1",
            Kind.HOLDOUT_BURN,
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
    parents: list[tuple[str, dict[str, float]]]
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
            # varies per filing so each is distinct, and stays satisfiable by the
            # fixture runs. Used to vary `decision_rule`, which GH#36 turned from
            # a free string into a validated one.
            minimum_effect=0.02 + n * 0.0001,
            seeds=(0, 1, 2),
            parent_id=churn_lineage,
        )
        for n in range(4)
    ]
    p_unfiled = Preregistration(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=0.70,
        minimum_effect=0.0307,
        seeds=(0, 1, 2),
        parent_id=parent_ok,
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
            Kind.PREREG_CLEAN,
            Expect.PROMOTE,
            cand("filed and met", 0.75, prereg_hash=p_ok.hash, parent_id=parent_ok),
        ),
        # the headline mode: primary flat against the declared baseline
        Fixture(
            "p-swap-1",
            Kind.METRIC_SWAP,
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
            Kind.HARKING,
            Expect.REJECT,
            cand("filed after the fact", 0.80, prereg_hash=p_unfiled.hash, parent_id=parent_ok),
        ),
        Fixture(
            "p-seedshop-1",
            Kind.SEED_SHOP,
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
            Kind.EXPLORATORY,
            Expect.REJECT,
            cand("great number, exploratory", 0.99, exploratory=True),
        ),
        Fixture(
            "p-nocite-1", Kind.NO_PREREG, Expect.REJECT, cand("confirmatory with no rule", 0.95)
        ),
        # G-08. Each of the four rules in this lineage is individually honest and
        # passes G-07; only counting across them reveals the shopping. Note the
        # numbers here are *good* — the rejection is about the pattern, not the
        # result.
        Fixture(
            "p-churn-1",
            Kind.PREREG_CHURN,
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
            Kind.PREREG_CLEAN,
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
            Kind.GUARDRAIL,
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
            Kind.EXPLORATORY,
            Expect.REJECT,
            cand("held exploratory", 0.97, exploratory=True),
        ),
    ]
    return PreregSuiteSetup(
        preregs=[p_ok, p_guard, *p_churn],
        # exp_id -> the metric it actually scored. Rule 8 reads baselines from
        # here, never from the preregistration.
        # Metrics, not one number: a guardrail fixture needs its guardrail metric
        # recorded on the parent or it fails closed and a PROMOTE case rejects.
        parents=[
            (parent_ok, {"val_metric": 0.70, "latency_ms": 12.0}),
            (churn_lineage, {"val_metric": 0.70}),
        ],
        suite=Suite(visible, heldout),
    )


# --------------------------------------------------------------------------- #
# G-09 — group-level leakage (invariant 4: every gate traces to a fixture)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GroupSuiteSetup:
    """The G-09 suite plus the task grouping a verifier must be built with.

    Separate from the core suite because the declaration lives on the verifier,
    not on the candidate — deliberately, since a candidate-supplied grouping
    could be omitted by the candidate. Evaluating these fixtures against a
    verifier with `grouping=None` would (correctly) promote the leaky one, which
    is the whole point of the design and would look like a suite failure.
    """

    grouping: DatasetGrouping
    suite: Suite


def _grouped_runs(
    metric: float,
    train_groups: tuple[str, ...],
    eval_groups: tuple[str, ...],
    seeds: int = 5,
    declare: bool = True,
) -> list[RunResult]:
    """Runs whose sample ids are always disjoint, and whose *groups* may not be.

    `overlap_count=0` throughout, on purpose: these fixtures must be invisible to
    `gate_no_leakage`. If a fixture here were also caught by the id-level gate it
    would prove nothing about G-09.
    """
    extra: dict[str, Any] = (
        {"train_groups": list(train_groups), "eval_groups": list(eval_groups)} if declare else {}
    )
    return [
        RunResult(
            seed=s,
            val_metric=metric,
            train_ids_hash="t",
            eval_ids_hash="e",
            overlap_count=0,
            wall_seconds=0.0,
            extra=dict(extra),
        )
        for s in range(seeds)
    ]


def build_group_suite() -> GroupSuiteSetup:
    """Fixtures for the leak that has disjoint sample ids.

    Modelled on the case EchoHawk (arXiv 2606.29589) documents: a dataset
    pre-segmented into clips, split at clip level, so slices of one recording sit
    on both sides. Every id distinct, every existing gate green, the number
    inflated. The paper measures the inflation at roughly five points of
    detection probability.
    """
    grouping = DatasetGrouping(
        group_key="recording_session",
        rationale="clips are segmented from longer continuous captures",
        source="EchoHawk, arXiv:2606.29589",
    )

    def cand(hyp: str, runs: list[RunResult]) -> Candidate:
        return Candidate(hypothesis=hyp, config={"m": "x"}, code_hash=hyp, runs=runs, cost_usd=0.4)

    visible = [
        # Clean: sessions genuinely held apart -> promote.
        Fixture(
            "g-clean-1",
            Kind.GENUINE,
            Expect.PROMOTE,
            cand("sessions held apart", _grouped_runs(0.85, ("s1", "s2", "s3"), ("s9",))),
        ),
        # THE fixture. One shared session, ids fully disjoint. `gate_no_leakage`
        # sees overlap_count == 0 and passes; G-09 must reject.
        Fixture(
            "g-leak-1",
            Kind.LEAKAGE,
            Expect.REJECT,
            cand("clip-level split", _grouped_runs(0.97, ("s1", "s2"), ("s2", "s9"))),
        ),
        # Declared grouping, no group ids recorded -> unproven, so rejected.
        # Fail-closed, or omitting the field becomes the way to pass.
        Fixture(
            "g-undeclared-1",
            Kind.LEAKAGE,
            Expect.REJECT,
            cand("no groups recorded", _grouped_runs(0.88, (), (), declare=False)),
        ),
    ]

    heldout = [
        Fixture(
            "g-clean-2",
            Kind.GENUINE,
            Expect.PROMOTE,
            cand("held clean sessions", _grouped_runs(0.80, ("a", "b"), ("c",))),
        ),
        Fixture(
            "g-leak-2",
            Kind.LEAKAGE,
            Expect.REJECT,
            cand("held one shared site", _grouped_runs(0.93, ("a", "b", "c"), ("c",))),
        ),
    ]
    return GroupSuiteSetup(grouping=grouping, suite=Suite(visible, heldout))


# --------------------------------------------------------------------------- #
# G-10 — the run actually happened (invariant 4)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AttestedSuiteSetup:
    """The G-10 suite plus the job log a verifier must be pointed at.

    Separate from the core suite for the same reason G-09's is: the source lives
    on the verifier, not the candidate. Evaluated against a verifier with
    `attestations=None`, the fabricated fixture would correctly promote — which
    is the design, and would read as a suite failure.
    """

    jobs: FakeJobLog
    suite: Suite


class FakeJobLog:
    """A stand-in for the registry's append-only log.

    Deliberately not a `JobRegistry`: the gate must be a pure function of
    recorded evidence, and a fixture that needed a real registry, a substrate and
    a cost cap to express "this handle was issued" would be testing those instead.
    """

    def __init__(self, issued: dict[str, dict[str, Any]]) -> None:
        self._issued = issued

    def attested_job(self, handle: str) -> dict[str, Any] | None:
        return self._issued.get(handle)


def build_attested_suite() -> AttestedSuiteSetup:
    """Fixtures for a run that never happened.

    Once the runner adjudicates rather than trusting a verdict (GH#33), an
    agent's remaining move is to hand over a `Candidate` describing runs that
    never occurred: plausible seeds, clean overlap, a tidy metric. A fabricated
    record and a genuine one are the same shape, so no gate reading the numbers
    can separate them. Only the job log can.
    """
    jobs = FakeJobLog(
        {
            "BRE-1-real": {
                "handle": "BRE-1-real",
                "ticket": "BRE-1",
                "state": "resolved",
                "exit_code": 0,
                "artifact_sha256": "ab" * 32,
            }
        }
    )
    real = RunAttestation(
        job_handle="BRE-1-real",
        command=("python", "train.py"),
        exit_code=0,
        wall_seconds=1234.0,
        artifact_sha256="ab" * 32,
    )
    invented = RunAttestation(
        job_handle="BRE-1-never-issued",
        command=("python", "train.py"),
        exit_code=0,
        wall_seconds=1234.0,
        artifact_sha256="ab" * 32,
    )

    def cand(hyp: str, attestation: Any) -> Candidate:
        return Candidate(
            hypothesis=hyp,
            config={"m": "x"},
            code_hash=hyp,
            runs=_runs(0.85),
            cost_usd=0.4,
            attestation=attestation,
        )

    visible = [
        Fixture(
            "a-genuine-1",
            Kind.GENUINE,
            Expect.PROMOTE,
            cand("run the registry issued", real),
        ),
        # THE fixture. Numbers identical to a promotable result; no job behind it.
        Fixture(
            "a-invented-1",
            Kind.FABRICATED,
            Expect.REJECT,
            cand("handle the registry never issued", invented),
        ),
        Fixture(
            "a-missing-1",
            Kind.FABRICATED,
            Expect.REJECT,
            cand("no attestation at all", None),
        ),
    ]
    heldout = [
        Fixture(
            "a-genuine-2",
            Kind.GENUINE,
            Expect.PROMOTE,
            cand("held attested run", real),
        ),
        Fixture(
            "a-tampered-2",
            Kind.FABRICATED,
            Expect.REJECT,
            cand(
                "artifact edited after the run",
                RunAttestation(
                    job_handle="BRE-1-real",
                    command=("python", "train.py"),
                    exit_code=0,
                    wall_seconds=1234.0,
                    artifact_sha256="cd" * 32,
                ),
            ),
        ),
    ]
    return AttestedSuiteSetup(jobs=jobs, suite=Suite(visible, heldout))
