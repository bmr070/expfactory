"""BRE-30 — the intent is durable before the side effect, and there is one writer.

The property under test is the one `JobRegistry` could not previously provide:
**a job that was started has left a row**, even when the process died before
anything could record what came back.

Before this, `submit` called the substrate and appended afterwards. A death in
between left a live, billable job with no row at all — invisible to `reconcile`,
which reads rows, and therefore invisible to the one component M2-03 made
responsible for noticing. The case box 10 exists to catch was the case it could
not see.

Everything here drives a real process death through a real file and then asks a
*fresh* registry what it can see. A mock would assert an assumption about the
write ordering rather than the ordering itself.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from expfactory.local_substrate import SubstrateRefused
from expfactory.registry import (
    SECONDS_PER_HOUR,
    BreakerTripped,
    CompletionRecord,
    InvalidJobInput,
    JobRegistry,
    JobSpec,
    JobState,
    RateCard,
    RegistryRefused,
    ReservationConflict,
    ReservationState,
    SingleWriterTimeout,
    SubstrateDeclined,
    SubstrateUncertain,
)

HOUR = SECONDS_PER_HOUR


class ProcessDied(BaseException):
    """Caught by nothing, on purpose.

    Stands in for a `SIGKILL`, a pulled plug, or the OOM killer landing between
    the substrate starting a job and the registry recording the handle. Deriving
    from `BaseException` means no `except Exception` anywhere in the path can
    quietly turn it into a handled error, which is exactly the fidelity this
    test needs.
    """


class FakeRateCard:
    def __init__(self, usd_per_hour: float = 1.0) -> None:
        self.usd_per_hour = usd_per_hour

    def price_usd(self, spec: JobSpec, billable_seconds: float) -> float:
        return self.usd_per_hour * billable_seconds / HOUR


class FakeSubstrate:
    def __init__(self) -> None:
        self.submitted: list[JobSpec] = []
        self.status: dict[str, JobState] = {}
        self.artifacts: dict[str, str] = {}
        self.completions: dict[str, CompletionRecord] = {}
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

    def completion(self, handle: str) -> CompletionRecord | None:
        """What this fake says it ran. Tests drive it by assigning `completions`."""
        return self.completions.get(handle)


class DyingSubstrate(FakeSubstrate):
    """Starts the job, then the process dies before the handle comes back.

    The exact shape of the defect: the substrate is billing, and the caller
    never gets to write down what it returned.
    """

    def submit(self, spec: JobSpec) -> str:
        super().submit(spec)  # the job really did start; the bill is real
        raise ProcessDied("SIGKILL between submit and record")


class DecliningSubstrate(FakeSubstrate):
    """States plainly that it started nothing. The one case that is not a crash."""

    def submit(self, spec: JobSpec) -> str:
        raise SubstrateDeclined("quota exhausted, nothing was queued")


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _spec(ticket: str = "BRE-1", key: str | None = None) -> JobSpec:
    return JobSpec(
        ticket=ticket,
        command=("python", "train.py"),
        image="cuda:12",
        gpu="A100",
        idempotency_key=key,
    )


def _registry(tmp_path: Path, sub: FakeSubstrate, clock: _Clock, **over: float) -> JobRegistry:
    kwargs: dict[str, float] = dict(per_job_cap_usd=50.0, per_day_cap_usd=100.0)
    kwargs.update(over)
    return JobRegistry(tmp_path / "jobs.jsonl", sub, clock=clock, **kwargs)  # type: ignore[arg-type]


def _priced(sub: FakeSubstrate, usd: float) -> FakeSubstrate:
    """The registry no longer accepts a cost, so a test that wants a $25 job has
    to make the *substrate* quote $25. That is BRE-29, and this is the only
    place these tests reach across it."""
    sub.rate = FakeRateCard(usd)
    return sub


# ---------------------------------------------------------------------------
# The crash window
# ---------------------------------------------------------------------------


def test_a_death_between_submit_and_record_leaves_a_row(tmp_path: Path):
    """The whole ticket, in one assertion."""
    sub, clock = _priced(DyingSubstrate(), 10.0), _Clock()
    reg = _registry(tmp_path, sub, clock)

    with pytest.raises(ProcessDied):
        reg.submit(_spec(), deadline_s=HOUR)

    assert sub.submitted, "precondition: the substrate really did start a job"
    reservations = list(reg.reservations().values())
    assert len(reservations) == 1
    assert reservations[0].handle is None
    assert reservations[0].is_orphaned


def test_a_fresh_registry_finds_the_orphan(tmp_path: Path):
    """Durable, not in memory. A new object over the same path sees it."""
    sub, clock = _priced(DyingSubstrate(), 10.0), _Clock()
    with pytest.raises(ProcessDied):
        _registry(tmp_path, sub, clock).submit(_spec(), deadline_s=HOUR)

    # A different process, in effect: nothing carries over but the file.
    restarted = _registry(tmp_path, FakeSubstrate(), _Clock())
    found = restarted.reconcile().orphaned
    assert len(found) == 1
    assert found[0].ticket == "BRE-1"


def test_an_orphan_opens_the_breaker_and_is_never_retried(tmp_path: Path):
    """Escalate, never resubmit.

    The job may still be running, so a retry buys the same result twice. W-12
    forbids auto-retry on cost; a reservation whose outcome is unknown is the
    same argument with less information.
    """
    sub, clock = _priced(DyingSubstrate(), 10.0), _Clock()
    with pytest.raises(ProcessDied):
        _registry(tmp_path, sub, clock).submit(_spec(), deadline_s=HOUR)

    live = _registry(tmp_path, FakeSubstrate(), clock)
    live.reconcile()

    assert live.breaker_reason() is not None
    with pytest.raises(BreakerTripped):
        live.submit(_spec("BRE-2"), deadline_s=HOUR)
    assert len(sub.submitted) == 1, "the orphan was resubmitted"


def test_submit_escalates_orphans_even_if_nobody_reconciles(tmp_path: Path):
    """The guarantee must not depend on remembering to call `reconcile`."""
    sub, clock = _priced(DyingSubstrate(), 10.0), _Clock()
    with pytest.raises(ProcessDied):
        _registry(tmp_path, sub, clock).submit(_spec(), deadline_s=HOUR)

    fresh = _registry(tmp_path, _priced(FakeSubstrate(), 1.0), clock)  # no reconcile()
    with pytest.raises(BreakerTripped):
        fresh.submit(_spec("BRE-2"), deadline_s=HOUR)


def test_an_orphan_counts_against_spend(tmp_path: Path):
    """Unknown is not zero.

    The money may be gone. Treating an unbound reservation as free is how a
    crash becomes extra budget.
    """
    sub, clock = _priced(DyingSubstrate(), 25.0), _Clock()
    with pytest.raises(ProcessDied):
        _registry(tmp_path, sub, clock).submit(_spec(), deadline_s=HOUR)

    assert _registry(tmp_path, FakeSubstrate(), clock).spend_today_usd() == pytest.approx(25.0)


def test_a_declined_submission_releases_the_money(tmp_path: Path):
    """The one case that is not a crash window.

    `SubstrateDeclined` is the substrate *stating* it started nothing, which
    differs in kind from a silence we have to assume the worst about.
    """
    sub, clock = _priced(DecliningSubstrate(), 25.0), _Clock()
    with pytest.raises(SubstrateDeclined):
        _registry(tmp_path, sub, clock).submit(_spec(), deadline_s=HOUR)

    restarted = _registry(tmp_path, FakeSubstrate(), clock)
    assert restarted.spend_today_usd() == pytest.approx(0.0)
    assert restarted.reconcile().orphaned == ()
    assert restarted.breaker_reason() is None


def test_abandoning_an_orphan_needs_a_name_and_still_charges(tmp_path: Path):
    """The only exit, and it does not refund.

    A state with no exit is a jam rather than a state: every reset would
    re-escalate the same key. But "I looked and I think nothing ran" is a
    person's claim, not the substrate's, so the money stays counted.
    """
    sub, clock = _priced(DyingSubstrate(), 25.0), _Clock()
    with pytest.raises(ProcessDied):
        _registry(tmp_path, sub, clock).submit(_spec(), deadline_s=HOUR)

    reg = _registry(tmp_path, FakeSubstrate(), clock)
    key = reg.reconcile().orphaned[0].key
    reg.abandon_reservation(key, operator="bmr070", reason="checked the console, nothing running")

    # **This test used to be vacuous, and a review proved it (BRE-41).**
    #
    # It asserted `reconcile().orphaned == ()` and `spend == 25.0`. Deleting the
    # `abandon_reservation` call above left both true and the test passing:
    # `orphaned` is empty after ANY first escalation, and an orphan is charged
    # whether abandoned or not. Two assertions, neither of which could fail.
    #
    # That is the `assert ... or True` family this repo already warns about,
    # written in the session that added the warning's newest entry. The fix is
    # to assert the thing that is only true *because* the call happened.
    assert reg.reservations()[key].state is ReservationState.ABANDONED

    # And the operator's name is the point of requiring one: what is being
    # recorded is a person's claim to have gone and looked at the substrate.
    log = (tmp_path / "jobs.jsonl").read_text(encoding="utf-8")
    abandoned = [
        json.loads(line)
        for line in log.splitlines()
        if line.strip() and json.loads(line).get("event") == "abandoned"
    ]
    assert len(abandoned) == 1
    assert abandoned[0]["operator"] == "bmr070"
    assert "console" in abandoned[0]["reason"]

    assert reg.spend_today_usd() == pytest.approx(25.0), "abandoning is not a refund"


def test_abandoning_an_unknown_key_is_refused(tmp_path: Path):
    reg = _registry(tmp_path, FakeSubstrate(), _Clock())
    with pytest.raises(ReservationConflict):
        reg.abandon_reservation("never-existed", operator="bmr070", reason="typo")


# ---------------------------------------------------------------------------
# Idempotency — one intent must not become two bills
# ---------------------------------------------------------------------------


def test_the_same_key_twice_does_not_submit_twice(tmp_path: Path):
    sub, clock = _priced(FakeSubstrate(), 10.0), _Clock()
    reg = _registry(tmp_path, sub, clock)

    first = reg.submit(_spec("BRE-1", key="intent-1"), deadline_s=HOUR)
    second = reg.submit(_spec("BRE-1", key="intent-1"), deadline_s=HOUR)

    assert first.handle == second.handle
    assert len(sub.submitted) == 1, "one intent became two jobs"


def test_the_same_key_twice_is_charged_once(tmp_path: Path):
    sub, clock = _priced(FakeSubstrate(), 30.0), _Clock()
    reg = _registry(tmp_path, sub, clock)

    reg.submit(_spec("BRE-1", key="intent-1"), deadline_s=HOUR)
    reg.submit(_spec("BRE-1", key="intent-1"), deadline_s=HOUR)

    assert reg.spend_today_usd() == pytest.approx(30.0)


def test_replaying_an_unbound_key_is_refused_not_retried(tmp_path: Path):
    """The dangerous replay.

    The first attempt may be running right now, so "sure, try again" is exactly
    how one intent becomes two bills. Refuse and make a human decide.
    """
    sub, clock = _priced(DyingSubstrate(), 10.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    with pytest.raises(ProcessDied):
        reg.submit(_spec("BRE-1", key="intent-1"), deadline_s=HOUR)

    fresh = _registry(tmp_path, FakeSubstrate(), clock)
    with pytest.raises((ReservationConflict, BreakerTripped)):
        fresh.submit(_spec("BRE-1", key="intent-1"), deadline_s=HOUR)
    assert len(sub.submitted) == 1


def test_the_key_reaches_the_substrate(tmp_path: Path):
    """So a substrate able to deduplicate can, even when this registry is wrong
    about whether the first attempt landed."""
    sub, clock = _priced(FakeSubstrate(), 1.0), _Clock()
    _registry(tmp_path, sub, clock).submit(_spec("BRE-1"), deadline_s=HOUR)
    assert sub.submitted[0].idempotency_key, "a key was minted but never passed on"


# ---------------------------------------------------------------------------
# One writer at a time
# ---------------------------------------------------------------------------


def test_a_second_writer_is_refused_rather_than_admitted(tmp_path: Path):
    """Two runners must not both read a spend total that excludes the other's job.

    Driven through the real lock on a real file. `lock_timeout_s=0` makes it one
    attempt then refuse, so this is deterministic rather than a race.
    """
    sub, clock = _priced(FakeSubstrate(), 1.0), _Clock()
    holder = _registry(tmp_path, sub, clock)
    contender = _registry(tmp_path, sub, clock, lock_timeout_s=0.0)

    with holder._admission_lock(), pytest.raises(SingleWriterTimeout):
        contender.submit(_spec("BRE-2"), deadline_s=HOUR)

    assert sub.submitted == [], "a refused submission must not have started anything"


def test_the_lock_is_released_so_the_next_writer_proceeds(tmp_path: Path):
    """A refusal that jammed the lock would stop `sweep`, and `sweep` is the only
    thing in this system that notices a lost job."""
    sub, clock = _priced(FakeSubstrate(), 1.0), _Clock()
    reg = _registry(tmp_path, sub, clock, lock_timeout_s=0.0)

    reg.submit(_spec("BRE-1"), deadline_s=HOUR)
    reg.submit(_spec("BRE-2"), deadline_s=HOUR)
    assert len(sub.submitted) == 2


def test_two_threads_cannot_both_admit_against_one_cap(tmp_path: Path):
    """The failure the lock exists for, driven concurrently.

    A $60 daily cap and two $40 jobs: exactly one may land. Without the lock both
    read a $0 total and both admit.
    """
    sub, clock = _priced(FakeSubstrate(), 40.0), _Clock()
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt(ticket: str) -> None:
        reg = _registry(tmp_path, sub, clock, per_day_cap_usd=60.0)
        barrier.wait()
        try:
            reg.submit(_spec(ticket), deadline_s=HOUR)
            outcomes.append("admitted")
        except RegistryRefused:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(t,)) for t in ("BRE-1", "BRE-2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("admitted") == 1, f"the cap admitted {outcomes.count('admitted')} jobs"
    assert len(sub.submitted) == 1


# ---------------------------------------------------------------------------
# BRE-29 must not have regressed
# ---------------------------------------------------------------------------


def test_sweep_still_notices_a_lost_job(tmp_path: Path):
    """The reservation protocol must not have cost the older guarantee."""
    sub, clock = _priced(FakeSubstrate(), 10.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec("BRE-1"), deadline_s=HOUR)

    clock.advance(2 * HOUR)
    lost = reg.sweep()

    assert [r.ticket for r in lost] == ["BRE-1"]
    assert reg.breaker_reason() is not None


# --------------------------------------------------------------------------- #
# BRE-41 — two meanings sharing one class, and a damaged log answering 0.0
# --------------------------------------------------------------------------- #


def test_uncertain_is_not_declined() -> None:
    """The type distinction is the whole fix.

    `SubstrateRefused` is a `SubstrateDeclined` — the registry's name for
    "nothing was started" — and the unreadable-marker path raised it while its
    own message said "may already own one". The registry believed the type.

    If `SubstrateUncertain` ever becomes a `SubstrateDeclined`, the live-job-as-
    released bug returns silently, so this pins the hierarchy rather than any
    behaviour.
    """
    assert not issubclass(SubstrateUncertain, SubstrateDeclined)
    assert issubclass(SubstrateRefused, SubstrateDeclined), (
        "a preflight refusal really did start nothing; that one must stay declined"
    )


class _UncertainSubstrate(FakeSubstrate):
    """Cannot tell whether the job started. The unclean-shutdown case."""

    def submit(self, spec: JobSpec) -> str:
        raise SubstrateUncertain("marker unreadable; this key may already own a run")


def test_an_uncertain_submission_stays_open_for_a_human(tmp_path: Path) -> None:
    """Reproduced before the fix as a live job recorded at zero cost:

        reservation states: {'intent-1': 'released'}
        spend_today_usd  : 0.0
        breaker_reason   : None

    `released` is the one state that does not count against spend. The
    reservation must instead stay open, so the next escalation orphans it and
    the breaker opens.
    """
    sub, clock = _priced(_UncertainSubstrate(), 25.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    with pytest.raises(SubstrateUncertain):
        reg.submit(_spec("BRE-1", key="intent-1"), deadline_s=HOUR)

    reservation = reg.reservations()["intent-1"]
    assert reservation.is_orphaned, f"uncertain outcome was resolved to {reservation.state!r}"
    assert reg.spend_today_usd() == pytest.approx(25.0), "unknown spend was recorded as zero"

    fresh = _registry(tmp_path, FakeSubstrate(), clock)
    assert len(fresh.reconcile().orphaned) == 1
    assert fresh.breaker_reason() is not None


def test_a_declined_submission_still_releases(tmp_path: Path) -> None:
    """The distinction must stay a distinction. A substrate that states it
    started nothing should not open the breaker, or the everyday case — one
    card, a second job asking for it — trips it every time."""
    sub, clock = _priced(DecliningSubstrate(), 25.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    with pytest.raises(SubstrateDeclined):
        reg.submit(_spec("BRE-1", key="intent-2"), deadline_s=HOUR)

    assert reg.spend_today_usd() == pytest.approx(0.0)
    assert reg.breaker_reason() is None


def test_a_damaged_log_makes_spend_unknown_not_zero(tmp_path: Path) -> None:
    """`_replay` drops a `bound` row whose key is not in `held`, and `held` is
    built only from *parseable* `reserved` rows — so one damaged line silently
    erases the live job bound to it.

    Reproduced: `log_damage() -> 1`, `spend_today_usd() -> 0.0`, `records() ->
    {}`, for a job 95,000s past its deadline. A live job, no spend, and nothing
    for `sweep` to notice.
    """
    sub, clock = _priced(FakeSubstrate(), 25.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec("BRE-1"), deadline_s=HOUR)
    assert reg.spend_today_usd() == pytest.approx(25.0)

    # A truncated line, as an unclean shutdown leaves.
    log = tmp_path / "jobs.jsonl"
    log.write_text(log.read_text(encoding="utf-8") + '{"event": "reserved", "at": 10\n', "utf-8")

    reopened = _registry(tmp_path, FakeSubstrate(), clock)
    assert reopened.log_damage() == 1
    with pytest.raises(BreakerTripped, match="unknown"):
        reopened.spend_today_usd()


# ---------------------------------------------------------------------------
# BRE-41 — the remaining fail-opens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("junk", ["null", "42", "[]", '{"nope": 1}', '"a string"'])
def test_valid_json_that_is_not_an_event_is_damage(tmp_path: Path, junk: str):
    """The quarantine caught `JSONDecodeError` only.

    Every value here parses fine and then raises a `TypeError` or `KeyError` out
    of the replay — which is not a `RegistryRefused`, so callers guarding on that
    miss it, and it escaped the runner's tick entirely. Worse, `log_damage()`
    reported **0** for a log that could not be replayed at all, and its contract
    is "non-zero means spend accounting is incomplete".
    """
    sub, clock = _priced(FakeSubstrate(), 10.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec(), deadline_s=HOUR)

    log = tmp_path / "jobs.jsonl"
    log.write_text(log.read_text(encoding="utf-8") + junk + "\n", encoding="utf-8")

    reopened = _registry(tmp_path, FakeSubstrate(), clock)
    assert reopened.log_damage() == 1, f"{junk!r} was not counted as damage"
    # And the reads no longer explode; they refuse, which is a state callers handle.
    with pytest.raises(BreakerTripped):
        reopened.spend_today_usd()


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_idempotency_key_is_refused(tmp_path: Path, blank: str):
    """`key = spec.idempotency_key or uuid4()` mapped `""` onto "no key".

    Two submissions with `""` produced two GPU jobs, charged twice, and because
    a *different* uuid was minted per call the substrate's own marker dedup could
    not save it either. A caller reading the key out of an unset environment
    variable believes it is protected.
    """
    sub, clock = _priced(FakeSubstrate(), 10.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    with pytest.raises(InvalidJobInput, match="empty or blank"):
        reg.submit(_spec("BRE-1", key=blank), deadline_s=HOUR)
    assert sub.submitted == [], "a refused submission must not have started anything"


def test_a_real_key_with_leading_space_stays_distinct(tmp_path: Path):
    """Refuse, do not sanitize. `" k"` and `"k"` are different keys and
    collapsing them would be the same error in the other direction."""
    sub, clock = _priced(FakeSubstrate(), 10.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec("BRE-1", key=" k"), deadline_s=HOUR)
    reg.submit(_spec("BRE-2", key="k"), deadline_s=HOUR)
    assert len(sub.submitted) == 2


def test_an_uncomparable_deadline_refuses_rather_than_never_expiring(tmp_path: Path):
    """`deadline_at` is the only field `sweep` reads and the only one `_replay`
    never validated.

    `json.loads` accepts bare `Infinity`, so a row carrying it stayed outstanding
    forever: `now < inf` is always true, the breaker never opened, and
    `log_damage()` read clean.
    """
    sub, clock = _priced(FakeSubstrate(), 10.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    reg.submit(_spec(), deadline_s=HOUR)

    log = tmp_path / "jobs.jsonl"
    poisoned = log.read_text(encoding="utf-8").replace(
        f'"deadline_at": {1000.0 + HOUR}', '"deadline_at": Infinity'
    )
    log.write_text(poisoned, encoding="utf-8")

    reopened = _registry(tmp_path, FakeSubstrate(), clock)
    reopened._clock = _Clock(10**12)  # type: ignore[assignment]
    with pytest.raises(BreakerTripped, match="deadline_at"):
        reopened.sweep()


def test_resetting_the_breaker_does_not_forget_an_orphan(tmp_path: Path):
    """`reset_breaker` was the cheap way past the control that forces a look.

    `breaker_reason` walked backwards and returned None at the first
    `breaker_reset`, so a reset with no `abandon_reservation` cleared an
    orphan's entire effect:

        after reset, orphaned_reservations(): []
        after reset, reconcile().orphaned:    ()
        submit after reset succeeded

    A possibly-live job, and the registry accepting work again with no
    `abandoned` row saying anyone checked. `abandon_reservation` needs a name
    AND a reason; `reset_breaker` needs a name and no reason.
    """
    sub, clock = _priced(DyingSubstrate(), 25.0), _Clock()
    with pytest.raises(ProcessDied):
        _registry(tmp_path, sub, clock).submit(_spec(), deadline_s=HOUR)

    reg = _registry(tmp_path, FakeSubstrate(), clock)
    reg.reconcile()
    reg.reset_breaker(operator="bmr070")

    assert reg.breaker_reason() is not None, "a reset forgot an unexplained reservation"
    assert len(reg.unaccounted_reservations()) == 1
    with pytest.raises(BreakerTripped):
        reg.submit(_spec("BRE-2"), deadline_s=HOUR)


def test_abandoning_is_what_actually_closes_it(tmp_path: Path):
    """The exit has to work, or the fix above is a jam rather than a control."""
    sub, clock = _priced(DyingSubstrate(), 25.0), _Clock()
    with pytest.raises(ProcessDied):
        _registry(tmp_path, sub, clock).submit(_spec(), deadline_s=HOUR)

    reg = _registry(tmp_path, _priced(FakeSubstrate(), 1.0), clock)
    key = reg.reconcile().orphaned[0].key
    reg.abandon_reservation(key, operator="bmr070", reason="checked, nothing running")
    reg.reset_breaker(operator="bmr070")

    assert reg.unaccounted_reservations() == []
    assert reg.breaker_reason() is None
    reg.submit(_spec("BRE-2"), deadline_s=HOUR)  # accepts work again


class _OneBadArtifact(FakeSubstrate):
    """Every job finishes; fetching the second one's artifact fails."""

    def fetch_artifact(self, handle: str) -> str:
        if handle == "job-1":
            raise OSError("artifact store unreachable")
        return super().fetch_artifact(handle)


def test_one_unreadable_artifact_does_not_strand_the_others(tmp_path: Path):
    """`resolve` appends the `resolved` row BEFORE returning, so a later failure
    used to discard every already-collected result while its record stayed
    durably closed — and `outstanding()` then excluded it, so `sweep` never saw
    it and this method never returned it again. Its ticket would sit in
    `Running Unattended` with nothing able to move it out.
    """
    sub, clock = _priced(_OneBadArtifact(), 1.0), _Clock()
    reg = _registry(tmp_path, sub, clock)
    for t in ("BRE-1", "BRE-2", "BRE-3"):
        reg.submit(_spec(t), deadline_s=HOUR)
    for h in ("job-0", "job-1", "job-2"):
        sub.status[h] = JobState.RESOLVED

    collected = reg.collect_finished()

    tickets = sorted(j.ticket for j in collected)
    assert tickets == ["BRE-1", "BRE-3"], f"a readable job was discarded: {tickets}"
    # And the one that failed is still open, so the next tick retries it rather
    # than losing it.
    assert [r.handle for r in reg.outstanding()] == ["job-1"]
