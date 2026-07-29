"""
registry — the JobRegistry and the compute-substrate seam (ticket N-08, M2-03).

The empirical lane splits in two: an agent writes an experiment in minutes, then
the experiment *runs* for hours on a GPU. The agent submits and detaches. What
follows is the part nobody else can own.

## What this records, and what it must never record

This holds what is **outstanding**. The ledger holds what **happened**.

That line is the whole reason M2-03 declined Metaflow: a second store that also
looks authoritative creates ambiguity about which record adjudicates. So there is
deliberately no "result" field here. `resolve()` takes an artifact reference and
nothing else; the artifact goes to the gates, and the gates' verdict goes to the
ledger. If this module ever grows a field someone reads to decide whether an
experiment succeeded, that failure mode has arrived by the back door.

## Why a registry has to exist at all

If a job is lost, nothing else can notice. The agent session ended hours ago; the
tracker only knows the ticket says "In Progress". A queue nobody watches is how a
six-hour run disappears silently.

## Durability

An append-only event log, replayed to derive state — the same shape as the
ledger, and for the same reason: history is not editable. A crash mid-run loses
nothing already written, and a fresh process sees the same outstanding set.

## Fail-closed

Cost caps are checked *before* submission, and an unreadable log refuses
submission rather than assuming zero spend. A breaker, once tripped, stays
tripped until a human resets it. W-12 puts cost and security on day one because
the precedents were each retrofitted after a shock.

## Who names the price (BRE-29)

`submit` used to take a `cost_estimate_usd` argument. Two defects came out of
that one signature and they are the same defect:

- Every check was `estimate > cap`, so `NaN` passed both caps (it compares false
  against everything) and a negative estimate passed both *and* subtracted from
  the trailing-day total, manufacturing budget that was never spent.
- More basically, the number was chosen by whoever was submitting. W-12 already
  recorded the general form: a self-reported cost cap is not a cap.

So costs are no longer an input. The substrate quotes them from its own
`RateCard`, over the job's deadline — the only window this registry enforces —
and every number that reaches the caps is validated finite and non-negative
first. Refuse, never coerce: a cost that cannot be read means spend is
**unknown**, and clamping it to zero is exactly the reading that must not happen.
"""

from __future__ import annotations

import enum
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeGuard, runtime_checkable

Clock = Callable[[], float]

SECONDS_PER_DAY = 86_400.0
SECONDS_PER_HOUR = 3_600.0


class JobState(enum.StrEnum):
    SUBMITTED = "submitted"
    RESOLVED = "resolved"
    LOST = "lost"


class RegistryRefused(RuntimeError):
    """Submission refused. Raised, never returned as a falsy value, because a
    silently dropped submission is indistinguishable from a lost job."""


class BreakerTripped(RegistryRefused):
    """The global circuit breaker is open. Requires a human to reset."""


class CostCapExceeded(RegistryRefused):
    """A per-job or per-day GPU cap would be breached by this submission."""


class InvalidJobInput(RegistryRefused):
    """A cost, cap or deadline that is not a usable number (BRE-29).

    A `RegistryRefused` rather than a `ValueError` because the outcome is the
    same one every other refusal here has: nothing was submitted, nothing was
    recorded, and the caller has to deal with it rather than read a falsy return
    and carry on.

    Refused, never coerced. Clamping `NaN` to zero, or `-100.0` to `0.0`, turns
    "this number is unreadable" into "this job is free", which is the single
    reading the caps must never make.
    """


# --------------------------------------------------------------------------- #
# Input validation. Every number that reaches a cap comparison goes through here
# first, because the comparison itself cannot defend the caps: `NaN > cap` is
# False and so is `NaN < cap`, so a non-finite estimate satisfies any test
# written either way round.
# --------------------------------------------------------------------------- #


def _finite(value: object) -> TypeGuard[float]:
    """A real, finite number — not a bool, not a string, not inf, not NaN.

    `bool` is excluded explicitly because it is a subclass of `int`, so `True`
    would otherwise price a job at one dollar and pass every check silently.
    """
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _checked_usd(value: object, what: str) -> float:
    """`value` read as dollars, or refused. Finite and non-negative."""
    if not _finite(value):
        raise InvalidJobInput(
            f"{what} is {value!r}, which is not a finite dollar amount. A cost "
            "that cannot be compared against a cap means spend is UNKNOWN, not "
            "zero: NaN passes every `>` test and inf fails every one."
        )
    amount = float(value)
    if amount < 0.0:
        raise InvalidJobInput(
            f"{what} is ${amount:.2f}. Negative dollars subtract from the "
            "trailing-day total and manufacture budget that was never spent, so "
            "the next job over the cap is admitted by arithmetic."
        )
    return amount


