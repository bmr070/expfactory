"""
Ticket 07 — the outer loop.

The runner is the trust boundary: it decides what gets worked on. So the tests
that matter are the refusals, not the happy path. A runner that dispatches
something it should not have is worse than one that dispatches nothing.
"""

from __future__ import annotations

import pytest

from expfactory.runner import (
    LABEL_AGENT_READY,
    LANE_EMPIRICAL,
    STATE_IN_PROGRESS,
    STATE_IN_REVIEW,
    STATE_NEEDS_HUMAN,
    AgentSession,
    Runner,
    Ticket,
    Tracker,
    proof_of_work,
)
from expfactory.verifier import Candidate, GateVerifier

HUMANS = frozenset({"bmr070"})


def _bundle(metric: float = 0.80, overlap: int = 0):
    runs = [
        dict(
            seed=s,
            val_metric=metric,
            train_ids_hash="t",
            eval_ids_hash="e",
            overlap_count=overlap,
            wall_seconds=0.0,
            extra={"latency_ms": 12.0},
        )
        for s in range(3)
    ]
    cand = Candidate(hypothesis="h", config={}, code_hash="abc", runs=runs, cost_usd=0.4)
    return GateVerifier(id_factory=lambda: "e1").run(cand)


class FakeTracker:
    def __init__(self, tickets: list[Ticket], actors: dict[str, str] | None = None) -> None:
        self.tickets = {t.id: t for t in tickets}
        self.actors = actors or {}
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []

    def open_tickets(self):
        return list(self.tickets.values())

    def label_actor(self, ticket_id: str, label: str) -> str | None:
        return self.actors.get(ticket_id)

    def comment(self, ticket_id: str, body: str) -> None:
        self.comments.append((ticket_id, body))

    def set_state(self, ticket_id: str, state: str) -> None:
        self.states.append((ticket_id, state))
        prior = self.tickets[ticket_id]
        self.tickets[ticket_id] = Ticket(
            prior.id, prior.title, prior.body, prior.labels, state=state
        )


class FakeAgent:
    def __init__(self, bundle=None, raises: Exception | None = None) -> None:
        self._bundle = bundle if bundle is not None else _bundle()
        self._raises = raises
        self.seen: list[Ticket] = []

    def run(self, ticket: Ticket):
        self.seen.append(ticket)
        if self._raises is not None:
            raise self._raises
        return self._bundle


def _ticket(tid="BRE-1", labels=(LABEL_AGENT_READY, LANE_EMPIRICAL), state="Todo") -> Ticket:
    return Ticket(id=tid, title="t", body="do the thing", labels=frozenset(labels), state=state)


def _runner(tracker, agent, **over):
    kwargs = dict(human_allowlist=HUMANS, max_concurrent=1)
    kwargs.update(over)
    return Runner(tracker, agent, **kwargs)  # type: ignore[arg-type]


def test_protocols_are_satisfied_structurally():
    assert isinstance(FakeTracker([]), Tracker)
    assert isinstance(FakeAgent(), AgentSession)


# ---- the refusals ----------------------------------------------------------


def test_a_label_applied_by_a_non_human_does_not_dispatch():
    """The defense that does not race.

    Checking only that `agent-ready` is *present* trusts whoever applied it. A
    compromised agent with issues:write could label its own ticket, and the
    stripping workflow races with polling — a label applied and removed seconds
    later is still visible to a poll in between. Asking who applied it does not.
    """
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "expfactory-agent[bot]"})
    agent = FakeAgent()
    result = _runner(tracker, agent).tick()

    assert agent.seen == []
    assert result.dispatched == []
    assert "not a human on the allowlist" in result.skipped["BRE-1"]


def test_an_unattributable_label_does_not_dispatch():
    """If the tracker cannot say who applied it, that is not a yes."""
    tracker = FakeTracker([_ticket()], actors={})
    result = _runner(tracker, FakeAgent()).tick()
    assert result.dispatched == []
    assert "cannot establish who applied" in result.skipped["BRE-1"]


def test_missing_agent_ready_does_not_dispatch():
    tracker = FakeTracker([_ticket(labels=(LANE_EMPIRICAL,))], actors={"BRE-1": "bmr070"})
    result = _runner(tracker, FakeAgent()).tick()
    assert result.skipped["BRE-1"] == "not agent-ready"


def test_a_lane_the_runner_cannot_verify_does_not_dispatch():
    """Dispatching without a verifier for the lane means producing an unadjudicated
    result, which is the one output this factory must never emit."""
    tracker = FakeTracker([_ticket(labels=(LABEL_AGENT_READY,))], actors={"BRE-1": "bmr070"})
    result = _runner(tracker, FakeAgent()).tick()
    assert "cannot verify this lane" in result.skipped["BRE-1"]


