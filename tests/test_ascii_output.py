"""
Everything this package prints for a human is ASCII.

Third time this has bitten (GH#28). The substrate guard's blocking message, the
local substrate's CLI, and `report()` each shipped with a character above
U+007F, and each rendered as a replacement character on the Windows console this
runs on. Mojibake in a verdict reads as a broken tool rather than a considered
result, which is corrosive precisely where the output is meant to carry
authority.

The first two fixes were one-off tests next to each function. That is the shape
that guarantees a fourth occurrence, so this generalises it: the check is over a
*registry* of output-producing calls, and adding a new one is a line here rather
than a new file.

Docstrings and comments are deliberately out of scope — they are never printed,
and the repository uses em-dashes throughout its prose.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from expfactory.gates_v1 import DatasetGrouping, gate_no_group_leakage
from expfactory.harness import Experiment, GateResult, RunResult, report
from expfactory.local_substrate import GpuInfo, describe_local_compute
from expfactory.runner import proof_of_work
from expfactory.verifier import Candidate, GateVerifier


def _experiment() -> Experiment:
    exp = Experiment(
        exp_id="e1",
        parent_id="p0",
        hypothesis="a hypothesis",
        config={"lr": 0.01},
        code_hash="abc",
        cost_usd=0.4,
    )
    exp.runs = [RunResult(s, 0.8 + s * 0.01, "t", "e", 0, 0.0) for s in range(3)]
    exp.gates = [GateResult("no_leakage", True, "train/eval disjoint")]
    return exp


def _bundle():
    runs = [RunResult(s, 0.8, "t", "e", 0, 0.0) for s in range(3)]
    return GateVerifier(id_factory=lambda: "e1").run(
        Candidate(hypothesis="h", config={}, code_hash="c", runs=runs, cost_usd=0.4)
    )


def _rejected_bundle():
    runs = [RunResult(s, 0.9, "t", "e", 11, 0.0) for s in range(3)]
    return GateVerifier(id_factory=lambda: "e2").run(
        Candidate(hypothesis="h", config={}, code_hash="c", runs=runs, cost_usd=0.4)
    )


def _group_leak_detail() -> str:
    exp = _experiment()
    for r in exp.runs:
        r.extra = {"train_groups": ["s1", "s2"], "eval_groups": ["s2"]}
    grouping = DatasetGrouping("recording_session", "clips cut from long captures")
    return gate_no_group_leakage(exp, grouping=grouping).detail


# Every function whose return value or stdout a human reads. Add to this list
# rather than writing another one-off test file.
PRODUCERS: dict[str, Callable[[], str]] = {
    "report (promoted)": lambda: report(_experiment()),
    "report (with baseline)": lambda: report(_experiment(), baseline=_experiment()),
    "proof_of_work (promoted)": lambda: proof_of_work(_bundle()),
    "proof_of_work (rejected)": lambda: proof_of_work(_rejected_bundle()),
    "describe_local_compute (no gpu)": lambda: describe_local_compute(prober=list),
    "describe_local_compute (gpu)": lambda: describe_local_compute(
        prober=lambda: [GpuInfo(0, "Test Card", 12282, 1227, 200.0)]
    ),
    "gate_no_group_leakage detail": _group_leak_detail,
}


@pytest.mark.parametrize("name", sorted(PRODUCERS))
def test_human_facing_output_is_ascii(name: str):
    text = PRODUCERS[name]()
    offenders = sorted({f"U+{ord(c):04X} {c!r}" for c in text if ord(c) > 127})
    assert not offenders, f"non-ASCII in {name}: {offenders}"


def test_the_registry_is_not_empty():
    """Guards the guard. An empty or mis-typed registry would make the
    parametrised test above vacuously pass."""
    assert len(PRODUCERS) >= 6
    for name, fn in PRODUCERS.items():
        assert fn().strip(), f"{name} produced nothing to check"


def test_every_gate_detail_the_suite_can_produce_is_ascii():
    """The registry above only covers the branches its fixtures happen to hit,
    which is how `gate_too_good`'s escalation message kept a U+2014 through the
    first pass of this file.

    The adversarial suite exists to drive every gate down both paths, so drive it
    and read every detail string it produces. Cheap, and it does not depend on
    anyone remembering to add a fixture here when they add a gate.
    """
    from expfactory.adversarial_suite import build_group_suite, build_suite
    from expfactory.selfcheck import run, run_group, run_prereg

    # Exercise all three suites so a mismatch message would surface too.
    for result in (run(), run_prereg(), run_group()):
        for line in result.mismatches:
            assert all(ord(c) < 128 for c in line), f"non-ASCII in mismatch: {line!r}"

    verifier = GateVerifier()
    fixtures = [
        *build_suite().visible_fixtures(),
        *build_suite().heldout_fixtures(),
        *build_group_suite().suite.visible_fixtures(),
    ]
    offenders: list[str] = []
    for fixture in fixtures:
        bundle = verifier.run(fixture.candidate)
        for gate in bundle.artifact.get("gates", []):
            text = f"{gate['name']}: {gate['detail']}"
            bad = sorted({f"U+{ord(c):04X}" for c in text if ord(c) > 127})
            if bad:
                offenders.append(f"{fixture.id} -> {text} {bad}")

    assert not offenders, "non-ASCII in gate details:\n  " + "\n  ".join(offenders)
