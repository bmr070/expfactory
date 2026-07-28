"""
gate_probe — properties every gate must satisfy, checked by search rather than by
a hand-written fixture.

## Why this exists

W-03 makes the labeled adversarial suite *the* acceptance criterion for the
factory. On 2026-07-27 that suite reported **5/5 correct** while
`gate_no_single_seed_dominance` was algebraically incapable of passing anything
whose seeds were not bit-identical — a blocking gate that would have rejected
every real experiment forever.

The fixtures missed it because they all produced identical metrics per seed. A
fixture asserts a *point*; it cannot assert a *property*, and "this gate can
sometimes pass" is a property.

MAP.md flagged this on day one: *"its own demo scenarios are miscalibrated and it
has no eval of itself."* The suite was the answer to the second half. This is the
part the suite cannot do for itself.

## Why a fuzzer before an agent

The obvious next step is an LLM agent trying to fool the gate set — the
hacker-fixer loop of arXiv:2606.08960, against the threat model reward-hacking
benchmarks describe as *"agents targeting weaknesses in the task setup, harness,
parsing, or workflow constraints"* (arXiv:2605.02964).

That is worth building, and it is not the cheapest sufficient point. W-11's
ladder puts a deterministic check below a hook, below CI, below prose — and a
property sweep is deterministic, free, runs in CI, and would have caught the
2026-07-27 bug in under a second.

It also catches the direction an adversary would not. A prober rewarded for
getting promoted probes for **false accepts**. The dominance bug was a **false
reject**, and would have shown up to such an agent only as inexplicable failure.
Both directions are checked here.

The seam is deliberately the same shape a prober would use — build candidates,
run them, compare verdicts to what is true by construction — so an LLM prober
plugs in later without this being rewritten.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field

from expfactory.harness import RunResult
from expfactory.verifier import Candidate, GateVerifier, VerdictBundle


@dataclass(frozen=True)
class Probe:
    """One generated candidate plus what must be true of the verdict.

    `expect_blocking` names gates that MUST fire; `expect_quiet` names gates that
    MUST NOT. Both may be empty — many probes only assert a property across a
    *family* of candidates, which `sweep` handles.
    """

    label: str
    candidate: Candidate
    expect_blocking: frozenset[str] = frozenset()
    expect_quiet: frozenset[str] = frozenset()


@dataclass
class ProbeFinding:
    """A gate disagreeing with what is true by construction."""

    probe: str
    gate: str
    detail: str

    def __str__(self) -> str:
        return f"{self.probe}: {self.gate} -- {self.detail}"


@dataclass
class ProbeReport:
    checked: int = 0
    findings: list[ProbeFinding] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.findings

    def __str__(self) -> str:
        if self.is_clean:
            return f"{self.checked} probes, no disagreements"
        lines = [f"{self.checked} probes, {len(self.findings)} disagreement(s):"]
        lines += [f"  {f}" for f in self.findings]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Candidate construction
# --------------------------------------------------------------------------- #


def runs(
    metric: float,
    seeds: int = 5,
    spread: float = 0.0,
    outlier: float = 0.0,
    overlap: int = 0,
    groups: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
) -> list[RunResult]:
    """Runs with controllable, *independent* knobs.

    `spread` fans every seed evenly — ordinary noise. `outlier` lifts one seed
    above the rest — a lottery. Keeping them separate is the point: the gate that
    broke conflated them, and a generator that could not express "spread without
    an outlier" could not have exposed it.
    """
    out: list[RunResult] = []
    for s in range(seeds):
        value = metric
        if spread and seeds > 1:
            value += spread * (s / (seeds - 1) - 0.5)
        if outlier and s == seeds - 1:
            value += outlier
        extra: dict[str, object] = {}
        if groups is not None:
            extra = {"train_groups": list(groups[0]), "eval_groups": list(groups[1])}
        out.append(
            RunResult(
                seed=s,
                val_metric=value,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=overlap,
                wall_seconds=0.0,
                extra=extra,
            )
        )
    return out


def candidate(label: str, **kwargs: object) -> Candidate:
    return Candidate(
        hypothesis=label,
        config={},
        code_hash=label,
        runs=runs(**kwargs),  # type: ignore[arg-type]
        cost_usd=0.4,
    )


def _gate(bundle: VerdictBundle, name: str) -> dict[str, object] | None:
    for gate in bundle.artifact.get("gates", []):
        if gate["name"] == name:
            return dict(gate)
    return None


# --------------------------------------------------------------------------- #
# The properties
# --------------------------------------------------------------------------- #


def probe_every_blocking_gate_can_pass(verifier: GateVerifier) -> Iterator[ProbeFinding]:
    """**The one that would have caught the 2026-07-27 bug.**

    A blocking gate that no input can satisfy is not strict, it is broken: it
    rejects every experiment forever, and a suite whose fixtures happen to sit in
    its one passing corner reports it as working.

    Sweeps a family of plainly-innocuous candidates — varying seed count and
    ordinary noise, no leakage, no outlier, no tampering — and reports any
    blocking gate that fires on all of them.
    """
    family = [
        ("identical seeds", {"metric": 0.80, "seeds": 5}),
        ("tiny spread", {"metric": 0.80, "seeds": 5, "spread": 0.002}),
        ("ordinary noise", {"metric": 0.80, "seeds": 5, "spread": 0.02}),
        ("wide noise", {"metric": 0.80, "seeds": 5, "spread": 0.10}),
        ("many seeds", {"metric": 0.80, "seeds": 20, "spread": 0.02}),
        ("three seeds", {"metric": 0.80, "seeds": 3, "spread": 0.02}),
        ("low metric", {"metric": 0.20, "seeds": 5, "spread": 0.02}),
    ]

    ever_passed: dict[str, bool] = {}
    for label, kwargs in family:
        bundle = verifier.run(candidate(label, **kwargs))
        for gate in bundle.artifact.get("gates", []):
            if gate["name"] in bundle.blocked_by or not gate["passed"]:
                ever_passed.setdefault(gate["name"], False)
            else:
                ever_passed[gate["name"]] = True

    for name, passed in sorted(ever_passed.items()):
        if not passed:
            yield ProbeFinding(
                probe="every-blocking-gate-can-pass",
                gate=name,
                detail=(
                    "rejected all "
                    f"{len(family)} innocuous candidates. A blocking gate no input can "
                    "satisfy rejects every experiment forever."
                ),
            )


# A candidate that SHOULD make each gate fire. Gates absent from this map are
# out of scope for the can-fail probe rather than assumed broken: most gates are
# conditional on evidence a bare candidate does not carry (`cost` needs a cap
# breach, `holdout_budget` a ledger, `attested_run` a source, `no_group_leakage`
# a declared grouping), and reporting those as "never fires" produced six false
# positives against a healthy gate set on the first run.
#
# Six false alarms is worse than none: it teaches a reader to skim the output,
# which is how a wall becomes a formality. Uncovered gates are reported as
# missing coverage instead, which is a real and different signal.
_TRIGGERS: dict[str, dict[str, object]] = {
    "no_leakage": {"metric": 0.99, "seeds": 5, "overlap": 500},
    "no_single_seed_dominance": {"metric": 0.50, "seeds": 5, "outlier": 0.45},
    "seed_variance": {"metric": 0.80, "seeds": 2},
}


def probe_every_blocking_gate_can_fail(verifier: GateVerifier) -> Iterator[ProbeFinding]:
    """The mirror of the can-pass probe: a gate that fires on nothing is
    decoration.

    Only checks gates with a declared trigger. A gate the sweep cannot provoke
    is a *coverage* gap, reported by `uncovered_gates`, not evidence of a bug.
    """
    for gate_name, kwargs in sorted(_TRIGGERS.items()):
        bundle = verifier.run(candidate(f"trigger-{gate_name}", **kwargs))
        gate = _gate(bundle, gate_name)
        if gate is None:
            yield ProbeFinding(
                probe="every-blocking-gate-can-fail",
                gate=gate_name,
                detail="gate did not run at all on input built to trigger it",
            )
        elif gate["passed"]:
            yield ProbeFinding(
                probe="every-blocking-gate-can-fail",
                gate=gate_name,
                detail=f"passed input built to trigger it: {gate['detail']}",
            )


def uncovered_gates(verifier: GateVerifier) -> list[str]:
    """Blocking gates with no declared trigger.

    Not a finding — a to-do list. Each one is a gate nothing here can prove is
    capable of firing, which is the coverage question W-09 asks of the fixture
    suite and this asks of itself.
    """
    bundle = verifier.run(candidate("baseline", metric=0.80, seeds=5))
    names = {g["name"] for g in bundle.artifact.get("gates", [])}
    return sorted(names - set(_TRIGGERS))


def probe_noise_does_not_flip_a_verdict(verifier: GateVerifier) -> Iterator[ProbeFinding]:
    """Adding ordinary seed noise around a fixed mean must not turn a promotion
    into a rejection.

    Noise is what real experiments have. A gate set that promotes the noiseless
    case and rejects the noisy one is calibrated against a world that does not
    exist — exactly the shape of the 2026-07-27 bug.
    """
    clean = verifier.run(candidate("noiseless", metric=0.80, seeds=5))
    if not clean.promoted:
        return  # nothing to compare against; other probes cover that

    for spread in (0.001, 0.01, 0.05, 0.15):
        noisy = verifier.run(candidate(f"noise-{spread}", metric=0.80, seeds=5, spread=spread))
        if not noisy.promoted:
            yield ProbeFinding(
                probe="noise-does-not-flip-a-verdict",
                gate=", ".join(noisy.blocked_by),
                detail=(
                    f"a promotable result rejected once seeds spread by {spread}. "
                    "Real experiments are never noiseless."
                ),
            )


def probe_more_leakage_never_helps(verifier: GateVerifier) -> Iterator[ProbeFinding]:
    """Monotonicity. Increasing overlap must never improve a verdict."""
    previous_ok = True
    for overlap in (0, 1, 10, 100, 1000):
        bundle = verifier.run(
            candidate(f"overlap-{overlap}", metric=0.90, seeds=5, overlap=overlap)
        )
        if bundle.promoted and not previous_ok:
            yield ProbeFinding(
                probe="more-leakage-never-helps",
                gate="no_leakage",
                detail=f"overlap={overlap} promoted after a smaller overlap was rejected",
            )
        previous_ok = bundle.promoted


def probe_group_leakage_is_caught_when_declared(
    verifier_with_grouping: GateVerifier,
) -> Iterator[ProbeFinding]:
    """With a grouping declared, shared groups must block however clean the ids."""
    leaky = candidate(
        "shared-session",
        metric=0.90,
        seeds=5,
        overlap=0,
        groups=(("s1", "s2"), ("s2", "s3")),
    )
    bundle = verifier_with_grouping.run(leaky)
    if "no_group_leakage" not in bundle.blocked_by:
        yield ProbeFinding(
            probe="group-leakage-is-caught-when-declared",
            gate="no_group_leakage",
            detail="shared groups with disjoint sample ids did not block",
        )


PROBES: tuple[Callable[[GateVerifier], Iterator[ProbeFinding]], ...] = (
    probe_every_blocking_gate_can_pass,
    probe_every_blocking_gate_can_fail,
    probe_noise_does_not_flip_a_verdict,
    probe_more_leakage_never_helps,
)


def sweep(
    verifier: GateVerifier | None = None,
    probes: Sequence[Callable[[GateVerifier], Iterator[ProbeFinding]]] = PROBES,
) -> ProbeReport:
    """Run every property against a verifier and report disagreements.

    Returns rather than raises, so a caller can print the whole picture instead
    of stopping at the first one — a gate set with three problems should say so
    in one run.
    """
    verifier = verifier or GateVerifier()
    report = ProbeReport()
    for probe in probes:
        report.checked += 1
        report.findings.extend(probe(verifier))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m expfactory.gate_probe`"""
    from expfactory.gates_v1 import DatasetGrouping

    report = sweep()
    print(report)

    grouping = DatasetGrouping("recording_session", "probe")
    grouped = list(probe_group_leakage_is_caught_when_declared(GateVerifier(grouping=grouping)))
    report.checked += 1
    report.findings.extend(grouped)
    for finding in grouped:
        print(f"  {finding}")

    print("verdict:", "PASS" if report.is_clean else "FAIL")
    return 0 if report.is_clean else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
