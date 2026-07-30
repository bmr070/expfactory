"""
runner — the outer loop (ticket 07).

Polls the tracker, claims one eligible ticket, dispatches an agent, and posts the
verdict with its proof-of-work. It is the only component that decides *what gets
worked on*, which makes it the trust boundary.

## The tracker is untrusted input

Anyone who can file a ticket can prompt-inject the factory. A ticket's body is
data, never instructions to the runner — the runner reads labels and ids, and
hands the body to the agent inside its sandbox. Nothing in a ticket can change
what the runner does with it.

Eligibility is therefore *allowlisted*, not inferred:

  1. the ticket carries `agent-ready`, and
  2. that label was applied by a **human on the allowlist**, checked against the
     tracker's own record of who applied it, and
  3. the ticket carries a lane label the runner knows how to verify.

Rule 2 is the one that matters. Checking only that the label is *present* trusts
whoever put it there, and a compromised agent with `issues: write` could label
its own ticket. There is a repo workflow that strips such labels, but it races
with polling — a label applied and removed seconds later can still be seen by a
poll in between. Asking *who applied it* does not race.

## The agent returns evidence, not a verdict

`AgentSession.run` returns a `Candidate` — runs, metrics, id hashes, the diff,
the preregistration it cites — and **the runner adjudicates it**. That ordering
is the point.

It used to return a `VerdictBundle`. GH#4 then added a check that the bundle
carried the preregistration gates, which closed the misconfiguration case and
not the dishonest one: nothing stopped a sandboxed agent constructing a bundle
with `promoted=True`, `blocked_by=()` and exactly the gate names the runner was
known to want. Every check was on data the agent authored.

> You cannot verify a result by asking the thing that produced it what the
> result was.

Moving adjudication here also makes three controls real that were previously
requests. `require_prereg=True`, the `PreregStore`, and G-09's `DatasetGrouping`
are all constructor arguments to a verifier **the agent cannot reach**.

### What this does not fix

The agent still authors the *evidence*. It could report a `val_metric` that no
run produced. That is a smaller and different problem — fabricated evidence has
to stay internally consistent across seeds, id hashes, overlap counts and the
diff, and any of those can be checked — but it is not solved here. Solving it
means the numbers coming from a compute substrate the agent does not control,
which is what W-06's split and the `JobRegistry` exist for. See GH#33.

## What it must never do

Approve its own work. The runner moves a finished ticket to review; a human or a
fresh-context reviewer decides. `promoted` still comes only from the gates.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from expfactory.sandbox import SecretStore, WorkspaceRefused, WorkspaceRoot
from expfactory.verifier import Candidate, VerdictBundle, Verifier

LABEL_AGENT_READY = "agent-ready"
LABEL_NEEDS_HUMAN = "needs-human"
LANE_EMPIRICAL = "lane:empirical"

STATE_IN_PROGRESS = "In Progress"
STATE_IN_REVIEW = "In Review"
STATE_NEEDS_HUMAN = "Needs Human"
# The agent submitted a GPU job and detached. Nobody is watching; the ticket
# waits here until the registry reports the job finished or lost. This is the
# state W-06's split exists to make possible — see `Submitted`.
STATE_RUNNING_UNATTENDED = "Running Unattended"

# Gates a hill-climb verdict must have been adjudicated under before this runner
# will pass it to a human as reviewable work.
#
# G-07 and G-08 only run when `GateVerifier` is built with `require_prereg=True`,
# and that defaults to False for a good reason: the same gate set judges one-off
# candidates with no lineage, the adversarial fixtures among them, where
# demanding a preregistration would reject everything and destroy their
# diagnostic value.
#
# Sound, and it leaves the safe configuration opt-in — and opt-in safety fails
# quietly. The runner cannot fix that by setting the flag, because **the runner
# does not build the verifier; the agent session does**, and the agent is the
# untrusted party. Asking it to enable its own anti-metric-shopping gate is not a
# control.
#
# So the check is on the artifact instead of the configuration: a returned
# verdict must *show* it was judged by these gates. Same move as the substrate
# guard — ask what the evidence says, never who produced it.
REQUIRED_EMPIRICAL_GATES = frozenset({"preregistration", "prereg_churn"})


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    body: str
    labels: frozenset[str]
    state: str = "Todo"


@runtime_checkable
class Tracker(Protocol):
    """GitHub Issues in production. Read-only for eligibility; writes are limited
    to state transitions and comments — the runner never edits a ticket's labels,
    because that is the human's channel for granting dispatch rights.

    `writable_states` is what the runner asks *before* dispatching, so that a
    state an adapter cannot express is a wiring error rather than an exception
    thrown at a ticket already holding a GPU. Every adapter has this answer for
    free — it is an allowlist constant on both real ones — and no adapter is
    permitted to answer it with a network call's worth of uncertainty.
    """

    def open_tickets(self) -> Sequence[Ticket]: ...
    def label_actor(self, ticket_id: str, label: str) -> str | None: ...
    def comment(self, ticket_id: str, body: str) -> None: ...
    def set_state(self, ticket_id: str, state: str) -> None: ...
    def writable_states(self) -> frozenset[str]: ...


class AgentFailed(RuntimeError):
    """The agent session did not produce a verdict."""


class StateUnreachable(RuntimeError):
    """The tracker cannot express a state this runner will need to write.

    A configuration error, raised where configuration happens. The alternative
    shipped for one release: the runner dispatched, the agent submitted a GPU
    job, the runner tried to park the ticket in `Running Unattended`, and the
    adapter refused a state its map had no entry for — after the spend.

    Every ticket would hit it, so this is not a per-ticket refusal. A runner
    wired against a tracker that cannot move work through the states it needs
    should not start.
    """


@dataclass(frozen=True)
class Submitted:
    """The agent started a compute job and detached. **Not a result.**

    The other thing `AgentSession.run` may return. It carries a job handle and
    nothing else — deliberately no metrics, no summary, no partial verdict, since
    at this point the job has not finished and anything the agent said about its
    outcome would be a prediction dressed as evidence.

    This is what W-06's two-substrate split is for. An agent session lasts
    minutes; a training run lasts hours. Returning a `Candidate` synchronously
    means holding an LLM-metered session open to do nothing but wait, which is the
    wrong shape at any timeout value. So the agent submits, returns this, and the
    session ends. The registry owns the run from there.
    """

    handle: str
    note: str = ""


@runtime_checkable
class ResultCollector(Protocol):
    """Turns a finished job's artifact into evidence the gates can judge.

    Owned by the runner, never by the agent — the agent's session is over by the
    time this runs. Whether the artifact itself is trustworthy is a separate
    question and the remaining half of GH#33: the substrate produced it, but the
    agent wrote the code that filled it. G-10 closes the "did this run at all"
    half by requiring the handle be one the registry issued.
    """

    def collect(self, ticket_id: str, handle: str, artifact_ref: str) -> Candidate: ...


@runtime_checkable
class FinishedJobRef(Protocol):
    """What `collect_finished` hands back. `FinishedJob` satisfies it."""

    @property
    def handle(self) -> str: ...
    @property
    def ticket(self) -> str: ...
    @property
    def artifact_ref(self) -> str: ...


@runtime_checkable
class AgentSession(Protocol):
    """Runs one ticket inside a sandbox and returns the **evidence** it produced.

    A `Candidate`, deliberately, not a `VerdictBundle`. The agent reports what it
    did — the runs, the metrics, the id hashes, the diff, which preregistration
    it cited — and the runner decides what that amounts to. An agent that cannot
    finish raises.

    This used to return a verdict, and that was wrong for a reason no amount of
    checking the verdict could fix: **you cannot verify a result by asking the
    thing that produced it what the result was.** See the module docstring.
    """

    def run(self, ticket: Ticket, workspace: Path | None = None) -> Candidate | Submitted: ...


@runtime_checkable
class AgentSessionFactory(Protocol):
    """Builds the session, so the runner decides what it can reach (BRE-38).

    Ticket 07's last unmet box, stated there as *"closing that box means
    inverting who builds what, which is a real change and not a wiring detail."*
    This is the inversion.

    An `AgentSession` handed in pre-built is a session somebody else configured.
    Invariant 6 says the agent never holds tracker or GPU credentials, and today
    that holds only because every caller has been careful — the runner has no way
    to enforce it on an object it did not construct. A factory closes that: the
    runner holds the factory, the factory holds the `SecretStore`, and a session
    exists only once its environment has been scrubbed.

    The shape is not invented here. TRL's OpenEnv `opencode` example builds its
    sessions the same way — `ResourceSessionFactory` constructed with the
    verifier and the sandbox backend, so the session cannot choose either:

        FreePortOpenCodeSessionFactory(sandbox_backend=..., verifier=...)

    Their reason is ours: the held-out tests must not be reachable from the thing
    being scored. Invariant 9 expressed as a constructor rather than as a runtime
    check, which is strictly stronger because there is no moment at which the
    wrong wiring exists.
    """

    def create(self, ticket: Ticket, workspace: Path | None = None) -> AgentSession: ...


class FixedSessionFactory:
    """Adapts one already-built `AgentSession` onto the factory seam.

    The migration path, and deliberately a *named* thing rather than an implicit
    fallback: a caller using this is choosing to keep constructing its own
    session, and the name says so at the call site.

    It grants no isolation — the session it returns is the one it was handed, and
    whatever that session can reach, it could reach already. `SandboxedSessionFactory`
    is the one that actually enforces anything.
    """

    def __init__(self, session: AgentSession) -> None:
        self._session = session

    def create(self, ticket: Ticket, workspace: Path | None = None) -> AgentSession:
        return self._session


class SandboxedSessionFactory:
    """Builds each session with the runner's secrets stripped from its environment.

    The factory holds the `SecretStore`; the session never sees it. `child_env`
    removes **every declared name**, not merely the ones a given run happens to
    use — SPEC §15.3's normative MUST, and the difference matters because a
    secret nobody remembered to use is exactly the one that leaks.

    `build` receives an environment that has already been scrubbed. It cannot opt
    out, because it never holds the store to opt out of.
    """

    def __init__(
        self,
        build: Callable[[Ticket, Path | None, Mapping[str, str]], AgentSession],
        *,
        secrets: SecretStore,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self._build = build
        self._secrets = secrets
        self._base_env = dict(base_env) if base_env is not None else dict(os.environ)

    def create(self, ticket: Ticket, workspace: Path | None = None) -> AgentSession:
        return self._build(ticket, workspace, self._secrets.child_env(self._base_env))


@runtime_checkable
class JobLedger(Protocol):
    """The compute-side registry, as much of it as the runner needs.

    A protocol rather than a `JobRegistry` import so the runner does not depend
    on the substrate half, and so a test can drive reconciliation without a
    registry, a log file and a fake substrate.
    """

    def collect_finished(self) -> Sequence[FinishedJobRef]: ...
    def sweep(self) -> Sequence[LostJob]: ...
    def breaker_reason(self) -> str | None: ...


@runtime_checkable
class LostJob(Protocol):
    """What `sweep` hands back. `JobRecord` satisfies this structurally."""

    @property
    def handle(self) -> str: ...
    @property
    def ticket(self) -> str: ...


@dataclass
class TickResult:
    dispatched: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    # Tickets grounded this tick because the compute job behind them vanished.
    # Separate from `failed` because nothing failed — a job stopped answering,
    # which is worse, since it may still be running and still spending.
    lost: dict[str, str] = field(default_factory=dict)
    # Kept apart from `failed` because they mean different things to whoever
    # reads the tick summary: `failed` is "the agent broke", `refused` is "the
    # agent returned something we will not accept as adjudicated".
    refused: dict[str, str] = field(default_factory=dict)
    # Detached this tick: the agent submitted a job and its session ended. Not a
    # result and not a failure -- the ticket now waits on the substrate.
    submitted: dict[str, str] = field(default_factory=dict)
    # Infrastructure that could not answer this tick, named rather than raised
    # (BRE-41). Not keyed by ticket, because the whole point is that we do not
    # know which tickets are affected: a registry that cannot replay its log
    # cannot tell us whose jobs finished.
    #
    # Separate from `failed` and `refused`, which are both statements about a
    # ticket. This is a statement about the runner's own footing, and a tick that
    # reports one is a tick whose silence about everything else means less.
    errors: list[str] = field(default_factory=list)
    # Finished jobs turned back into verdicts this tick.
    collected: dict[str, str] = field(default_factory=dict)


class Runner:
    def __init__(
        self,
        tracker: Tracker,
        agent: AgentSession,
        verifier: Verifier,
        *,
        human_allowlist: frozenset[str],
        max_concurrent: int = 1,
        max_awaiting_human: int | None = None,
        workspaces: WorkspaceRoot | None = None,
        collector: ResultCollector | None = None,
        jobs: JobLedger | None = None,
        lane: str = LANE_EMPIRICAL,
        required_gates: frozenset[str] | None = None,
    ) -> None:
        """
        `verifier` is positional and required. It is the whole point of this
        class: the runner adjudicates, so that the untrusted party never decides
        whether its own work passed.

        Build it the way the hill-climb needs — `require_prereg=True`, a
        `PreregStore`, and a `DatasetGrouping` when the task's data is segmented
        from longer captures. All three are now genuinely enforceable, because
        the agent no longer supplies the verifier.

        `required_gates` names gates a verdict must have been judged by, as a
        check on *this* configuration. Defaults to `REQUIRED_EMPIRICAL_GATES` on
        the empirical lane and to nothing elsewhere — the deterministic lane has
        no preregistration.

        Pass an explicit `frozenset()` to disable that check. Deliberately
        awkward to write and easy to spot in review: the difference between a
        chosen exemption and a default nobody chose.

        `max_awaiting_human` bounds how many tickets may be sitting in a human's
        queue before dispatch stalls. MAP.md's founding constraint — "throughput
        ceiling is human review bandwidth; any design that raises agent
        concurrency without raising review capacity is rejected by default" —
        was prose until now. Prose does not ratchet (invariant 8).

        Left `None` by default, which means unbounded, because a value picked
        here would be a guess about one particular human's capacity. Set it
        deliberately per deployment.
        `workspaces` gives each ticket a private directory, prepared by the
        runner and handed to the agent. Optional, and its absence is ticket 07's
        unmet acceptance box rather than a design choice: without it two
        concurrent tickets share whatever directory the agent picks.

        `collector` turns a finished job's artifact into a `Candidate` the gates
        can judge. Without it the runner can dispatch detached work but never
        bring it back, so a `Submitted` return is refused rather than parked in a
        state nothing can leave.

        `jobs` is the compute registry. Optional, because the deterministic lane
        submits no GPU work — but on the empirical lane its absence means **no
        component in the system can notice a lost job**, since `sweep` is the
        only thing that can and nothing else calls it. Wire it.

        Detachment needs *both*, which is why they are checked together below
        rather than one at a time: a collector alone cannot notice a job that
        stopped answering, and a registry alone cannot turn one that finished
        back into evidence.
        """
        if not human_allowlist:
            # Fail closed. An empty allowlist would make every label acceptable,
            # which is the opposite of what this control is for.
            raise ValueError("human_allowlist must name at least one human")
        if max_awaiting_human is not None and max_awaiting_human < 1:
            # Zero would be a runner that never dispatches, which `needs-human`
            # already expresses and which is better said out loud than encoded
            # as a limit nobody reads.
            raise ValueError("max_awaiting_human must be at least 1, or None for unbounded")
        self._tracker = tracker
        # BRE-38. A bare session is wrapped rather than rejected, so every
        # existing caller keeps working while the seam the runner actually
        # depends on becomes the factory. `isinstance` against a
        # `runtime_checkable` Protocol rather than `hasattr("create")`: a
        # session that happens to grow a `create` method must not silently
        # change meaning.
        self._agents: AgentSessionFactory = (
            agent if isinstance(agent, AgentSessionFactory) else FixedSessionFactory(agent)
        )
        self._verifier = verifier
        self._humans = human_allowlist
        self._max_concurrent = max_concurrent
        self._max_awaiting_human = max_awaiting_human
        self._workspaces = workspaces
        self._collector = collector
        self._jobs = jobs
        self._lane = lane
        if required_gates is None:
            required_gates = REQUIRED_EMPIRICAL_GATES if lane == LANE_EMPIRICAL else frozenset()
        self._required_gates = required_gates
        self._check_states_are_reachable()

    def _can_detach(self) -> bool:
        """Whether a `Submitted` return can be honoured at all.

        Both halves or neither. `_collect_finished` already needs the pair, and a
        runner holding only one of them can park a ticket in a state it has no
        way to move out of.
        """
        return self._collector is not None and self._jobs is not None

    def _check_states_are_reachable(self) -> None:
        """Refuse to start against a tracker that cannot write what we will need.

        Asked once, at construction, because the answer cannot change during a
        run and because "before dispatching" is the only useful time to learn it.
        A per-ticket check would notice the same problem one GPU job later.

        `Running Unattended` is required only when detachment is possible. A
        deterministic-lane runner with no registry never writes it, and demanding
        it there would force every tracker to model a state that lane has no
        concept of.
        """
        needed = {STATE_IN_PROGRESS, STATE_IN_REVIEW, STATE_NEEDS_HUMAN}
        if self._can_detach():
            needed.add(STATE_RUNNING_UNATTENDED)

        declare = getattr(self._tracker, "writable_states", None)
        if declare is None:
            # Fail closed. A tracker that will not say what it can write cannot
            # be checked, and dispatching anyway gambles a GPU job on the guess.
            raise StateUnreachable(
                f"{type(self._tracker).__name__} does not implement writable_states(), so "
                "the runner cannot establish that the states it needs are reachable "
                f"before it dispatches. Needed: {sorted(needed)}."
            )

        missing = needed - set(declare())
        if missing:
            raise StateUnreachable(
                f"{type(self._tracker).__name__} cannot write {sorted(missing)}, which this "
                f"runner needs. It declares {sorted(declare())}. This is a wiring error and "
                "it is raised here rather than at the transition, where it would surface "
                "after an agent session had already run and possibly submitted a job."
            )

    # -- the trust boundary -------------------------------------------------

    def eligibility(self, ticket: Ticket) -> str | None:
        """Why this ticket is NOT eligible, or None if it is.

        Returning the reason rather than a bool keeps refusals legible: a ticket
        that silently never runs is indistinguishable from one nobody filed.
        """
        if LABEL_AGENT_READY not in ticket.labels:
            return "not agent-ready"
        if self._lane not in ticket.labels:
            return f"no {self._lane} label: the runner cannot verify this lane"
        if LABEL_NEEDS_HUMAN in ticket.labels:
            return "needs-human: a breaker tripped or this is a red-lane path"
        if ticket.state in (STATE_IN_PROGRESS, STATE_IN_REVIEW, STATE_RUNNING_UNATTENDED):
            # `Running Unattended` matters most here: its agent session ended, so
            # nothing else marks it busy, and re-dispatching would start a second
            # GPU job for work already paid for and still running.
            return f"already {ticket.state}"

        actor = self._tracker.label_actor(ticket.id, LABEL_AGENT_READY)
        if actor is None:
            return "cannot establish who applied agent-ready"
        if actor not in self._humans:
            # The defense that does not race with the label-stripping workflow.
            return f"agent-ready applied by {actor!r}, who is not a human on the allowlist"
        return None

    def eligible(self) -> list[Ticket]:
        return [t for t in self._tracker.open_tickets() if self.eligibility(t) is None]

    # -- one poll cycle -----------------------------------------------------

    def tick(self) -> TickResult:
        result = TickResult()
        # Read once. This used to poll twice — the count and then the loop — so a
        # ticket that changed state between the two calls was counted against one
        # budget and dispatched against another.
        tickets = self._tracker.open_tickets()

        # Collect BEFORE sweeping. `sweep` resolves a job that finished after its
        # deadline and returns it as not-lost, which closes the record without
        # naming the ticket that was waiting — so that ticket would sit in
        # `Running Unattended` forever. Collecting first means the sweep only ever
        # sees jobs that genuinely never answered.
        collected = self._collect_finished(result)

        # Then reconcile. A lost job's ticket has to reach a human in the same
        # tick that noticed it: the point of the sweep is that a ticket sitting
        # in progress with nobody working on it is the failure the registry
        # exists to prevent, and deferring it by one poll interval reintroduces
        # exactly that window.
        grounded = self._reconcile_lost(result)

        breaker = self._breaker_reason()
        # An unattended run holds a GPU and is unfinished work, so it counts
        # against concurrency even though its agent session ended. Without this
        # the runner would start a job per tick and queue them all on one card.
        in_flight = sum(
            1 for t in tickets if t.state in (STATE_IN_PROGRESS, STATE_RUNNING_UNATTENDED)
        )
        budget = max(0, self._max_concurrent - in_flight)
        # `tickets` was read before the sweep, so a ticket the sweep just moved
        # to needs-human still looks idle in it. It is in front of a human now,
        # and has to count against review capacity this tick rather than next.
        review_budget = self._review_budget(tickets, already_waiting=len(grounded) + len(collected))

        for ticket in tickets:
            reason = self.eligibility(ticket)
            if reason is not None:
                # Specific reason first: a ticket that was never eligible should
                # say so, rather than be attributed to a breaker it never reached.
                result.skipped[ticket.id] = reason
                continue
            if ticket.id in grounded:
                # `tickets` was read before the sweep, so this one still looks
                # dispatchable in memory. It is not.
                result.skipped[ticket.id] = "grounded this tick: its compute job was lost"
                continue
            if breaker is not None:
                result.skipped[ticket.id] = f"compute breaker open: {breaker}"
                continue
            if budget == 0:
                result.skipped[ticket.id] = "concurrency limit reached"
                continue
            if review_budget == 0:
                result.skipped[ticket.id] = (
                    f"review queue full: {self._max_awaiting_human} ticket(s) already "
                    "awaiting a human. Dispatch stalls rather than queueing more."
                )
                continue
            budget -= 1
            if review_budget is not None:
                # Decremented before dispatch, not after, and not conditioned on
                # where the ticket lands. A dispatch that ends in needs-human
                # rather than review still consumed the human's attention, and
                # erring toward under-dispatch is the safe side of this control.
                review_budget -= 1
            self._dispatch(ticket, result)
        return result

    def _review_budget(self, tickets: Sequence[Ticket], already_waiting: int = 0) -> int | None:
        """How many more tickets may be handed to a human this tick, or None when
        unbounded.

        Counts `needs-human` alongside `in-review` because both are the factory
        putting work in front of a person. That gives this one control a second
        job: a run of correlated failures piles into needs-human and stalls
        dispatch on its own — the circuit breaker W-08 noted Symphony lacks,
        falling out of the review bound rather than needing its own mechanism.
        """
        if self._max_awaiting_human is None:
            return None
        waiting = sum(1 for t in tickets if t.state in (STATE_IN_REVIEW, STATE_NEEDS_HUMAN))
        return max(0, self._max_awaiting_human - waiting - already_waiting)

    def _breaker_reason(self) -> str | None:
        """Why dispatch is halted, or None.

        The registry already refuses *job submission* while the breaker is open.
        That is not sufficient on its own: without this check the runner keeps
        starting agent sessions, each of which does its work and only discovers
        the halt when it tries to submit — so a tripped breaker costs one full
        inference session per ticket to observe, and costs nothing at all to
        observe on a lane that submits no jobs.

        A breaker that only the spender consults is not a breaker.
        """
        if self._jobs is None:
            return None
        return self._jobs.breaker_reason()

    def _collect_finished(self, result: TickResult) -> set[str]:
        """Turn every finished job back into a verdict. Returns their ticket ids.

        The other half of the detach model. `_reconcile_lost` notices a job that
        never answered; this notices one that did, and is the only thing that
        moves a ticket out of `Running Unattended`.
        """
        if self._jobs is None or self._collector is None:
            return set()

        # **The registry call belongs inside the guard, not beside it (BRE-41).**
        #
        # `collect_finished()` sat outside the per-job `try` below, so anything it
        # raised escaped `tick()` entirely — and the registry can raise on a
        # damaged log, which is a state it is *designed* to reach. The
        # consequence is the one the whole detach model exists to prevent: the
        # poll loop dies, so `sweep` never runs in that tick, so nothing notices
        # a lost job.
        #
        # Caught here and reported rather than swallowed. A registry that cannot
        # answer is a condition for a human, not a reason to stop polling: the
        # next tick still sweeps, and the breaker still refuses new work.
        try:
            finished = self._jobs.collect_finished()
        except Exception as exc:  # noqa: BLE001 — a registry that cannot answer must not kill the loop
            result.errors.append(
                f"the job registry could not report finished jobs: {exc}. Tickets parked "
                "in Running Unattended stay parked until this is repaired; the sweep in "
                "this tick still ran."
            )
            return set()

        done: set[str] = set()
        for job in finished:
            try:
                candidate = self._collector.collect(job.ticket, job.handle, job.artifact_ref)
            except Exception as exc:  # noqa: BLE001 — an artifact we cannot read is not a result
                self._tracker.comment(
                    job.ticket,
                    f"Job `{job.handle}` finished, but its artifact could not be "
                    f"read into a candidate: {exc}\n\nThe run happened and was "
                    "paid for; what it produced is unusable. Moved to needs-human "
                    "rather than retried.",
                )
                self._tracker.set_state(job.ticket, STATE_NEEDS_HUMAN)
                result.failed.append(job.ticket)
                done.add(job.ticket)
                continue
            self._adjudicate(job.ticket, candidate, result, dispatched=False)
            done.add(job.ticket)
        return done

    def _reconcile_lost(self, result: TickResult) -> set[str]:
        """Ground the tickets whose compute jobs vanished. Returns their ids.

        Never retries. The registry is explicit about why, and it is the same
        reason the ticket goes to a human: a job whose state is unknown may still
        be running and still burning budget, so resubmitting can double-spend.
        """
        if self._jobs is None:
            return set()

        grounded: set[str] = set()
        for job in self._jobs.sweep():
            self._tracker.comment(
                job.ticket,
                f"Compute job `{job.handle}` passed its deadline without resolving.\n\n"
                "Moved to needs-human and **not retried**. The job may still be "
                "running and still spending, so resubmitting could double-spend. "
                "Check the substrate before restarting anything.\n\n"
                "This also opened the compute breaker, which halts dispatch until "
                "a human resets it by name.",
            )
            self._tracker.set_state(job.ticket, STATE_NEEDS_HUMAN)
            result.lost[job.ticket] = job.handle
            grounded.add(job.ticket)
        return grounded

    def _dispatch(self, ticket: Ticket, result: TickResult) -> None:
        self._tracker.set_state(ticket.id, STATE_IN_PROGRESS)

        # Prepared by the runner, not requested by the agent. A workspace the
        # agent chose would be a workspace the agent could point anywhere, which
        # is the whole of what `WorkspaceRoot` refuses.
        workspace: Path | None = None
        if self._workspaces is not None:
            try:
                workspace = self._workspaces.prepare(ticket.id).path
            except WorkspaceRefused as exc:
                # Before the agent runs, so nothing has happened yet. A ticket id
                # that cannot be a directory name is a tracker problem or an
                # attack, and either way it is not the agent's to resolve.
                self._tracker.comment(
                    ticket.id,
                    f"No workspace could be prepared for this ticket: {exc}\n\n"
                    "Nothing was dispatched.",
                )
                self._tracker.set_state(ticket.id, STATE_NEEDS_HUMAN)
                result.refused[ticket.id] = f"workspace refused: {exc}"
                return

        try:
            # Constructed here, per ticket, so the runner is the thing that
            # decides what this session can reach (BRE-38). Previously it ran
            # whatever object the caller had wired.
            produced = self._agents.create(ticket, workspace).run(ticket, workspace)
        except Exception as exc:  # noqa: BLE001 — any agent failure is the same to us
            # Never silently drop it. A ticket stuck in progress with nobody
            # working on it is the failure mode the whole registry exists to
            # prevent, and the same rule applies here.
            self._tracker.comment(
                ticket.id,
                f"Agent session failed and produced no candidate: {exc}\n\n"
                "Moved to needs-human rather than retried: a failure whose cause "
                "is unknown may repeat at cost.",
            )
            self._tracker.set_state(ticket.id, STATE_NEEDS_HUMAN)
            result.failed.append(ticket.id)
            return

        if isinstance(produced, Submitted):
            self._detach(ticket, produced, result)
            return

        self._adjudicate(ticket.id, produced, result, dispatched=True)

    def _detach(self, ticket: Ticket, submitted: Submitted, result: TickResult) -> None:
        """Park a ticket on a running job. The agent session is over."""
        if not self._can_detach():
            # Refuse rather than park. `Running Unattended` is only leavable by
            # two components acting together, and a ticket in a state nothing can
            # move it out of is worse than one that never started: it looks like
            # work in flight, so nobody goes looking.
            #
            # Both are named because they fail differently and a reader has to
            # know which one to wire. Without a collector, a job that finishes
            # perfectly never becomes a verdict. Without a registry, `sweep`
            # never runs, so a job that dies is never even noticed to be gone —
            # and *that* is the case where the ticket sits forever, because
            # nothing will ever report the job at all.
            missing = ", ".join(
                name
                for name, wired in (
                    ("ResultCollector", self._collector is not None),
                    ("JobLedger", self._jobs is not None),
                )
                if not wired
            )
            self._tracker.comment(
                ticket.id,
                f"Agent submitted job `{submitted.handle}` and detached, but this "
                f"runner has no {missing}, so nothing here can bring that job back: "
                "a collector turns a finished artifact into a verdict, and the "
                "registry is the only thing that notices a job that stopped "
                "answering.\n\nRefused rather than parked in a state nothing can "
                "leave. The job may still be running — check the substrate.",
            )
            self._tracker.set_state(ticket.id, STATE_NEEDS_HUMAN)
            result.refused[ticket.id] = f"detached with no {missing} wired"
            return

        note = f"\n\n{submitted.note}" if submitted.note else ""
        self._tracker.comment(
            ticket.id,
            f"Compute job `{submitted.handle}` submitted; the agent session has "
            "ended. The registry owns the run now. The runner collects the "
            "artifact when it finishes, or grounds this ticket if the job passes "
            f"its deadline without answering.{note}",
        )
        self._tracker.set_state(ticket.id, STATE_RUNNING_UNATTENDED)
        result.submitted[ticket.id] = submitted.handle

    def _adjudicate(
        self, ticket_id: str, candidate: Candidate, result: TickResult, *, dispatched: bool
    ) -> None:
        """THE trust boundary. Runs on the runner's verifier, never inside the
        agent session, and is reached identically whether the candidate came back
        synchronously or was collected from a detached job hours later."""
        try:
            bundle = self._verifier.run(candidate, ticket=ticket_id)
        except Exception as exc:  # noqa: BLE001 — a candidate we cannot judge is not a result
            self._tracker.comment(
                ticket_id,
                f"Candidate could not be adjudicated: {exc}\n\n"
                "The evidence the agent returned did not survive verification. "
                "This is not a rejected experiment; it is a candidate the gates "
                "could not judge at all. Moved to needs-human.",
            )
            self._tracker.set_state(ticket_id, STATE_NEEDS_HUMAN)
            result.failed.append(ticket_id)
            return

        missing = self._required_gates - set(bundle.gate_names)
        if missing:
            # Now a check on the *runner's own* configuration, which is what GH#4
            # asked for and could not previously get: a verifier built without
            # require_prereg produces verdicts that never faced G-07, and a
            # verdict that never faced G-07 cannot show it was not metric-shopped.
            #
            # Kept as a per-verdict refusal rather than a constructor assertion
            # because `Verifier` is a protocol — the runner cannot introspect an
            # arbitrary implementation's configuration, only look at what it
            # produced. Evidence over declaration, the same rule as everywhere
            # else here.
            names = ", ".join(sorted(missing))
            self._tracker.comment(
                ticket_id,
                f"Verdict refused: adjudicated without {names}.\n\n"
                "On the empirical lane a verdict must show it was judged by the "
                "preregistration gates. This one carries "
                f"[{', '.join(bundle.gate_names)}].\n\n"
                "The runner owns the verifier, so this is a misconfiguration of "
                "the runner rather than anything the agent did. Every ticket will "
                "hit it until the verifier is built with require_prereg=True.",
            )
            self._tracker.set_state(ticket_id, STATE_NEEDS_HUMAN)
            result.refused[ticket_id] = f"missing gates: {names}"
            return

        self._tracker.comment(ticket_id, proof_of_work(bundle))
        # To review, never to done. The runner does not approve its own work.
        self._tracker.set_state(ticket_id, STATE_IN_REVIEW)
        if dispatched:
            result.dispatched.append(ticket_id)
        else:
            result.collected[ticket_id] = candidate.code_hash


def proof_of_work(bundle: VerdictBundle) -> str:
    """The block a human reads instead of the agent's narrative summary.

    Every claim here is reconstructible from the ledger row alone. It reports the
    verdict the gates reached; it does not argue for it.
    """
    verdict = "PROMOTED" if bundle.promoted else f"REJECTED ({', '.join(bundle.blocked_by)})"
    lines = [
        f"**{verdict}** - experiment `{bundle.exp_id}`",
        "",
        f"- metric: `{bundle.mean_metric:.4f}` over {len(bundle.seeds)} seeds {list(bundle.seeds)}",
        f"- code: `{bundle.code_hash}`",
        f"- cost: ${bundle.cost_usd:.2f}",
    ]
    if bundle.prereg_hash:
        lines.append(f"- preregistration: `{bundle.prereg_hash}`")
    if bundle.metrics:
        other = {k: v for k, v in bundle.metrics.items() if k != "val_metric"}
        if other:
            lines.append(
                "- also recorded: " + ", ".join(f"`{k}`={v:.4f}" for k, v in sorted(other.items()))
            )
    lines += ["", "Gates:"]
    for gate in bundle.artifact.get("gates", []):
        mark = "PASS" if gate["passed"] else "FAIL"
        lines.append(f"  - [{mark}] `{gate['name']}`: {gate['detail']}")
    lines += [
        "",
        "_A rejection is a correct outcome. The bar is that the gates behaved, "
        "not that the number moved._",
    ]
    return "\n".join(lines)
