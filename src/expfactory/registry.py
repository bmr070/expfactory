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

## Durable reservation, written before the side effect (BRE-30)

`submit` used to call the substrate and append its row *afterwards*. A process
death in that gap left a live, billable job with **no registry record at all**,
and `reconcile` could not find it: `reconcile` polls handles read back out of the
log, and there was no row to read. M2-03's box 10 — "if the queue loses a job,
someone must notice" — failed in exactly the case it exists to catch, and box 5,
"durable restart state", failed with it.

So the intent is now durable *before* the side effect:

    reserved ──bound──▶ job (handle) ──resolved──▶ RESOLVED
       │      │                       └──lost────▶ LOST
       │      └── the substrate answered; the handle is bound to the key
       ├──released ── the substrate stated it started nothing
       └──orphaned ── nothing bound it: the crash window. Breaker opens, a human
                      decides, and `abandoned` is the only way out.

A `reserved` row carries the idempotency key, the priced amount and the deadline,
and is flushed and `fsync`ed *before* `ComputeSubstrate.submit` is called. The
key rides along on `JobSpec.idempotency_key`, so the provider sees it and one
intent cannot become two jobs. `bound` then binds the returned handle to the key.

**An orphan is never auto-retried.** W-12 forbids auto-retry on cost, and a job
whose state is unknown may already have spent; resubmitting can double-spend a
GPU budget. It goes to a human with the breaker open, and it counts against
today's spend the whole time, because the honest reading of "we reserved money
and do not know what happened" is that the money may be gone.

## One writer at a time

Read-the-caps → reserve → submit → bind is a transition, not four statements.
Two runner processes that interleave it can both admit work against the same
daily budget: each reads a spend total that does not yet include the other's job.
`docs/SPEC.md` assumes a single writer for the **verdict ledger**, never for this
compute ledger, so the assumption had to be made real rather than inherited. An
advisory OS file lock on a sidecar next to the log does it — see
`_admission_lock`, which states the platform assumption it rests on.
"""

from __future__ import annotations

import contextlib
import enum
import json
import math
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any, Protocol, TypeGuard, runtime_checkable

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

Clock = Callable[[], float]

SECONDS_PER_DAY = 86_400.0
SECONDS_PER_HOUR = 3_600.0

# How long to wait between attempts at the admission lock. Short enough that a
# handoff between two runner processes is not perceptible, long enough that a
# contended lock does not spin a core.
_LOCK_POLL_S = 0.02


class JobState(enum.StrEnum):
    SUBMITTED = "submitted"
    RESOLVED = "resolved"
    LOST = "lost"


class ReservationState(enum.StrEnum):
    """Where a reservation sits between "we committed the money" and "we know
    what happened to it". See the state machine in the module docstring."""

    # Written, and nothing has bound a handle to it yet. Either the submission
    # is in flight right now, or the process died holding it.
    RESERVED = "reserved"
    # The substrate answered and its handle is durably bound to this key. From
    # here the job is tracked by handle like any other.
    BOUND = "bound"
    # The substrate stated it started nothing (`SubstrateDeclined`), so no
    # compute was bought. The only state that does not count against spend.
    RELEASED = "released"
    # Nobody bound it and nobody released it: the crash window. Needs a human.
    ORPHANED = "orphaned"
    # A named human looked at an orphan and closed it. Still counts against
    # spend, because "I looked" is not "nothing ran".
    ABANDONED = "abandoned"


class RegistryRefused(RuntimeError):
    """Submission refused. Raised, never returned as a falsy value, because a
    silently dropped submission is indistinguishable from a lost job."""


class BreakerTripped(RegistryRefused):
    """The global circuit breaker is open. Requires a human to reset."""


class CostCapExceeded(RegistryRefused):
    """A per-job or per-day GPU cap would be breached by this submission."""


class ReservationConflict(RegistryRefused):
    """This idempotency key already names an intent that is not cleanly bound.

    Idempotency here refuses rather than replays. A key whose reservation never
    bound a handle is the crash window, and the job behind it may be running and
    spending right now — so "run it again" is the one answer that can double the
    bill. A key a human already abandoned refuses too: a deliberate retry is a
    *new* intent and gets a new key, which keeps it visible in the log as the
    second attempt it is rather than hiding inside the first one's row.
    """


