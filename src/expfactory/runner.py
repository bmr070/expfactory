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

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from expfactory.verifier import Candidate, VerdictBundle, Verifier

LABEL_AGENT_READY = "agent-ready"
LABEL_NEEDS_HUMAN = "needs-human"
LANE_EMPIRICAL = "lane:empirical"

STATE_IN_PROGRESS = "In Progress"
STATE_IN_REVIEW = "In Review"
STATE_NEEDS_HUMAN = "Needs Human"

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
    because that is the human's channel for granting dispatch rights."""

    def open_tickets(self) -> Sequence[Ticket]: ...
    def label_actor(self, ticket_id: str, label: str) -> str | None: ...
    def comment(self, ticket_id: str, body: str) -> None: ...
    def set_state(self, ticket_id: str, state: str) -> None: ...


class AgentFailed(RuntimeError):
    """The agent session did not produce a verdict."""


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

    def run(self, ticket: Ticket) -> Candidate: ...


@runtime_checkable
class JobLedger(Protocol):
    """The compute-side registry, as much of it as the runner needs.

    A protocol rather than a `JobRegistry` import so the runner does not depend
    on the substrate half, and so a test can drive reconciliation without a
    registry, a log file and a fake substrate.
    """

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
        `jobs` is the compute registry. Optional, because the deterministic lane
        submits no GPU work — but on the empirical lane its absence means **no
        component in the system can notice a lost job**, since `sweep` is the
        only thing that can and nothing else calls it. Wire it.
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
        self._agent = agent
        self._verifier = verifier
        self._humans = human_allowlist
        self._max_concurrent = max_concurrent
        self._max_awaiting_human = max_awaiting_human
        self._jobs = jobs
        self._lane = lane
        if required_gates is None:
            required_gates = REQUIRED_EMPIRICAL_GATES if lane == LANE_EMPIRICAL else frozenset()
        self._required_gates = required_gates

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
        if ticket.state in (STATE_IN_PROGRESS, STATE_IN_REVIEW):
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

        # Reconcile first. A lost job's ticket has to reach a human in the same
        # tick that noticed it: the point of the sweep is that a ticket sitting
        # in progress with nobody working on it is the failure the registry
        # exists to prevent, and deferring it by one poll interval reintroduces
        # exactly that window.
        grounded = self._reconcile_lost(result)

        breaker = self._breaker_reason()
        in_flight = sum(1 for t in tickets if t.state == STATE_IN_PROGRESS)
        budget = max(0, self._max_concurrent - in_flight)
        # `tickets` was read before the sweep, so a ticket the sweep just moved
        # to needs-human still looks idle in it. It is in front of a human now,
        # and has to count against review capacity this tick rather than next.
        review_budget = self._review_budget(tickets, already_waiting=len(grounded))

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
        try:
            candidate = self._agent.run(ticket)
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

        # THE trust boundary. Adjudication happens here, on the runner's verifier,
        # never inside the agent session. See the module docstring.
        try:
            bundle = self._verifier.run(candidate)
        except Exception as exc:  # noqa: BLE001 — a candidate we cannot judge is not a result
            self._tracker.comment(
                ticket.id,
                f"Candidate could not be adjudicated: {exc}\n\n"
                "The evidence the agent returned did not survive verification. "
                "This is not a rejected experiment; it is a candidate the gates "
                "could not judge at all. Moved to needs-human.",
            )
            self._tracker.set_state(ticket.id, STATE_NEEDS_HUMAN)
            result.failed.append(ticket.id)
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
                ticket.id,
                f"Verdict refused: adjudicated without {names}.\n\n"
                "On the empirical lane a verdict must show it was judged by the "
                "preregistration gates. This one carries "
                f"[{', '.join(bundle.gate_names)}].\n\n"
                "The runner owns the verifier, so this is a misconfiguration of "
                "the runner rather than anything the agent did. Every ticket will "
                "hit it until the verifier is built with require_prereg=True.",
            )
            self._tracker.set_state(ticket.id, STATE_NEEDS_HUMAN)
            result.refused[ticket.id] = f"missing gates: {names}"
            return

        self._tracker.comment(ticket.id, proof_of_work(bundle))
        # To review, never to done. The runner does not approve its own work.
        self._tracker.set_state(ticket.id, STATE_IN_REVIEW)
        result.dispatched.append(ticket.id)


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
