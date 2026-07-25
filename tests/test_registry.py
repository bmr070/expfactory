"""
N-08 — JobRegistry and the compute-substrate seam.

The properties under test are the ones nothing else in the system can provide:
a lost job is noticed, cost caps refuse *before* compute is touched, the breaker
stays shut once open, and state survives a restart.

The fake substrate is deliberately dumb — this is testing the registry's
bookkeeping and refusals, not Modal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from expfactory.registry import (
    BreakerTripped,
    ComputeSubstrate,
    CostCapExceeded,
    JobRegistry,
    JobSpec,
    JobState,
)


class FakeSubstrate:
    """Records what was submitted; lets a test drive poll results."""

    def __init__(self) -> None:
        self.submitted: list[JobSpec] = []
        self.status: dict[str, JobState] = {}
        self.artifacts: dict[str, str] = {}

    def submit(self, spec: JobSpec) -> str:
        handle = f"job-{len(self.submitted)}"
        self.submitted.append(spec)
        self.status[handle] = JobState.SUBMITTED
        self.artifacts[handle] = f"s3://artifacts/{handle}"
        return handle

    def poll(self, handle: str) -> JobState:
        return self.status[handle]

    def fetch_artifact(self, handle: str) -> str:
        return self.artifacts[handle]


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _spec(ticket: str = "BRE-1") -> JobSpec:
    return JobSpec(ticket=ticket, command=("python", "train.py"), image="cuda:12", gpu="A100")


def _registry(tmp_path: Path, sub: FakeSubstrate, clock: _Clock, **over: float) -> JobRegistry:
    kwargs: dict[str, float] = dict(per_job_cap_usd=50.0, per_day_cap_usd=100.0)
    kwargs.update(over)
    return JobRegistry(tmp_path / "jobs.jsonl", sub, clock=clock, **kwargs)  # type: ignore[arg-type]


def test_the_substrate_protocol_is_satisfied_structurally():
    assert isinstance(FakeSubstrate(), ComputeSubstrate)


# ---- the property nothing else can provide ---------------------------------


def test_lost_job_is_detected_and_opens_the_breaker(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec(), cost_estimate_usd=10.0, deadline_s=3600)

    clock.advance(3601)
    lost = reg.sweep()

    assert [r.handle for r in lost] == ["job-0"]
    assert reg.records()["job-0"].state is JobState.LOST
    assert reg.breaker_reason() is not None


def test_a_lost_job_is_never_auto_retried(tmp_path: Path):
    """Resubmitting a job whose state is unknown can double-spend GPU budget, so
    the breaker blocks the next submission outright rather than retrying."""
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec(), cost_estimate_usd=10.0, deadline_s=60)
    clock.advance(61)
    reg.sweep()

    before = len(sub.submitted)
    with pytest.raises(BreakerTripped):
        reg.submit(_spec(), cost_estimate_usd=1.0)
    assert len(sub.submitted) == before, "a refused submission must not reach the substrate"


def test_a_job_that_finished_in_time_is_collected_not_lost(tmp_path: Path):
    """Past the deadline but actually finished: that is an uncollected result,
    not a loss, and must not trip the breaker."""
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec(), cost_estimate_usd=10.0, deadline_s=60)
    sub.status["job-0"] = JobState.RESOLVED

    clock.advance(61)
    assert reg.sweep() == []
    assert reg.records()["job-0"].state is JobState.RESOLVED
    assert reg.breaker_reason() is None


# ---- fail-closed cost caps --------------------------------------------------


def test_per_job_cap_refuses_before_touching_the_substrate(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock, per_job_cap_usd=25.0)
    with pytest.raises(CostCapExceeded, match="per-job cap"):
        reg.submit(_spec(), cost_estimate_usd=26.0)
    assert sub.submitted == [], "cost check must precede submission, not follow it"


def test_daily_cap_counts_prior_submissions(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock, per_job_cap_usd=100.0, per_day_cap_usd=30.0)
    reg.submit(_spec(), cost_estimate_usd=20.0)
    with pytest.raises(CostCapExceeded, match="daily cap"):
        reg.submit(_spec(), cost_estimate_usd=15.0)


def test_daily_spend_counts_lost_jobs_too(tmp_path: Path):
    """A job that vanished still burned compute. Excluding losses would let
    repeated failures spend without limit — the one direction this must not be
    wrong in."""
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock, per_day_cap_usd=100.0)
    reg.submit(_spec(), cost_estimate_usd=40.0, deadline_s=60)
    clock.advance(61)
    reg.sweep()
    assert reg.spend_today_usd() == 40.0


def test_spend_falls_out_of_the_window_after_a_day(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec(), cost_estimate_usd=40.0)
    assert reg.spend_today_usd() == 40.0
    clock.advance(86_401)
    assert reg.spend_today_usd() == 0.0


def test_an_unreadable_log_refuses_rather_than_assuming_zero_spend(tmp_path: Path):
    """Treating a corrupt log as 'no spend so far' hands an unbounded budget to
    whatever corrupted it. Same fail-safe stance as HoldoutBudget."""
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    (tmp_path / "jobs.jsonl").write_text("{not json\n")
    with pytest.raises(BreakerTripped):
        reg.submit(_spec(), cost_estimate_usd=1.0)


# ---- the breaker ------------------------------------------------------------


def test_breaker_stays_open_until_a_human_resets_it(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.trip_breaker("manual test")
    with pytest.raises(BreakerTripped):
        reg.submit(_spec(), cost_estimate_usd=1.0)

    reg.reset_breaker(operator="bmr070")
    assert reg.breaker_reason() is None
    reg.submit(_spec(), cost_estimate_usd=1.0)  # no raise


def test_reset_survives_and_does_not_resurrect_older_trips(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.trip_breaker("first")
    reg.reset_breaker(operator="bmr070")
    reg.trip_breaker("second")
    assert reg.breaker_reason() == "second"


# ---- durability -------------------------------------------------------------


def test_outstanding_jobs_survive_a_restart(tmp_path: Path):
    """The submitting process is expected to be gone. A fresh registry over the
    same path must see the same outstanding set."""
    sub, clock = FakeSubstrate(), _Clock()
    _registry(tmp_path, sub, clock).submit(_spec("BRE-9"), cost_estimate_usd=5.0)

    reopened = _registry(tmp_path, sub, clock)
    assert [r.ticket for r in reopened.outstanding()] == ["BRE-9"]


def test_resolution_closes_the_record_and_returns_a_reference(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec(), cost_estimate_usd=5.0)
    ref = reg.resolve("job-0")

    assert ref == "s3://artifacts/job-0"
    assert reg.outstanding() == []
    assert reg.records()["job-0"].artifact_ref == ref


def test_registry_records_no_verdict_field(tmp_path: Path):
    """The guard against becoming a second source of truth. This registry says
    what is outstanding; the ledger says what happened. If a 'promoted' or
    'result' field ever appears here, M2-03's Metaflow objection has arrived by
    the back door."""
    from dataclasses import fields

    from expfactory.registry import JobRecord

    names = {f.name for f in fields(JobRecord)}
    assert not names & {"promoted", "result", "verdict", "metric", "passed"}


def test_reconcile_reports_finished_jobs_still_listed_open(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec(), cost_estimate_usd=5.0)
    sub.status["job-0"] = JobState.RESOLVED
    assert reg.reconcile() == ["job-0"]