def _checked_seconds(value: object, what: str) -> float:
    """`value` read as a duration, or refused. Finite and strictly positive."""
    if not _finite(value):
        raise InvalidJobInput(
            f"{what} is {value!r}, which is not a finite number of seconds. A "
            "deadline that cannot be compared is a job nothing can call lost."
        )
    seconds = float(value)
    if seconds <= 0.0:
        raise InvalidJobInput(
            f"{what} is {seconds}s. A deadline at or before the moment of "
            "submission is already expired — the next sweep calls the job lost "
            "and opens the breaker — and it prices the run's billable window at "
            "nothing, which is the zero-cost failure C-01 exists to prevent."
        )
    return seconds


@dataclass(frozen=True)
class JobSpec:
    """What to run. Opaque to the registry — the substrate interprets it."""

    ticket: str
    command: tuple[str, ...]
    image: str
    gpu: str | None = None
    env: Mapping[str, str] | None = None


@dataclass(frozen=True)
class RunAttestation:
    """What the compute substrate can vouch for, which the agent cannot write.

    The second half of GH#33. Moving adjudication to the runner stopped the agent
    *declaring a verdict*; it did not stop the agent inventing the evidence the
    verdict is computed from. A `Candidate` assembled in an agent session is still
    just numbers the agent typed, and a run that never happened produces evidence
    indistinguishable from a run that did.

    This is the part a job's execution produces and a session cannot fake:

    - `job_handle` is issued by the substrate at submission and recorded in the
      registry's append-only log before the job starts. An agent quoting a handle
      the registry never issued is caught by lookup.
    - `exit_code`, `wall_seconds` and `command` come from the completion record
      the substrate wrote, not from anything the payload printed.
    - `artifact_sha256` binds these numbers to the exact bytes the run left
      behind, so an artifact edited after the fact no longer matches.

    **What it deliberately does not establish**: that the metric inside the
    artifact is *correct*. The agent still writes the training code, so it can
    still compute a wrong number honestly-looking. Closing that needs the
    evaluation to run in trusted code against data the agent never sees, which
    is a separate build (GH#39) and not something an attestation can reach.
    """

    job_handle: str
    command: tuple[str, ...]
    exit_code: int
    wall_seconds: float
    artifact_sha256: str


@dataclass(frozen=True)
class JobRecord:
    handle: str
    ticket: str
    submitted_at: float
    deadline_at: float
    cost_estimate_usd: float
    state: JobState
    # A *reference* to where the artifact landed, never the artifact's contents
    # and never a verdict. See the module docstring.
    artifact_ref: str | None = None

    @property
    def is_open(self) -> bool:
        return self.state is JobState.SUBMITTED


@dataclass(frozen=True)
class FinishedJob:
    """A job that completed, and the ticket waiting on it.

    Carries an artifact *reference*, never the artifact's contents and never a
    verdict — the same rule `resolve` follows. What the artifact means is decided
    by the gates.
    """

    handle: str
    ticket: str
    artifact_ref: str


@runtime_checkable
class RateCard(Protocol):
    """What a substrate charges for its own compute (BRE-29).

    The price is quoted by the side that owns the hardware and holds the
    credential, never by the side asking for the work. That is the whole change:
    a number the submitter picks is a *request*, and W-12 recorded the general
    form — a self-reported cost cap is not a cap.

    **Deliberately not keyed on hardware.** No GPU, no SKU, no device class
    appears in this signature. `ComputeSubstrate.submit`/`poll`/`fetch_artifact`
    mention no hardware anywhere, and this must not become the method that
    introduces it: the GPU under the desk is one lane of this factory, and edge
    devices and rented infra are the same seam at a different rate. What a
    substrate prices *on* is that substrate's own business, which is why it gets
    the whole `JobSpec` and the registry never reads a field of it.

    `billable_seconds` is the job's **deadline**, not a duration the caller
    estimates. The deadline is the only bound this factory actually enforces —
    `sweep` calls a job lost past it — so pricing that window prices the worst
    case the registry has committed to. A caller who wants a cheaper job has to
    accept a shorter deadline, which is a constraint rather than a claim.

    Contract: finite, non-negative, and non-decreasing in `billable_seconds`. A
    card that got cheaper the longer a job ran would break the argument above,
    and `tests/test_substrate_conformance.py` checks it for every substrate.
    """

    def price_usd(self, spec: JobSpec, billable_seconds: float) -> float: ...