class SingleWriterTimeout(RegistryRefused):
    """Another process holds the admission lock and would not let go in time.

    Refused rather than waited out forever: a runner blocked indefinitely on a
    lock is a runner that has stopped sweeping, and nothing else in the system
    notices a lost job.
    """


class SubstrateDeclined(RuntimeError):
    """A substrate's way of saying **nothing was started**.

    Not a `RegistryRefused` — it is raised by the substrate, not by this module,
    and the registry re-raises it untouched.

    This distinction is load-bearing, and it is the only thing a substrate has to
    tell the registry beyond the four protocol methods. When `submit` raises
    *this*, the reservation is released and no compute was bought. When it raises
    anything else, the outcome is genuinely unknown — the provider may have
    started the job and failed on the way back — so the reservation stays open
    and a human decides. Guessing "it probably did not start" is how a live job
    becomes invisible, which is the whole defect BRE-30 exists to close.
    """


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


# --------------------------------------------------------------------------- #
# The OS lock primitives, one call each. Kept as module functions so the two
# platform branches sit side by side and `_admission_lock` reads as policy
# rather than as portability.
# --------------------------------------------------------------------------- #


def _try_lock(fh: IO[bytes]) -> bool:
    """One non-blocking attempt at the exclusive lock. True if it was taken.

    The one place in this module that answers with a bool rather than raising,
    and deliberately so: the caller is a retry loop two lines away that cannot
    forget to read it, and the *policy* — refuse, do not wait forever — raises
    `SingleWriterTimeout` from `_admission_lock` where a caller can see it.
    """
    fh.seek(0)
    try:
        if sys.platform == "win32":
            # Windows locks a byte range from the current position, so the seek
            # above is not decoration: two writers must contend for the *same*
            # byte or they both "succeed".
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fh: IO[bytes]) -> None:
    fh.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class JobSpec:
    """What to run. Opaque to the registry — the substrate interprets it.

    `idempotency_key` names *this intent to run it* (BRE-30). Leave it `None` and
    `submit` mints one, so every call is a distinct intent and nothing about the
    existing behaviour changes. Set it, and a second submission of the same key
    cannot become a second job: a bound key returns the record it already made,
    and an unbound one refuses because the first attempt's fate is unknown.

    It lives here, on the thing being submitted, rather than as a third argument
    to `submit`, for two reasons. The substrate then *receives* it — the registry
    hands the substrate a spec, so a key on the spec is a key the provider can
    deduplicate on, which is requirement 2 of the ticket and not something a
    registry-only field could satisfy. And `submit`'s signature stays exactly
    what BRE-29 left it: a caller names what to run and how long it may run, and
    still cannot name what it costs.
    """

    ticket: str
    command: tuple[str, ...]
    image: str
    # The one field on this contract that names a device class, and the registry
    # never reads it — only a substrate does. Generalising it (an `accelerator`
    # string, a resource mapping) is DEFERRED until a second substrate exists to
    # generalise against, because an abstraction invented for one hypothetical
    # caller is the speculative generality invariant 4 refuses. `env` already
    # carries `EXPFACTORY_VRAM_MIB` for the same class of need, which is the
    # idiom to reach for first. Recorded in BRE-33 so the moment a Jetson, a TPU
    # or a CPU-only job arrives is a decision rather than a surprise.
    gpu: str | None = None
    env: Mapping[str, str] | None = None
    idempotency_key: str | None = None


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
class Reservation:
    """A durable intent to spend, written before the substrate is touched.

    This is the row that closes the crash window. It exists on disk from the
    moment the caps admitted the job, so a process death anywhere after it still
    leaves something for `reconcile` to find — which is the difference between a
    lost job and an invisible one.

    `handle` is `None` until the substrate answers. That is not a missing value:
    it is the state, and it is the state the whole ticket is about.
    """

    key: str
    ticket: str
    reserved_at: float
    deadline_at: float
    cost_estimate_usd: float
    handle: str | None = None
    state: ReservationState = ReservationState.RESERVED

    @property
    def is_orphaned(self) -> bool:
        """No handle, and nobody said nothing started. The crash-window case."""
        return self.handle is None and self.state is ReservationState.RESERVED

    @property
    def is_charged(self) -> bool:
        """Whether this reservation counts against today's spend.

        Everything except `RELEASED`. A reservation that never bound a handle
        still counts, because the money may be gone and this factory's standing
        answer to "we cannot tell" is *unknown*, never *zero*.
        """
        return self.state is not ReservationState.RELEASED


