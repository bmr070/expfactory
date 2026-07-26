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

## What it must never do

Approve its own work. The runner moves a finished ticket to review; a human or a
fresh-context reviewer decides. `promoted` still comes only from the gates.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from expfactory.verifier import VerdictBundle

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
    """Runs one ticket to a verdict inside a sandbox.

    Returns a VerdictBundle because promotion is decided by the gates, never by
    the agent and never by the runner. An agent that cannot finish raises.
    """

    def run(self, ticket: Ticket) -> VerdictBundle: ...


@dataclass
class TickResult:
    dispatched: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    # Kept apart from `failed` because they mean different things to whoever
    # reads the tick summary: `failed` is "the agent broke", `refused` is "the
    # agent returned something we will not accept as adjudicated".
    refused: dict[str, str] = field(default_factory=dict)


class Runner:
    def __init__(
        self,
        tracker: Tracker,
        agent: AgentSession,
        *,
        human_allowlist: frozenset[str],
        max_concurrent: int = 1,
        lane: str = LANE_EMPIRICAL,
        required_gates: frozenset[str] | None = None,
    ) -> None:
        """
        `required_gates` names gates a returned verdict must have been judged by.
        Defaults to `REQUIRED_EMPIRICAL_GATES` on the empirical lane and to
        nothing elsewhere — the deterministic lane has no preregistration.

        Pass an explicit `frozenset()` to disable the check. That is deliberately
        awkward to write and easy to spot in review: it is the difference between
        a deliberate exemption and a default nobody chose.
        """
        if not human_allowlist:
            # Fail closed. An empty allowlist would make every label acceptable,
            # which is the opposite of what this control is for.
            raise ValueError("human_allowlist must name at least one human")
        self._tracker = tracker
        self._agent = agent
        self._humans = human_allowlist
        self._max_concurrent = max_concurrent
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
            return f"no {self._lane} label — the runner cannot verify this lane"
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
        in_flight = sum(1 for t in self._tracker.open_tickets() if t.state == STATE_IN_PROGRESS)
        budget = max(0, self._max_concurrent - in_flight)

        for ticket in self._tracker.open_tickets():
            reason = self.eligibility(ticket)
            if reason is not None:
                result.skipped[ticket.id] = reason
                continue
            if budget == 0:
                result.skipped[ticket.id] = "concurrency limit reached"
                continue
            budget -= 1
            self._dispatch(ticket, result)
        return result

    def _dispatch(self, ticket: Ticket, result: TickResult) -> None:
        self._tracker.set_state(ticket.id, STATE_IN_PROGRESS)
        try:
            bundle = self._agent.run(ticket)
        except Exception as exc:  # noqa: BLE001 — any agent failure is the same to us
            # Never silently drop it. A ticket stuck in progress with nobody
            # working on it is the failure mode the whole registry exists to
            # prevent, and the same rule applies here.
            self._tracker.comment(
                ticket.id,
                f"Agent session failed and produced no verdict: {exc}\n\n"
                "Moved to needs-human rather than retried — a failure whose cause "
                "is unknown may repeat at cost.",
            )
            self._tracker.set_state(ticket.id, STATE_NEEDS_HUMAN)
            result.failed.append(ticket.id)
            return

        missing = self._required_gates - set(bundle.gate_names)
        if missing:
            # Refused before a human is asked to review it. A verdict that never
            # faced G-07 cannot show it was not metric-shopped, and putting it in
            # the review queue anyway launders that: the reviewer sees a
            # proof-of-work block that looks like every other one.
            #
            # Not retried, and deliberately not "fixed" by re-running with the
            # gate on. Whatever produced an unadjudicated verdict is a
            # configuration problem in the agent session, and re-running it
            # spends GPU to reach the same place.
            names = ", ".join(sorted(missing))
            self._tracker.comment(
                ticket.id,
                f"Verdict refused: adjudicated without {names}.\n\n"
                "On the empirical lane a verdict must show it was judged by the "
                "preregistration gates. This one carries "
                f"[{', '.join(bundle.gate_names)}].\n\n"
                "The agent session builds its own verifier, so this is a "
                "configuration fault there, not a result to review. Moved to "
                "needs-human rather than retried.",
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
        f"**{verdict}** — experiment `{bundle.exp_id}`",
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
        lines.append(f"  - [{mark}] `{gate['name']}` — {gate['detail']}")
    lines += [
        "",
        "_A rejection is a correct outcome. The bar is that the gates behaved, "
        "not that the number moved._",
    ]
    return "\n".join(lines)
