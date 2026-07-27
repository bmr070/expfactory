"""
G-10: the run behind these numbers actually happened.

The second half of GH#33. Moving adjudication to the runner (#35) stopped the
agent *declaring a verdict*. It did not stop the agent inventing the evidence a
verdict is computed from, and that is the move it has left: hand over a
`Candidate` describing runs that never occurred — five plausible seeds, clean
overlap counts, a tidy metric.

No gate that reads the numbers can catch that, because a fabricated record and a
genuine one are the same shape. Only the registry's append-only log can, and the
agent does not write it.

The load-bearing test is the first one: fabricated evidence that every *other*
gate promotes.
"""

from __future__ import annotations

from typing import Any

import pytest

from expfactory.gates_v1 import gate_attested_run
from expfactory.harness import Experiment, RunResult
from expfactory.registry import RunAttestation
from expfactory.verifier import Candidate, GateVerifier

REAL = RunAttestation(
    job_handle="BRE-1-real",
    command=("python", "train.py"),
    exit_code=0,
    wall_seconds=1234.0,
    artifact_sha256="ab" * 32,
)


class FakeLog:
    def __init__(self, issued: dict[str, dict[str, Any]] | None = None) -> None:
        self._issued = (
            issued
            if issued is not None
            else {
                "BRE-1-real": {
                    "handle": "BRE-1-real",
                    "ticket": "BRE-1",
                    "state": "resolved",
                    "exit_code": 0,
                    "artifact_sha256": "ab" * 32,
                }
            }
        )

    def attested_job(self, handle: str) -> dict[str, Any] | None:
        return self._issued.get(handle)


def _candidate(attestation: Any = REAL, metric: float = 0.85) -> Candidate:
    runs = [RunResult(s, metric, "t", "e", 0, 0.0) for s in range(5)]
    return Candidate(
        hypothesis="h",
        config={},
        code_hash="c",
        runs=runs,
        cost_usd=0.4,
        attestation=attestation,
    )


def _exp() -> Experiment:
    exp = Experiment(exp_id="e1", parent_id=None, hypothesis="h", config={}, code_hash="c")
    exp.runs = [RunResult(s, 0.85, "t", "e", 0, 0.0) for s in range(5)]
    return exp


# --------------------------------------------------------------------------- #


def test_fabricated_evidence_passes_every_other_gate_and_this_one_stops_it():
    """The reason G-10 exists, stated as the attack.

    The candidate is clean by every measure the other gates have: no id overlap,
    no seed lottery, no tamper diff, a plausible metric. It describes a run that
    never happened.
    """
    invented = RunAttestation(
        job_handle="never-issued",
        command=("python", "train.py"),
        exit_code=0,
        wall_seconds=1234.0,
        artifact_sha256="ab" * 32,
    )
    candidate = _candidate(invented)

    # every other gate is happy
    assert GateVerifier().run(candidate).promoted

    # and the log says this run does not exist
    bundle = GateVerifier(attestations=FakeLog()).run(candidate)
    assert not bundle.promoted
    assert "attested_run" in bundle.blocked_by


def test_a_run_the_registry_issued_is_accepted():
    assert GateVerifier(attestations=FakeLog()).run(_candidate()).promoted


def test_no_attestation_at_all_blocks_when_a_log_is_configured():
    """Fail-closed. If omitting the field were a pass, omitting it would be the
    technique — the same reasoning as G-09's undeclared-groups case."""
    result = gate_attested_run(_exp(), attestation=None, attestations=FakeLog())
    assert not result.passed and result.blocking


def test_no_log_configured_warns_without_blocking():
    """The deterministic lane and the one-off fixtures have no job behind them.
    Blocking there would make the gate something everyone routes around — but the
    detail has to say plainly that nothing was checked, rather than reading as a
    clean bill of health."""
    result = gate_attested_run(_exp(), attestation=None, attestations=None)

    assert result.passed and not result.blocking
    assert "no check was made" in result.detail