@dataclass(frozen=True)
class Reconciliation:
    """What a startup reconciliation found — by handle *and* by key.

    Two fields rather than one list because they need different actions and
    conflating them hides the expensive one. `finished` is routine: a job the
    substrate says is done that this log still lists as open. `orphaned` is the
    crash window, and every entry in it means a human has to decide whether
    something is still running.
    """

    finished: tuple[str, ...] = ()
    orphaned: tuple[Reservation, ...] = ()


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
    """The long-job side of the two-substrate split (W-06).

    **The axis is duration and trust, not silicon.** W-06 splits an agent session
    — minutes, untrusted, LLM-metered — from a job that outlives it and holds a
    credential the agent never sees. This docstring named the accelerator until
    BRE-33, which was wrong in a way worth recording: `LocalGpuSubstrate` is the
    accelerator-bound implementation, and it sits *behind* this seam. The seam
    itself names no hardware anywhere in it, and most work entering this factory
    needs no accelerator at all.

    Reading this as "the accelerator protocol" is how a device class ends up in a
    signature that has to serve edge boards and rented instances too.

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
        lock_timeout_s: float = 30.0,
    ) -> None:
        # Validated before the log file is even created. A registry whose caps
        # cannot bind must not come into existence, let alone leave a file
        # behind that looks like a working ledger: a NaN cap passes every `>`
        # comparison below and a negative one refuses every job, and neither is
        # something a caller should be able to configure by accident.
        self._per_job_cap = _checked_usd(per_job_cap_usd, "per_job_cap_usd")
        self._per_day_cap = _checked_usd(per_day_cap_usd, "per_day_cap_usd")
        self._default_deadline_s = _checked_seconds(default_deadline_s, "default_deadline_s")
        # Seconds, and zero is legitimate — "one attempt, then refuse" is what
        # the two-writer fixtures use — so `_checked_seconds`, which demands a
        # strictly positive duration for a deadline, is the wrong check here.
        if not _finite(lock_timeout_s) or lock_timeout_s < 0.0:
            raise InvalidJobInput(
                f"lock_timeout_s is {lock_timeout_s!r}; it must be a finite, "
                "non-negative number of seconds to wait for the admission lock."
            )
        self._lock_timeout_s = float(lock_timeout_s)

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        # A sidecar rather than the log itself. Locking bytes of a file that is
        # also being appended to by every other operation invites a writer to
        # block on a reader's advisory lock for reasons unrelated to admission.
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._lock_path.touch(exist_ok=True)
        self._substrate = substrate
        self._clock = clock
        self._damaged = 0

    # -- event log ---------------------------------------------------------

    def _append(self, event: dict[str, Any]) -> None:
        """Append one event and make it **durable** before returning (BRE-30).

        `flush` plus `os.fsync`, because the whole reservation protocol rests on
        one claim: the row was on disk before the side effect happened. A record
        sitting in a userspace buffer when the process dies is a record that did
        not exist, and the crash window this ticket closes would simply move
        from "between two calls" to "between a write and a page flush".

        The cost is a real disk sync per event. That is a handful per job over a
        six-hour run, so it is not a cost worth reasoning about; losing a live
        billable job is.
        """
        with self.path.open("a") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    # -- the single-writer guard -------------------------------------------

    @contextlib.contextmanager
    def _admission_lock(self) -> Iterator[None]:
        """Exclusive across processes for the whole admission transition.

        **The assumption, stated out loud:** an advisory byte-range lock on a
        local filesystem, held by processes on one machine. `fcntl.flock` on
        POSIX, `msvcrt.locking` on Windows; both conflict between two open file
        descriptions, so this serialises two threads in one process as readily as
        two runner processes, which is what makes it testable. It does **not**
        survive a network filesystem with broken lock semantics, and it is not a
        distributed lock. That is the deployment this factory has — one box, one
        GPU, a handful of runner processes — and a database bought to do this
        job would be a second store that also looks authoritative, which is
        precisely what M2-03 declined Metaflow for.

        A lock is not a substitute for the reservation. It stops two *live*
        writers interleaving; it says nothing about a writer that died, whose
        half-finished transition only the durable `reserved` row can reveal.

        Bounded rather than blocking. `SingleWriterTimeout` refuses the
        submission, which is recoverable; a runner parked forever inside a lock
        has stopped calling `sweep`, and `sweep` is the only thing in this system
        that can notice a lost job.
        """
        give_up_at = time.monotonic() + self._lock_timeout_s
        with self._lock_path.open("r+b") as fh:
            while not _try_lock(fh):
                if time.monotonic() >= give_up_at:
                    raise SingleWriterTimeout(
                        f"another process held the admission lock on {self._lock_path} "
                        f"for more than {self._lock_timeout_s:g}s; refusing to submit "
                        "rather than admitting work against a spend total that "
                        "another writer is in the middle of changing"
                    )
                time.sleep(_LOCK_POLL_S)
            try:
                yield
            finally:
                _unlock(fh)

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

    def _replay(self) -> tuple[dict[str, JobRecord], dict[str, Reservation]]:
        """One pass over the log, yielding both views it can produce.

        Deliberately a faithful read, not a validating one: whatever the log
        says is what this returns, so `sweep` can still notice a lost job whose
        row is otherwise unusable. Refusing on a bad *number* belongs in
        `spend_today_usd`, which is the only thing that does arithmetic on one.
        That split is BRE-29's and BRE-30 does not touch it.

        A job appears in `records` exactly once — via `bound`, or via a legacy
        `submitted` row — so the two views can be summed without double-counting
        a job that has both a reservation and a handle.

        `submitted` is still replayed because the log is append-only: rows
        written before this protocol existed are history, and history is not
        editable. Nothing writes them any more.
        """
        jobs: dict[str, JobRecord] = {}
        held: dict[str, Reservation] = {}
        for e in self._events():
            kind = e["event"]
            if kind == "reserved":
                held[e["key"]] = Reservation(
                    key=e["key"],
                    ticket=e["ticket"],
                    reserved_at=e["at"],
                    deadline_at=e["deadline_at"],
                    cost_estimate_usd=e["cost_estimate_usd"],
                )
            elif kind == "bound" and e["key"] in held:
                held[e["key"]] = replace(
                    held[e["key"]], handle=e["handle"], state=ReservationState.BOUND
                )
                prior = held[e["key"]]
                jobs[e["handle"]] = JobRecord(
                    handle=e["handle"],
                    ticket=prior.ticket,
                    # The reservation's own timestamp, not the binding's: the
                    # deadline was computed from it, and `deadline_at -
                    # submitted_at` has to stay the window that was priced.
                    submitted_at=prior.reserved_at,
                    deadline_at=prior.deadline_at,
                    cost_estimate_usd=prior.cost_estimate_usd,
                    state=JobState.SUBMITTED,
                )
            elif kind in ("released", "orphaned", "abandoned") and e["key"] in held:
                held[e["key"]] = replace(held[e["key"]], state=ReservationState(kind))
            elif kind == "submitted":
                # Legacy shape, pre-BRE-30. See the docstring.
                jobs[e["handle"]] = JobRecord(
                    handle=e["handle"],
                    ticket=e["ticket"],
                    submitted_at=e["at"],
                    deadline_at=e["deadline_at"],
                    cost_estimate_usd=e["cost_estimate_usd"],
                    state=JobState.SUBMITTED,
                )
            elif kind in ("resolved", "lost") and e.get("handle") in jobs:
                prior_job = jobs[e["handle"]]
                jobs[e["handle"]] = JobRecord(
                    handle=prior_job.handle,
                    ticket=prior_job.ticket,
                    submitted_at=prior_job.submitted_at,
                    deadline_at=prior_job.deadline_at,
                    cost_estimate_usd=prior_job.cost_estimate_usd,
                    state=JobState.RESOLVED if kind == "resolved" else JobState.LOST,
                    artifact_ref=e.get("artifact_ref"),
                )
        return jobs, held

    def records(self) -> dict[str, JobRecord]:
        """Jobs that have a handle, keyed by it. Faithful replay — see `_replay`.

        A reservation that never bound a handle is deliberately *not* here, and
        cannot be: this mapping is keyed by handle and the crash-window case has
        no handle to key on. It is in `reservations()`, it is in
        `spend_today_usd`, and `reconcile` escalates it. Making it invisible to
        all three was the defect.
        """
        return self._replay()[0]

    def reservations(self) -> dict[str, Reservation]:
        """Every durable intent this log has ever recorded, keyed by its key.

        Including bound and closed ones, because idempotency is a question about
        history: "has this key been used" cannot be answered by a view that
        forgets the keys that were.
        """
        return self._replay()[1]

    def orphaned_reservations(self) -> list[Reservation]:
        """Reservations with no handle and no explanation — the crash window.

        The whole point of the protocol: this list is what a startup can see
        that the old submit-then-record shape left invisible.
        """
        return [r for r in self.reservations().values() if r.is_orphaned]

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
        """Why the breaker is open, or None. Any lost job opens it.

        So does an orphaned reservation (BRE-30). The two are the same event
        seen from different sides — compute this factory paid for and cannot
        account for — and an orphan is the worse of the two, because a lost job
        at least has a handle somebody can go and look up.
        """
        for e in reversed(self._events()):
            if e["event"] == "breaker_reset":
                return None
            if e["event"] == "lost":
                return f"job {e['handle']} exceeded its deadline and was never resolved"
            if e["event"] == "orphaned":
                return (
                    f"reservation {e['key']} was written but no handle was ever bound "
                    "to it: a job may have been started and may still be spending. "
                    "Check the substrate by that key before resetting."
                )
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

        **Unbound reservations count (BRE-30).** A reservation whose handle was
        never bound is money this factory committed and cannot account for, and
        the only safe reading of that is that it was spent. Excluding it would
        make a crash the cheapest way to get past the daily cap.
        """
        cutoff = self._clock() - SECONDS_PER_DAY
        jobs, held = self._replay()
        total = 0.0
        # Every job with a handle, plus every reservation that never got one.
        # `_replay` guarantees these do not overlap: a bound reservation is
        # already represented by its `JobRecord`.
        charges: list[tuple[str, str, float, float]] = [
            (f"job {r.handle}", "submitted_at", r.submitted_at, r.cost_estimate_usd)
            for r in jobs.values()
        ]
        charges += [
            (f"reservation {r.key}", "reserved_at", r.reserved_at, r.cost_estimate_usd)
            for r in held.values()
            if r.handle is None and r.is_charged
        ]
        for what, when, at, cost in charges:
            if not _finite(at):
                raise BreakerTripped(
                    f"{what} in {self.path} has {when}={at!r}, so it can be placed "
                    "neither inside nor outside the trailing day; today's spend is "
                    "unknown. Repair or archive the log before submitting."
                )
            if at < cutoff:
                continue
            try:
                total += _checked_usd(cost, f"{what}'s recorded cost in {self.path}")
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
        """Reserve, submit, bind. Checks run before the substrate is touched.

        Order matters: a job that is refused must not have been started, and a
        job that was started must have been recorded. **Recording now happens
        first** (BRE-30). The old shape called the substrate and appended
        afterwards, so a process death in between left a live billable job with
        no row at all — invisible to `reconcile`, which reads the log, and so
        invisible to the one component M2-03 made responsible for noticing.

        What is durable before `ComputeSubstrate.submit` is called: the key, the
        priced amount, the deadline. What is durable immediately after: the
        handle. A death anywhere in that sequence now leaves a `reserved` row
        with no `bound` row, which `reconcile` finds and escalates.

        **Idempotent by key.** `spec.idempotency_key` names the intent. Submit it
        twice and the second call does not reach the substrate: a bound key
        returns the record the first call made, and an unbound one raises
        `ReservationConflict`, because the first attempt may be running right now
        and "try again" is how one intent becomes two bills. Leave the key `None`
        and one is minted per call, which is the pre-BRE-30 behaviour exactly.

        **One writer at a time.** The whole transition runs under
        `_admission_lock`, so two runner processes cannot both read a spend total
        that excludes the other's job and both admit against the same daily cap.

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
        # actual problem rather than surfacing as a strange price downstream. It
        # is also outside the lock, because refusing an unusable argument does
        # not need to exclude anybody.
        billable_s = (
            self._default_deadline_s  # already validated in __init__
            if deadline_s is None
            else _checked_seconds(deadline_s, "deadline_s")
        )

        with self._admission_lock():
            # Before the breaker is read, not after: an orphan found here opens
            # the breaker, and the check below is then what refuses. That makes
            # "no new work while a reservation is unaccounted for" hold even in a
            # process that never calls `reconcile`.
            self._escalate_orphans()

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

            key = spec.idempotency_key or uuid.uuid4().hex
            # Carried on the spec so the *provider* sees it. A substrate that can
            # deduplicate on it cannot turn one intent into two jobs even if this
            # registry is wrong about whether the first one landed.
            keyed = spec if spec.idempotency_key else replace(spec, idempotency_key=key)

            existing = self.reservations().get(key)
            if existing is not None:
                return self._idempotent_result(existing)

            # The substrate is trusted with the credential, not with arithmetic: a
            # rate card that returns NaN or a negative number is a bug that would
            # otherwise disable the caps, so its answer is checked like any other.
            cost_estimate_usd = _checked_usd(
                self._substrate.rate_card().price_usd(keyed, billable_s),
                f"the substrate's quote for {spec.ticket} over {billable_s:.0f}s",
            )

            if cost_estimate_usd > self._per_job_cap:
                raise CostCapExceeded(
                    f"estimate ${cost_estimate_usd:.2f} exceeds per-job cap "
                    f"${self._per_job_cap:.2f}"
                )
            projected = self.spend_today_usd() + cost_estimate_usd
            if projected > self._per_day_cap:
                raise CostCapExceeded(
                    f"estimate ${cost_estimate_usd:.2f} would take today's spend to "
                    f"${projected:.2f}, over the ${self._per_day_cap:.2f} daily cap"
                )

            now = self._clock()
            record = JobRecord(
                handle="",  # bound below; a reservation has no handle yet
                ticket=spec.ticket,
                submitted_at=now,
                # The window that was priced is the window that is enforced. If
                # these two ever diverge, the quote stops being an upper bound.
                deadline_at=now + billable_s,
                cost_estimate_usd=cost_estimate_usd,
                state=JobState.SUBMITTED,
            )
            # THE line this ticket exists for. Durable — flushed and fsynced —
            # before anything bills.
            self._append(
                {
                    "event": "reserved",
                    "at": now,
                    "key": key,
                    "ticket": spec.ticket,
                    "deadline_at": record.deadline_at,
                    "cost_estimate_usd": cost_estimate_usd,
                }
            )

            try:
                handle = self._substrate.submit(keyed)
            except SubstrateDeclined as exc:
                # The substrate asserts it started nothing, so the money is not
                # spent and the reservation is released rather than left to be
                # escalated. Any other exception falls through untouched: the
                # outcome is unknown, the reservation stays open, and the next
                # reconciliation calls it what it is.
                self._append(
                    {"event": "released", "at": self._clock(), "key": key, "reason": str(exc)}
                )
                raise

            self._append({"event": "bound", "at": self._clock(), "key": key, "handle": handle})
            return replace(record, handle=handle)

    def _idempotent_result(self, existing: Reservation) -> JobRecord:
        """The answer to submitting a key that has been used before.

        Bound: hand back the record the first call made. Nothing reaches the
        substrate and nothing is charged twice, which is what idempotent means.

        Anything else: refuse. An unbound reservation may be a job running right
        now, and a released or abandoned one has already been reasoned about by
        someone — in both cases a resubmission under the same key would either
        double-spend or silently overwrite the history of what happened.
        """
        if existing.state is ReservationState.BOUND and existing.handle is not None:
            job = self.records().get(existing.handle)
            if job is not None:
                return job
        raise ReservationConflict(
            f"idempotency key {existing.key!r} is already reserved for ticket "
            f"{existing.ticket} at ${existing.cost_estimate_usd:.2f} and is "
            f"{existing.state}. Refusing to submit it a second time: the first "
            "attempt may be running and spending right now. Reconcile it, then "
            "use a new key if a fresh attempt is genuinely wanted."
        )

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

    def _escalate_orphans(self) -> list[Reservation]:
        """Mark every unbound reservation orphaned, which opens the breaker.

        Not locked, and deliberately not: both callers already hold the
        admission lock or take it around this. Appending twice for one key would
        be harmless anyway — replay is idempotent — but tripping the breaker
        twice for one orphan would read as two incidents.

        Called from `submit` as well as `reconcile` so the guarantee does not
        depend on anybody remembering to reconcile at startup.
        """
        found = self.orphaned_reservations()
        now = self._clock()
        for reservation in found:
            self._append({"event": "orphaned", "at": now, "key": reservation.key})
        return found

    def reconcile(self) -> Reconciliation:
        """Startup reconciliation, by handle **and** by key.

        Two questions, and until BRE-30 this could only ask the first:

        - `finished` — handles the substrate reports done that this log still
          lists as open. The ordinary case of a result nobody collected.
        - `orphaned` — reservations with no handle bound to them. The crash
          window: something was priced, admitted and very possibly started, and
          then the process died before anything could record what came back.

        The second list used to be unreachable. `reconcile` polls handles read
        out of the log, so a job whose row was never written was invisible to
        the one component M2-03 made responsible for noticing it. Now the row
        exists before the job does, and this is where it surfaces.

        **An orphan is escalated, never retried.** Each one appends an
        `orphaned` row, which opens the breaker and stops every further
        submission until a human resets it — the same treatment a lost job gets,
        for the same W-12 reason. Automatically resubmitting would be the single
        worst available move: the job may be running, so the retry doubles the
        bill for one result. `abandon_reservation` is the way out, and it takes a
        human's name.
        """
        with self._admission_lock():
            orphaned = self._escalate_orphans()
        return Reconciliation(
            finished=tuple(
                r.handle
                for r in self.outstanding()
                if self._substrate.poll(r.handle) is JobState.RESOLVED
            ),
            orphaned=tuple(orphaned),
        )

    def abandon_reservation(self, key: str, operator: str, reason: str) -> None:
        """Human-only. Close an orphaned reservation that someone has checked.

        The only exit from `orphaned`, and it exists because a state with no exit
        is not a state, it is a jam: `reconcile` would re-escalate the same key
        after every breaker reset and the registry would never accept work again.

        Deliberately requires a name and a reason, like `reset_breaker`, because
        what is being recorded is a person's claim to have gone and looked at the
        substrate. It does **not** refund the reservation — the amount still
        counts against the day. "I checked and I think nothing ran" is not the
        same as the substrate stating it started nothing, and only the second one
        releases money.
        """
        existing = self.reservations().get(key)
        if existing is None:
            raise ReservationConflict(
                f"no reservation {key!r} in {self.path}; nothing to abandon. "
                "Refusing rather than writing a row about an intent that was "
                "never recorded."
            )
        if existing.state is not ReservationState.ORPHANED:
            raise ReservationConflict(
                f"reservation {key!r} is {existing.state}, not orphaned. Only a "
                "reservation nothing ever bound needs a human to close it."
            )
        self._append(
            {
                "event": "abandoned",
                "at": self._clock(),
                "key": key,
                "operator": operator,
                "reason": reason,
            }
        )
