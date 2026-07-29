"""
N-08 — JobRegistry and the compute-substrate seam.
BRE-29 — the caps are checked on numbers that can actually be compared.
BRE-30 — the intent is durable before the side effect, and there is one writer.

The properties under test are the ones nothing else in the system can provide:
a lost job is noticed, cost caps refuse *before* compute is touched, the breaker
stays shut once open, and state survives a restart.

The fake substrate is deliberately dumb — this is testing the registry's
bookkeeping and refusals, not Modal.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from expfactory.registry import (
    SECONDS_PER_HOUR,
    BreakerTripped,
    ComputeSubstrate,
    CostCapExceeded,
    InvalidJobInput,
    JobRegistry,
    JobSpec,
    JobState,
    RateCard,
)

HOUR = SECONDS_PER_HOUR


class FakeRateCard:
    """A price the test controls, charged per hour of the job's billable window.

    Per-hour rather than flat so these tests exercise the property that matters:
    the registry prices the *deadline*, so the number under test moves when the
    deadline moves. `_submit` inverts the arithmetic, which keeps every dollar
    figure below as readable as it was when the caller passed one in.
    """

    def __init__(self, usd_per_hour: float = 1.0) -> None:
        self.usd_per_hour = usd_per_hour

    def price_usd(self, spec: JobSpec, billable_seconds: float) -> float:
        return self.usd_per_hour * billable_seconds / HOUR


class BrokenRateCard:
    """Quotes a number nothing can compare. Stands in for a rate-card bug, or a
    substrate reading a price out of a corrupt config."""

    def __init__(self, quote: float) -> None:
        self.quote = quote

    def price_usd(self, spec: JobSpec, billable_seconds: float) -> float:
        return self.quote


class FakeSubstrate:
    """Records what was submitted; lets a test drive poll results."""

    def __init__(self) -> None:
        self.submitted: list[JobSpec] = []
        self.status: dict[str, JobState] = {}
        self.artifacts: dict[str, str] = {}
        self.rate: RateCard = FakeRateCard()

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

    def rate_card(self) -> RateCard:
        return self.rate


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


def _submit(
    reg: JobRegistry,
    sub: FakeSubstrate,
    usd: float,
    *,
    ticket: str = "BRE-1",
    deadline_s: float = HOUR,
):
    """Submit a job the substrate prices at exactly `usd`.

    The registry no longer accepts a cost, so a test that wants a $26 job has to
    make the *substrate* quote $26. That is the whole point of BRE-29, and this
    helper is the only place the tests below are allowed to reach across it.
    """
    sub.rate = FakeRateCard(usd * HOUR / deadline_s)
    return reg.submit(_spec(ticket), deadline_s=deadline_s)


def test_the_substrate_protocol_is_satisfied_structurally():
    assert isinstance(FakeSubstrate(), ComputeSubstrate)


def test_a_substrate_without_a_rate_card_is_not_a_substrate():
    """The price authority is part of the seam, not an optional extra. A
    substrate that cannot quote its own compute would leave the caps checking a
    number somebody else invented, which is the defect BRE-29 closed."""

    class NoPrices:
        def submit(self, spec: JobSpec) -> str:
            return "h"

        def poll(self, handle: str) -> JobState:
            return JobState.SUBMITTED

        def fetch_artifact(self, handle: str) -> str:
            return "ref"

    assert not isinstance(NoPrices(), ComputeSubstrate)


# ---- the property nothing else can provide ---------------------------------


def test_lost_job_is_detected_and_opens_the_breaker(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    _submit(reg, sub, 10.0, deadline_s=3600)

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
    _submit(reg, sub, 10.0, deadline_s=60)
    clock.advance(61)
    reg.sweep()

    before = len(sub.submitted)
    with pytest.raises(BreakerTripped):
        reg.submit(_spec())
    assert len(sub.submitted) == before, "a refused submission must not reach the substrate"


def test_a_job_that_finished_in_time_is_collected_not_lost(tmp_path: Path):
    """Past the deadline but actually finished: that is an uncollected result,
    not a loss, and must not trip the breaker."""
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    _submit(reg, sub, 10.0, deadline_s=60)
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
        _submit(reg, sub, 26.0)
    assert sub.submitted == [], "cost check must precede submission, not follow it"


def test_daily_cap_counts_prior_submissions(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock, per_job_cap_usd=100.0, per_day_cap_usd=30.0)
    _submit(reg, sub, 20.0)
    with pytest.raises(CostCapExceeded, match="daily cap"):
        _submit(reg, sub, 15.0)


def test_a_longer_deadline_costs_more(tmp_path: Path):
    """The deadline is what gets priced, so it is a real constraint rather than a
    declared intention.

    This is why the cost argument could be removed without putting a different
    free number in its place: the caller still chooses, but what they choose is
    the window `sweep` will hold them to. Buying a cheaper job means accepting an
    earlier deadline.
    """
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock, per_day_cap_usd=1_000.0)
    sub.rate = FakeRateCard(10.0)

    short = reg.submit(_spec("BRE-short"), deadline_s=HOUR)
    long = reg.submit(_spec("BRE-long"), deadline_s=4 * HOUR)

    assert short.cost_estimate_usd == 10.0
    assert long.cost_estimate_usd == 40.0
    assert long.deadline_at - long.submitted_at == 4 * HOUR, "priced window is enforced window"


def test_daily_spend_counts_lost_jobs_too(tmp_path: Path):
    """A job that vanished still burned compute. Excluding losses would let
    repeated failures spend without limit — the one direction this must not be
    wrong in."""
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock, per_day_cap_usd=100.0)
    _submit(reg, sub, 40.0, deadline_s=60)
    clock.advance(61)
    reg.sweep()
    assert reg.spend_today_usd() == 40.0


def test_spend_falls_out_of_the_window_after_a_day(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    _submit(reg, sub, 40.0)
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
        reg.submit(_spec())


# ---- the breaker ------------------------------------------------------------


def test_breaker_stays_open_until_a_human_resets_it(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.trip_breaker("manual test")
    with pytest.raises(BreakerTripped):
        reg.submit(_spec())

    reg.reset_breaker(operator="bmr070")
    assert reg.breaker_reason() is None
    reg.submit(_spec())  # no raise


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
    _submit(_registry(tmp_path, sub, clock), sub, 5.0, ticket="BRE-9")

    reopened = _registry(tmp_path, sub, clock)
    assert [r.ticket for r in reopened.outstanding()] == ["BRE-9"]


def test_resolution_closes_the_record_and_returns_a_reference(tmp_path: Path):
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    _submit(reg, sub, 5.0)
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
    _submit(reg, sub, 5.0)
    sub.status["job-0"] = JobState.RESOLVED

    found = reg.reconcile()
    assert found.finished == ("job-0",)
    assert found.orphaned == (), "nothing crashed, so nothing is unaccounted for"


def test_a_corrupt_row_does_not_brick_the_registry(tmp_path: Path):
    """Refusing has to stay recoverable.

    An earlier version raised while *parsing*, so one malformed line jammed the
    registry permanently: `reset_breaker` appended fine and then
    `breaker_reason` raised again reading it back. A breaker with no path to
    reset is not a breaker, it is a brick.

    Good rows still parse, the damage is reported, and repairing the log clears
    the refusal without anyone having to edit history.
    """
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    _submit(reg, sub, 7.0)

    path = tmp_path / "jobs.jsonl"
    good = path.read_text()
    path.write_text(good + "{corrupt\n")

    assert reg.log_damage() == 1
    assert [r.ticket for r in reg.outstanding()] == ["BRE-1"], "good rows still parse"
    with pytest.raises(BreakerTripped, match="unreadable row"):
        reg.submit(_spec())

    path.write_text(good)  # repair
    assert reg.log_damage() == 0
    reg.submit(_spec())  # recovered, no raise


# ---- BRE-29: numbers that cannot be compared are refused, not compared -------
#
# The defect these fixtures pin down was reproduced at HEAD twice over. Every
# check in `submit` was written `estimate > cap`, and that one shape lets two
# different values straight through:
#
#   NaN    compares False against everything, so it is under every cap
#   -100   genuinely under every cap, and it then *lowered* `spend_today_usd`,
#          manufacturing budget for the next job that should have been refused
#
# Neither is a rounding error and neither can be clamped away. A cost that
# cannot be read means spend is unknown.

BAD_NUMBERS = [
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="plus-inf"),
    pytest.param(float("-inf"), id="minus-inf"),
    pytest.param(-100.0, id="negative"),
    pytest.param(-0.01, id="barely-negative"),
]

BAD_DEADLINES = [
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="plus-inf"),
    pytest.param(float("-inf"), id="minus-inf"),
    pytest.param(0.0, id="zero"),
    pytest.param(-60.0, id="negative"),
]


def _submitted_row(handle: str, at: float, cost: float) -> str:
    """A log line in exactly the shape `submit` writes.

    Hand-written because the API can no longer produce a poisoned one — which is
    the point. `json.dumps` emits bare `NaN`/`Infinity` literals and `json.loads`
    reads them straight back, so these really are rows the parser accepts.
    """
    return json.dumps(
        {
            "event": "submitted",
            "at": at,
            "handle": handle,
            "ticket": "BRE-1",
            "deadline_at": at + HOUR,
            "cost_estimate_usd": cost,
        },
        sort_keys=True,
    )


@pytest.mark.parametrize("quote", BAD_NUMBERS)
def test_an_unusable_quote_is_refused_before_the_substrate_is_touched(tmp_path: Path, quote: float):
    """The caps never see the number at all.

    The substrate is trusted with the GPU credential; it is not trusted with
    arithmetic. A rate card reading a price out of a corrupt config is a bug
    that would otherwise disable every cap silently.
    """
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    sub.rate = BrokenRateCard(quote)

    with pytest.raises(InvalidJobInput):
        reg.submit(_spec())

    assert sub.submitted == [], "a refused submission must not reach the substrate"
    assert reg.records() == {}, "and must not be recorded"


@pytest.mark.parametrize("quote", BAD_NUMBERS)
def test_an_unusable_quote_never_reaches_the_log(tmp_path: Path, quote: float):
    """Not merely refused this time — unable to be written at all.

    `records()` is a faithful replay, so a bad value that got as far as `_append`
    would come back on every later read and poison every later cap check, in a
    file whose whole design is that history is not editable.
    """
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    sub.rate = BrokenRateCard(quote)

    with pytest.raises(InvalidJobInput):
        reg.submit(_spec())

    assert (tmp_path / "jobs.jsonl").read_text() == ""


@pytest.mark.parametrize("bad", BAD_NUMBERS)
@pytest.mark.parametrize("cap", ["per_job_cap_usd", "per_day_cap_usd"])
def test_an_unusable_cap_refuses_at_construction(tmp_path: Path, cap: str, bad: float):
    """A NaN cap passes every `>` comparison in `submit`, so a registry built
    with one enforces nothing while reading as configured.

    Refused before the log file is created: an empty `jobs.jsonl` left behind by
    a failed construction is indistinguishable from a working registry with no
    history yet.
    """
    sub, clock = FakeSubstrate(), _Clock()
    with pytest.raises(InvalidJobInput, match=cap):
        _registry(tmp_path, sub, clock, **{cap: bad})
    assert not (tmp_path / "jobs.jsonl").exists()


@pytest.mark.parametrize("bad", BAD_DEADLINES)
def test_an_unusable_deadline_is_refused(tmp_path: Path, bad: float):
    """A deadline is now two things at once — the window `sweep` enforces and the
    window the substrate prices — so a bad one costs twice.

    Zero and negative are refused, not only the non-finite ones: a job whose
    deadline has already passed is lost on the next sweep, which opens the
    breaker and needs a human, and it prices its own billable window at nothing,
    which is the zero-cost failure C-01 exists to prevent.
    """
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)

    with pytest.raises(InvalidJobInput, match="deadline_s"):
        reg.submit(_spec(), deadline_s=bad)
    assert sub.submitted == []

    with pytest.raises(InvalidJobInput, match="default_deadline_s"):
        JobRegistry(
            tmp_path / "other.jsonl",
            sub,
            per_job_cap_usd=1.0,
            per_day_cap_usd=1.0,
            default_deadline_s=bad,
        )


def test_a_negative_estimate_can_no_longer_corrupt_the_trailing_day_total(tmp_path: Path):
    """The arithmetic the defect actually exploited, shut at both ends.

    At HEAD: submit a -$100 job, watch `spend_today_usd` drop to -$40, and the
    next $130 job is admitted against a $100 daily cap because -40 + 130 < 100.
    Real compute, bought with a number that was never spent.

    The submitting side can no longer name a price at all, and the reading side
    refuses a recorded one it cannot use. So the only remaining way to get a
    negative row into the log is to edit the log by hand, which is what happens
    here — and even that yields a refusal instead of free budget.
    """
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock, per_job_cap_usd=200.0, per_day_cap_usd=100.0)
    _submit(reg, sub, 60.0)
    assert reg.spend_today_usd() == 60.0

    path = tmp_path / "jobs.jsonl"
    honest = path.read_text()
    path.write_text(honest + _submitted_row("job-tampered", clock.t, -100.0) + "\n")

    # The old sum() returned -40.0 here, and then admitted the next job.
    with pytest.raises(BreakerTripped, match="spend is therefore unknown"):
        reg.spend_today_usd()
    with pytest.raises(BreakerTripped):
        _submit(reg, sub, 130.0)
    assert len(sub.submitted) == 1, "nothing new reached the substrate"

    path.write_text(honest)  # repair; refusing stays recoverable
    assert reg.spend_today_usd() == 60.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_recorded_cost_makes_todays_spend_unknown(tmp_path: Path, bad: float):
    """One poisoned row used to disable the daily cap for the whole trailing day
    rather than miscount one job: `sum()` over a NaN is NaN, and `NaN > cap` is
    False, so every subsequent submission passed."""
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    _submit(reg, sub, 5.0)

    path = tmp_path / "jobs.jsonl"
    path.write_text(path.read_text() + _submitted_row("job-poisoned", clock.t, bad) + "\n")

    assert reg.log_damage() == 0, "the row parses; this is not the corrupt-line path"
    with pytest.raises(BreakerTripped, match="recorded cost"):
        reg.spend_today_usd()


def test_a_non_finite_submission_time_makes_todays_spend_unknown(tmp_path: Path):
    """A NaN timestamp compares False against the cutoff either way round, so the
    job drops silently out of the trailing window instead of being counted. Same
    class of defect as the NaN cost, and the same answer: unknown, not zero."""
    sub, clock = FakeSubstrate(), _Clock()
    reg = _registry(tmp_path, sub, clock)
    _submit(reg, sub, 5.0)

    path = tmp_path / "jobs.jsonl"
    path.write_text(path.read_text() + _submitted_row("job-untimed", float("nan"), 5.0) + "\n")

    assert math.isnan(reg.records()["job-untimed"].submitted_at), "the row really did parse"
    with pytest.raises(BreakerTripped, match="submitted_at"):
        reg.spend_today_usd()


def test_the_caller_cannot_name_a_price_at_all(tmp_path: Path):
    """The structural half of the fix, asserted structurally.

    Validating an estimate makes a bad number *unlikely*; removing the parameter
    makes a caller-chosen number impossible. If `cost_estimate_usd` ever returns
    as an argument here, every fixture above collapses into input hygiene on a
    value the untrusted side still picks — which W-12 already recorded is not a
    cap.
    """
    import inspect

    params = inspect.signature(JobRegistry.submit).parameters
    assert "cost_estimate_usd" not in params
    assert set(params) == {"self", "spec", "deadline_s"}

    reg = _registry(tmp_path, FakeSubstrate(), _Clock())
    with pytest.raises(TypeError):
        reg.submit(_spec(), cost_estimate_usd=1.0)  # type: ignore[call-arg]
