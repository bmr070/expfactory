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
"""

from __future__ import annotations

import enum
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

Clock = Callable[[], float]

SECONDS_PER_DAY = 86_400.0


class JobState(str, enum.Enum):
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


@dataclass(frozen=True)
class JobSpec:
    """What to run. Opaque to the registry — the substrate interprets it."""

    ticket: str
    command: tuple[str, ...]
    image: str
    gpu: str | None = None
    env: Mapping[str, str] | None = None


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


@runtime_checkable
class ComputeSubstrate(Protocol):
    """The GPU side of the two-substrate split (W-06).

    The registry holds the credential for this; the agent never does. The agent
    asks the registry to submit, and receives an artifact reference back later.
    """

    def submit(self, spec: JobSpec) -> str: ...
    def poll(self, handle: str) -> JobState: ...
    def fetch_artifact(self, handle: str) -> str: ...


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
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._substrate = substrate
        self._per_job_cap = per_job_cap_usd
        self._per_day_cap = per_day_cap_usd
        self._default_deadline_s = default_deadline_s
        self._clock = clock

    # -- event log ---------------------------------------------------------

    def _append(self, event: dict[str, Any]) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def _events(self) -> list[dict[str, Any]]:
        try:
            return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            # Fail closed. Treating an unreadable log as "no spend so far" would
            # hand an unbounded GPU budget to whatever corrupted it.
            raise BreakerTripped(
                f"job log at {self.path} is unreadable ({exc}); refusing to submit"
            ) from exc

    def records(self) -> dict[str, JobRecord]:
        """Current state, derived by replaying the log in order."""
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
        """
        cutoff = self._clock() - SECONDS_PER_DAY
        return sum(r.cost_estimate_usd for r in self.records().values() if r.submitted_at >= cutoff)

    # -- the operations the runner calls ------------------------------------

    def submit(
        self,
        spec: JobSpec,
        cost_estimate_usd: float,
        deadline_s: float | None = None,
    ) -> JobRecord:
        """Submit a job and record it. Checks run before the substrate is touched.

        Order matters: a job that is refused must not have been started, and a
        job that was started must have been recorded. Recording happens
        immediately after submission, so the widest possible gap is one process
        death between the two — which `reconcile` is there to catch.
        """
        reason = self.breaker_reason()
        if reason is not None:
            raise BreakerTripped(f"breaker open: {reason} — reset required before submitting")

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
            deadline_at=now + (deadline_s if deadline_s is not None else self._default_deadline_s),
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