def test_an_artifact_edited_after_the_run_is_caught():
    """The handle is real and the job did happen. The bytes are not the ones the
    substrate recorded, so the numbers are no longer bound to that run."""
    tampered = RunAttestation(
        job_handle="BRE-1-real",
        command=("python", "train.py"),
        exit_code=0,
        wall_seconds=1234.0,
        artifact_sha256="cd" * 32,
    )
    result = gate_attested_run(_exp(), attestation=tampered, attestations=FakeLog())

    assert not result.passed
    assert "digest" in result.detail


def test_a_mismatched_exit_code_is_caught():
    lying = RunAttestation(
        job_handle="BRE-1-real",
        command=("python", "train.py"),
        exit_code=0,
        wall_seconds=1234.0,
        artifact_sha256="ab" * 32,
    )
    log = FakeLog({"BRE-1-real": {"ticket": "BRE-1", "exit_code": 1, "artifact_sha256": "ab" * 32}})
    result = gate_attested_run(_exp(), attestation=lying, attestations=log)

    assert not result.passed
    assert "exit code" in result.detail


def test_a_handle_borrowed_from_another_ticket_is_caught_when_the_ticket_is_known():
    """The run happened, but not for the work being claimed.

    Only checkable when the caller knows which ticket it is adjudicating. The
    verifier does not, so it passes `ticket=None` and this case currently slips
    through there — recorded in GH#33 rather than papered over. The gate supports
    it so the runner can bind it once the runner submits jobs itself.
    """
    result = gate_attested_run(_exp(), attestation=REAL, attestations=FakeLog(), ticket="BRE-99")
    assert not result.passed
    assert "belongs to ticket" in result.detail

    # and the same evidence is fine for the ticket it really belongs to
    assert gate_attested_run(
        _exp(), attestation=REAL, attestations=FakeLog(), ticket="BRE-1"
    ).passed


def test_the_source_comes_from_the_verifier_not_the_candidate():
    """A candidate carrying its own attestation source could carry one that says
    yes to everything. Same trust boundary as G-09's grouping."""
    invented = RunAttestation(
        job_handle="never-issued",
        command=("x",),
        exit_code=0,
        wall_seconds=1.0,
        artifact_sha256="ab" * 32,
    )
    candidate = _candidate(invented)

    assert GateVerifier().run(candidate).promoted, "no log configured: warns"
    assert not GateVerifier(attestations=FakeLog()).run(candidate).promoted


def test_the_gate_always_appears_in_the_verdict():
    """Non-blocking is not absent. A reader must be able to tell an unchecked run
    from a checked one."""
    bundle = GateVerifier().run(_candidate())
    assert "attested_run" in bundle.to_dict()["gate_names"]


def test_a_real_registry_satisfies_the_protocol(tmp_path):
    """The gate is driven by a fixture, so this is the check that the real thing
    still fits the shape the fixture models."""
    from expfactory.gates_v1 import AttestationSource
    from expfactory.registry import JobRegistry, JobSpec

    class Sub:
        def submit(self, spec: JobSpec) -> str:
            return "h1"

        def poll(self, handle: str):
            from expfactory.registry import JobState

            return JobState.SUBMITTED

        def fetch_artifact(self, handle: str) -> str:
            return "ref"

    registry = JobRegistry(
        tmp_path / "jobs.jsonl", Sub(), per_job_cap_usd=10.0, per_day_cap_usd=100.0
    )
    assert isinstance(registry, AttestationSource)

    assert registry.attested_job("never-submitted") is None
    record = registry.submit(JobSpec(ticket="BRE-1", command=("x",), image="local"), 1.0)
    found = registry.attested_job(record.handle)
    assert found is not None and found["ticket"] == "BRE-1"


def test_an_unknown_handle_names_the_handle():
    """A refusal that does not say which handle failed sends whoever reads it
    back to the log to guess."""
    invented = RunAttestation(
        job_handle="ghost-1234",
        command=("x",),
        exit_code=0,
        wall_seconds=1.0,
        artifact_sha256="ab" * 32,
    )
    result = gate_attested_run(_exp(), attestation=invented, attestations=FakeLog())
    assert "ghost-1234" in result.detail


@pytest.mark.parametrize("field", ["job_handle", "artifact_sha256"])
def test_the_attestation_carries_what_the_substrate_produced(field: str):
    assert getattr(REAL, field)