@runtime_checkable
class ComputeSubstrate(Protocol):
    """The GPU side of the two-substrate split (W-06).

    The registry holds the credential for this; the agent never does. The agent
    asks the registry to submit, and receives an artifact reference back later.

    `rate_card` lives here rather than as a second `JobRegistry` argument so a
    registry cannot be wired to one substrate and priced by another's rates. The
    thing that runs the job is the thing that knows what running it costs.
    """

    def submit(self, spec: JobSpec) -> str: ...
    def poll(self, handle: str) -> JobState: ...
    def fetch_artifact(self, handle: str) -> str: ...
    def rate_card(self) -> RateCard: ...


class JobRegistry:
    """Durable record of outstanding submissions, plus the breaker and caps."""

    def __init__(
        self,
        path: str | Path,
        substrate: ComputeSubstrate,
        *,
        per_job_cap_usd: float,
        per_day_cap_usd: float,
        default_deadline_s: float = 6 * 3600.0,
        clock: Clock = time.time,
    ) -> None:
        # Validated before the log file is even created. A registry whose caps
        # cannot bind must not come into existence, let alone leave a file
        # behind that looks like a working ledger: a NaN cap passes every `>`
        # comparison below and a negative one refuses every job, and neither is
        # something a caller should be able to configure by accident.
        self._per_job_cap = _checked_usd(per_job_cap_usd, "per_job_cap_usd")
        self._per_day_cap = _checked_usd(per_day_cap_usd, "per_day_cap_usd")
        self._default_deadline_s = _checked_seconds(default_deadline_s, "default_deadline_s")

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._substrate = substrate
        self._clock = clock
        self._damaged = 0

    # -- event log ---------------------------------------------------------

    def _append(self, event: dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def _events(self) -> list[dict[str, Any]]:
        """Parse the log, or refuse.

        Fail closed: treating an unreadable log as "no spend so far" would hand
        an unbounded GPU budget to whatever corrupted it.

        But refusing must stay *recoverable*. An earlier version raised from
        here and nowhere else, which meant a single malformed line jammed the
        registry permanently — `reset_breaker` appends fine, and then
        `breaker_reason` raises again reading it back. A breaker with no path to
        reset is not a breaker, it is a brick.

        So a corrupt line is quarantined rather than fatal: everything parseable
        is returned, and the damage is reported through `log_damage()`, which
        `submit` consults and a human can see and act on.
        """
        out: list[dict[str, Any]] = []
        self._damaged = 0
        try:
            lines = self.path.read_text().splitlines()
        except OSError as exc:
            raise BreakerTripped(
                f"job log at {self.path} cannot be read ({exc}); refusing to submit"
            ) from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # Counted, not dropped silently: an unreadable row may have been
                # a submission, so spend accounting is no longer trustworthy.
                self._damaged += 1
        return out

    def log_damage(self) -> int:
        """Unparseable rows seen on the last read. Non-zero means spend accounting
        is incomplete, because a corrupt row may have been a submission."""
        self._events()
        return self._damaged

    def records(self) -> dict[str, JobRecord]:
        """Current state, derived by replaying the log in order.

        Deliberately a faithful read, not a validating one: whatever the log
        says is what this returns, so `sweep` can still notice a lost job whose
        row is otherwise unusable. Refusing on a bad *number* belongs in
        `spend_today_usd`, which is the only thing that does arithmetic on one.
        """
        out: dict[str, JobRecord] = {}
        for e in self._events():
            kind = e["event"]
            if kind == "submitted":
                out[e["handle"]] = JobRecord(
                    handle=e["handle"],
                    ticket=e["ticket"],
                    submitted_at=e["at"],
                    deadline_at=e["deadline_at"],
                    cost_estimate_usd=e["cost_estimate_usd"],
                    state=JobState.SUBMITTED,
                )
            elif kind in ("resolved", "lost") and e["handle"] in out:
                prior = out[e["handle"]]
                out[e["handle"]] = JobRecord(
                    handle=prior.handle,
                    ticket=prior.ticket,
                    submitted_at=prior.submitted_at,
                    deadline_at=prior.deadline_at,
                    cost_estimate_usd=prior.cost_estimate_usd,
                    state=JobState.RESOLVED if kind == "resolved" else JobState.LOST,
                    artifact_ref=e.get("artifact_ref"),
                )
        return out

    def attested_job(self, handle: str) -> dict[str, Any] | None:
        """What the log says about this handle, or None if it never issued one.

        Satisfies `AttestationSource` for G-10. Reads the append-only log, which
        is written at submission and resolution by the registry — never by an
        agent session. A candidate quoting a handle that is not here describes a
        run that this factory has no record of starting.

        Returns a plain mapping rather than a `JobRecord` so the gate can stay
        free of a registry import and be driven by a fixture.
        """
        record = self.records().get(handle)
        if record is None:
            return None
        return {
            "handle": record.handle,
            "ticket": record.ticket,
            "state": str(record.state),
            "submitted_at": record.submitted_at,
            "artifact_ref": record.artifact_ref,
        }

    def outstanding(self) -> list[JobRecord]:
        return [r for r in self.records().values() if r.is_open]

    # -- breaker and caps --------------------------------------------------

    def breaker_reason(self) -> str | None:
        """Why the breaker is open, or None. Any lost job opens it."""
        for e in reversed(self._events()):
            if e["event"] == "breaker_reset":
                return None
            if e["event"] == "lost":
                return f"job {e['handle']} exceeded its deadline and was never resolved"
            if e["event"] == "breaker_tripped":
                return str(e.get("reason", "tripped"))
        return None

    def trip_breaker(self, reason: str) -> None:
        self._append({"event": "breaker_tripped", "at": self._clock(), "reason": reason})

    def reset_breaker(self, operator: str) -> None:
        """Human-only. Deliberately requires naming who reset it, because the
        breaker exists to force someone to look at why it opened."""
        self._append({"event": "breaker_reset", "at": self._clock(), "operator": operator})

    def spend_today_usd(self) -> float:
        """Estimated GPU spend in the trailing 24h.

        Counts *every* submission, resolved or lost. A job that vanished still
        burned compute, and excluding it would let repeated losses spend without
        limit — the one direction this must not be wrong in.

        Fail closed on a poisoned row (BRE-29). The log is append-only but it is
        still a file on disk, and one bad number wrecks this total in exactly the
        wrong direction: `-100.0` subtracts real budget and admits the next job
        over the cap, `NaN` makes the sum NaN and every downstream `>` False. The
        old `sum(...)` did both silently. `submit` can no longer write either
        value, so anything unusable here arrived by editing the log — and the
        honest answer to "what did we spend today" is then *unknown*, which is
        the same stance an unreadable log already takes.
        """
        cutoff = self._clock() - SECONDS_PER_DAY
        total = 0.0
        for record in self.records().values():
            if not _finite(record.submitted_at):
                raise BreakerTripped(
                    f"job {record.handle} in {self.path} has submitted_at="
                    f"{record.submitted_at!r}, so it can be placed neither inside nor "
                    "outside the trailing day; today's spend is unknown. Repair or "
                    "archive the log before submitting."
                )
            if record.submitted_at < cutoff:
                continue
            try:
                total += _checked_usd(
                    record.cost_estimate_usd, f"job {record.handle}'s recorded cost in {self.path}"
                )
            except InvalidJobInput as exc:
                raise BreakerTripped(
                    f"{exc} Today's spend is therefore unknown. Repair or archive "
                    "the log before submitting."
                ) from exc
        return total

    # -- the operations the runner calls ------------------------------------

    def submit(
        self,
        spec: JobSpec,
        deadline_s: float | None = None,
    ) -> JobRecord:
        """Submit a job and record it. Checks run before the substrate is touched.

        Order matters: a job that is refused must not have been started, and a
        job that was started must have been recorded. Recording happens
        immediately after submission, so the widest possible gap is one process
        death between the two — which `reconcile` is there to catch.

        **The caller does not name the price (BRE-29).** There used to be a
        `cost_estimate_usd` argument here, which made every cap a number the
        submitting side chose. The substrate quotes it instead, from its own
        `RateCard`, over the job's deadline — the window `sweep` enforces and
        therefore the most this run can cost before something notices. The quote
        is validated before it is compared to anything, because `NaN > cap` is
        False and a negative quote lowers `spend_today_usd`.

        `deadline_s` is consequently load-bearing in two ways at once: it bounds
        the run and it prices it. A shorter deadline buys a cheaper job, which is
        the correct trade to expose.
        """
        # Checked first: it is a pure argument check, and its refusal names the
        # actual problem rather than surfacing as a strange price downstream.
        billable_s = (
            self._default_deadline_s  # already validated in __init__
            if deadline_s is None
            else _checked_seconds(deadline_s, "deadline_s")
        )

        reason = self.breaker_reason()
        if reason is not None:
            raise BreakerTripped(f"breaker open: {reason} — reset required before submitting")

        if self._damaged:
            # Spend accounting is incomplete, so the caps below cannot be trusted.
            raise BreakerTripped(
                f"{self._damaged} unreadable row(s) in {self.path}: a corrupt row may "
                "have been a submission, so today's spend is unknown. Repair or "
                "archive the log before submitting."
            )

        # The substrate is trusted with the credential, not with arithmetic: a
        # rate card that returns NaN or a negative number is a bug that would
        # otherwise disable the caps, so its answer is checked like any other.
        cost_estimate_usd = _checked_usd(
            self._substrate.rate_card().price_usd(spec, billable_s),
            f"the substrate's quote for {spec.ticket} over {billable_s:.0f}s",
        )

        if cost_estimate_usd > self._per_job_cap:
            raise CostCapExceeded(
                f"estimate ${cost_estimate_usd:.2f} exceeds per-job cap ${self._per_job_cap:.2f}"
            )
        projected = self.spend_today_usd() + cost_estimate_usd
        if projected > self._per_day_cap:
            raise CostCapExceeded(
                f"estimate ${cost_estimate_usd:.2f} would take today's spend to "
                f"${projected:.2f}, over the ${self._per_day_cap:.2f} daily cap"
            )

        now = self._clock()
        handle = self._substrate.submit(spec)
        record = JobRecord(
            handle=handle,
            ticket=spec.ticket,
            submitted_at=now,
            # The window that was priced is the window that is enforced. If these
            # two ever diverge, the quote stops being an upper bound.
            deadline_at=now + billable_s,
            cost_estimate_usd=cost_estimate_usd,
            state=JobState.SUBMITTED,
        )
        self._append(
            {
                "event": "submitted",
                "at": now,
                "handle": handle,
                "ticket": spec.ticket,
                "deadline_at": record.deadline_at,
                "cost_estimate_usd": cost_estimate_usd,
            }
        )
        return record

    def resolve(self, handle: str) -> str:
        """Fetch the artifact reference and close the record.

        Returns a *reference*, not a result. What the artifact means is decided
        by the gates, and recorded by the ledger.
        """
        artifact_ref = self._substrate.fetch_artifact(handle)
        self._append(
            {
                "event": "resolved",
                "at": self._clock(),
                "handle": handle,
                "artifact_ref": artifact_ref,
            }
        )
        return artifact_ref

    def collect_finished(self) -> list[FinishedJob]:
        """Jobs the substrate reports done, closed here and handed back with the
        ticket that is waiting on them.

        This is the collection half of the detach model (W-06, M2-03). The agent
        submits and walks away; nothing brings the artifact back into a verdict
        until something calls this.

        **Call it before `sweep`.** `sweep` resolves a job that finished *after*
        its deadline and returns it as not-lost, which closes the record without
        telling anyone which ticket was waiting — so the ticket would sit in
        `Running Unattended` forever. Collecting first means `sweep` only ever
        sees jobs that genuinely never answered.
        """
        out: list[FinishedJob] = []
        for record in self.outstanding():
            if self._substrate.poll(record.handle) is not JobState.RESOLVED:
                continue
            out.append(
                FinishedJob(
                    handle=record.handle,
                    ticket=record.ticket,
                    artifact_ref=self.resolve(record.handle),
                )
            )
        return out

    def sweep(self) -> list[JobRecord]:
        """Find jobs past their deadline, mark them lost, and open the breaker.

        This is the only thing in the system that can notice a lost job. It is
        called on a timer by the runner, not by an agent.

        Losses are **never auto-retried**. A job whose state is unknown may still
        be running and still burning budget; resubmitting can double-spend. The
        ticket goes to a human instead.
        """
        now = self._clock()
        lost: list[JobRecord] = []
        for record in self.outstanding():
            if now < record.deadline_at:
                continue
            if self._substrate.poll(record.handle) is JobState.RESOLVED:
                # Finished within the window but nobody collected it; not a loss.
                self.resolve(record.handle)
                continue
            self._append({"event": "lost", "at": now, "handle": record.handle})
            lost.append(record)
        return lost

    def reconcile(self) -> list[str]:
        """Handles the substrate reports finished that we still list as open.

        Covers the one gap `submit` cannot close: a process death between
        starting a job and recording it, and the ordinary case of a resolved job
        nobody collected yet.
        """
        return [
            r.handle
            for r in self.outstanding()
            if self._substrate.poll(r.handle) is JobState.RESOLVED
        ]