def test_needs_human_is_never_picked_up():
    tracker = FakeTracker(
        [_ticket(labels=(LABEL_AGENT_READY, LANE_EMPIRICAL, "needs-human"))],
        actors={"BRE-1": "bmr070"},
    )
    result = _runner(tracker, FakeAgent()).tick()
    assert "needs-human" in result.skipped["BRE-1"]


def test_an_empty_allowlist_is_refused_at_construction():
    """An empty allowlist would make every label acceptable — the opposite of
    what this control is for. Fail closed rather than vacuously true."""
    with pytest.raises(ValueError, match="at least one human"):
        Runner(FakeTracker([]), FakeAgent(), human_allowlist=frozenset())


# ---- concurrency ------------------------------------------------------------


def test_concurrency_is_bounded_by_work_already_in_flight():
    tickets = [_ticket("BRE-1"), _ticket("BRE-2", state=STATE_IN_PROGRESS)]
    tracker = FakeTracker(tickets, actors={"BRE-1": "bmr070", "BRE-2": "bmr070"})
    result = _runner(tracker, FakeAgent(), max_concurrent=1).tick()

    assert result.dispatched == []
    assert result.skipped["BRE-1"] == "concurrency limit reached"


def test_only_up_to_the_limit_are_dispatched():
    tickets = [_ticket("BRE-1"), _ticket("BRE-2"), _ticket("BRE-3")]
    tracker = FakeTracker(tickets, actors={t.id: "bmr070" for t in tickets})
    result = _runner(tracker, FakeAgent(), max_concurrent=2).tick()
    assert len(result.dispatched) == 2
    assert result.skipped["BRE-3"] == "concurrency limit reached"


# ---- the happy path, and what it must not do -------------------------------


def test_a_verdict_is_posted_and_the_ticket_goes_to_review_not_done():
    """The runner never approves its own work — L2 is a human."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    result = _runner(tracker, FakeAgent()).tick()

    assert result.dispatched == ["BRE-1"]
    assert tracker.states == [("BRE-1", STATE_IN_PROGRESS), ("BRE-1", STATE_IN_REVIEW)]
    assert "Done" not in [s for _, s in tracker.states]
    assert tracker.comments and "PROMOTED" in tracker.comments[0][1]


def test_a_rejected_experiment_is_still_a_completed_run():
    """Rejecting every proposed gain is a passing run (W-03). A rejection is
    reported and reviewed, not treated as a failure of the runner."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    agent = FakeAgent(bundle=_bundle(overlap=9))  # leakage -> rejected
    result = _runner(tracker, agent).tick()

    assert result.dispatched == ["BRE-1"]
    assert result.failed == []
    assert "REJECTED" in tracker.comments[0][1]
    assert tracker.states[-1] == ("BRE-1", STATE_IN_REVIEW)


def test_an_agent_failure_goes_to_needs_human_not_silence():
    """A ticket stuck In Progress with nobody working on it is the failure the
    registry exists to prevent; the same rule applies here."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    agent = FakeAgent(raises=RuntimeError("sandbox died"))
    result = _runner(tracker, agent).tick()

    assert result.failed == ["BRE-1"]
    assert tracker.states[-1] == ("BRE-1", STATE_NEEDS_HUMAN)
    assert "sandbox died" in tracker.comments[0][1]


def test_the_runner_never_writes_labels():
    """Labels are the human's channel for granting dispatch rights. A runner that
    can set them can grant itself work."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    _runner(tracker, FakeAgent()).tick()
    assert not hasattr(tracker, "labels_written")
    assert tracker.tickets["BRE-1"].labels == frozenset({LABEL_AGENT_READY, LANE_EMPIRICAL})


def test_ticket_body_is_passed_to_the_agent_as_data():
    """The body is untrusted input handed to the sandbox, not read by the runner."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    agent = FakeAgent()
    _runner(tracker, agent).tick()
    assert agent.seen[0].body == "do the thing"


# ---- proof of work ----------------------------------------------------------


def test_proof_of_work_reconstructs_the_verdict_without_narrative():
    text = proof_of_work(_bundle())
    assert "PROMOTED" in text
    assert "`abc`" in text  # code hash
    assert "[0, 1, 2]" in text  # seeds
    assert "latency_ms" in text  # secondary metrics recorded
    assert "no_leakage" in text  # gate-by-gate


def test_proof_of_work_names_the_blocking_gates_on_rejection():
    text = proof_of_work(_bundle(overlap=4))
    assert "REJECTED" in text
    assert "no_leakage" in text
