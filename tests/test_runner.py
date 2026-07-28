"""
Ticket 07 — the outer loop.

The runner is the trust boundary: it decides what gets worked on. So the tests
that matter are the refusals, not the happy path. A runner that dispatches
something it should not have is worse than one that dispatches nothing.
"""

from __future__ import annotations

import dataclasses

import pytest

from expfactory.runner import (
    LABEL_AGENT_READY,
    LANE_EMPIRICAL,
    REQUIRED_EMPIRICAL_GATES,
    STATE_IN_PROGRESS,
    STATE_IN_REVIEW,
    STATE_NEEDS_HUMAN,
    AgentSession,
    JobLedger,
    LostJob,
    Runner,
    Ticket,
    Tracker,
    proof_of_work,
)
from expfactory.verifier import Candidate, GateVerifier

HUMANS = frozenset({"bmr070"})


def _candidate(metric: float = 0.80, overlap: int = 0) -> Candidate:
    """The evidence an agent session returns. Not a verdict — the runner judges."""
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
    return Candidate(hypothesis="h", config={}, code_hash="abc", runs=runs, cost_usd=0.4)


class FakeVerifier:
    """Stands in for a correctly-configured hill-climb verifier.

    Wraps the real `GateVerifier` and appends the preregistration gate names,
    rather than filing an actual preregistration. What the runner checks is
    `bundle.gate_names`; standing up a ledger and a matching prereg in every
    runner test would be testing G-07 here instead of in its own file.

    `adjudicating=False` is the *misconfigured runner* — a verifier built without
    require_prereg — which is now the only way those gates can go missing, since
    the agent no longer supplies the verifier.
    """

    def __init__(self, adjudicating: bool = True, raises: Exception | None = None) -> None:
        self._adjudicating = adjudicating
        self._raises = raises
        self.seen: list[Candidate] = []

    def run(self, candidate: Candidate):
        self.seen.append(candidate)
        if self._raises is not None:
            raise self._raises
        bundle = GateVerifier(id_factory=lambda: "e1").run(candidate)
        if self._adjudicating:
            bundle = dataclasses.replace(
                bundle, gate_names=(*bundle.gate_names, *sorted(REQUIRED_EMPIRICAL_GATES))
            )
        return bundle


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
    def __init__(self, candidate=None, raises: Exception | None = None) -> None:
        self._candidate = candidate if candidate is not None else _candidate()
        self._raises = raises
        self.seen: list[Ticket] = []

    def run(self, ticket: Ticket):
        self.seen.append(ticket)
        if self._raises is not None:
            raise self._raises
        return self._candidate


def _ticket(tid="BRE-1", labels=(LABEL_AGENT_READY, LANE_EMPIRICAL), state="Todo") -> Ticket:
    return Ticket(id=tid, title="t", body="do the thing", labels=frozenset(labels), state=state)


def _runner(tracker, agent, verifier=None, **over):
    kwargs = dict(human_allowlist=HUMANS, max_concurrent=1)
    kwargs.update(over)
    return Runner(tracker, agent, verifier or FakeVerifier(), **kwargs)  # type: ignore[arg-type]


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
        Runner(FakeTracker([]), FakeAgent(), FakeVerifier(), human_allowlist=frozenset())


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


# ---- review bandwidth --------------------------------------------------------
#
# MAP.md, day one: "throughput ceiling is human review bandwidth. Any design that
# raises agent concurrency without raising review capacity is rejected by
# default." That was prose until `max_awaiting_human`. Symphony's SPEC.md §8.3 is
# the nearest published mechanism, but it caps agents by the *source* state; the
# constraint here is on the destination, so this is an adaptation and not a port.


def test_dispatch_stalls_when_the_human_queue_is_full():
    """The founding constraint, as a number rather than a paragraph."""
    tickets = [_ticket("BRE-1"), _ticket("BRE-2", state=STATE_IN_REVIEW)]
    tracker = FakeTracker(tickets, actors={t.id: "bmr070" for t in tickets})

    result = _runner(tracker, FakeAgent(), max_concurrent=5, max_awaiting_human=1).tick()

    assert result.dispatched == []
    assert "review queue full" in result.skipped["BRE-1"]


def test_needs_human_counts_against_review_capacity_too():
    """Both states are the factory putting work in front of a person. Counting
    only in-review would let a pile of tripped breakers look like idle capacity."""
    tickets = [_ticket("BRE-1"), _ticket("BRE-2", state=STATE_NEEDS_HUMAN)]
    tracker = FakeTracker(tickets, actors={t.id: "bmr070" for t in tickets})

    result = _runner(tracker, FakeAgent(), max_concurrent=5, max_awaiting_human=1).tick()

    assert result.dispatched == []
    assert "review queue full" in result.skipped["BRE-1"]


def test_correlated_failures_stall_dispatch_without_a_separate_breaker():
    """W-08 noted Symphony has no circuit breaker: thirty tickets failing on one
    upstream break produce thirty independent retry storms.

    A review bound is a crude breaker for free. Every failure lands in
    needs-human, so the third failure exhausts the queue and the rest are never
    dispatched — the run stops instead of burning the whole backlog on the same
    fault.
    """

    class AlwaysFails:
        def run(self, ticket):
            raise RuntimeError("upstream is down")

    tickets = [_ticket(f"BRE-{i}") for i in range(1, 8)]
    tracker = FakeTracker(tickets, actors={t.id: "bmr070" for t in tickets})

    result = _runner(tracker, AlwaysFails(), max_concurrent=99, max_awaiting_human=3).tick()

    assert len(result.failed) == 3, "stopped after the queue filled, not after all seven"
    assert len(result.skipped) == 4
    assert all("review queue full" in r for r in result.skipped.values())


def test_the_bound_is_unset_by_default():
    """A default here would be a guess about one person's capacity. Unbounded
    until someone chooses, and the choice is visible in the constructor call."""
    tickets = [_ticket(f"BRE-{i}") for i in range(1, 5)]
    tracker = FakeTracker(tickets, actors={t.id: "bmr070" for t in tickets})

    result = _runner(tracker, FakeAgent(), max_concurrent=99).tick()

    assert len(result.dispatched) == 4


def test_a_zero_bound_is_refused_rather_than_silently_never_dispatching():
    with pytest.raises(ValueError, match="at least 1"):
        Runner(
            FakeTracker([]),
            FakeAgent(),
            FakeVerifier(),
            human_allowlist=HUMANS,
            max_awaiting_human=0,
        )


def test_the_tracker_is_polled_once_per_tick():
    """It used to be polled twice — once to count in-flight work and again to
    iterate — so a ticket that changed state between the calls was counted
    against one budget and dispatched against another."""
    tickets = [_ticket("BRE-1")]
    tracker = FakeTracker(tickets, actors={"BRE-1": "bmr070"})
    calls = {"n": 0}
    original = tracker.open_tickets

    def counted():
        calls["n"] += 1
        return original()

    tracker.open_tickets = counted  # type: ignore[method-assign]
    _runner(tracker, FakeAgent()).tick()

    assert calls["n"] == 1


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
    agent = FakeAgent(candidate=_candidate(overlap=9))  # leakage -> rejected
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
    text = proof_of_work(FakeVerifier().run(_candidate()))
    assert "PROMOTED" in text
    assert "`abc`" in text  # code hash
    assert "[0, 1, 2]" in text  # seeds
    assert "latency_ms" in text  # secondary metrics recorded
    assert "no_leakage" in text  # gate-by-gate


def test_proof_of_work_names_the_blocking_gates_on_rejection():
    text = proof_of_work(FakeVerifier().run(_candidate(overlap=4)))
    assert "REJECTED" in text
    assert "no_leakage" in text


# ---- GH#4: a verdict must show it was adjudicated --------------------------
#
# G-07 and G-08 only run when the verifier is built with require_prereg=True, and
# nothing forced that. The runner cannot fix it by setting the flag, because the
# runner does not build the verifier — the agent session does, and the agent is
# the untrusted party. So the check is on the artifact, not the configuration.


def test_a_verdict_without_the_prereg_gates_is_refused():
    """The hole GH#4 describes. A verdict that never faced G-07 cannot show it
    was not metric-shopped."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    result = _runner(tracker, FakeAgent(), verifier=FakeVerifier(adjudicating=False)).tick()

    assert result.dispatched == []
    assert "BRE-1" in result.refused
    assert "preregistration" in result.refused["BRE-1"]


def test_a_refused_verdict_never_reaches_the_review_queue():
    """The point of refusing at all.

    Putting an unadjudicated verdict in review launders it: the reviewer sees a
    proof-of-work block that looks like every other one, and the missing gate is
    invisible unless they think to check for an absence.
    """
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    _runner(tracker, FakeAgent(), verifier=FakeVerifier(adjudicating=False)).tick()

    states = [s for tid, s in tracker.states if tid == "BRE-1"]
    assert STATE_IN_REVIEW not in states
    assert states[-1] == STATE_NEEDS_HUMAN


def test_a_refusal_says_what_was_missing():
    """A ticket parked in needs-human with no reason is indistinguishable from
    one nobody looked at."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    _runner(tracker, FakeAgent(), verifier=FakeVerifier(adjudicating=False)).tick()

    body = "\n".join(b for tid, b in tracker.comments if tid == "BRE-1")
    assert "prereg_churn" in body and "preregistration" in body
    assert "refused" in body.lower()


def test_a_refusal_is_not_counted_as_an_agent_failure():
    """Different things to whoever reads the tick summary: `failed` is 'the agent
    broke', `refused` is 'the agent returned something we will not accept'. One
    is retryable in principle, the other is a configuration fault."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    result = _runner(tracker, FakeAgent(), verifier=FakeVerifier(adjudicating=False)).tick()

    assert result.failed == []
    assert result.refused


def test_a_properly_adjudicated_verdict_still_dispatches():
    """The check must not swallow good work."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    result = _runner(tracker, FakeAgent()).tick()

    assert result.dispatched == ["BRE-1"]
    assert result.refused == {}


def test_a_rejected_but_adjudicated_verdict_still_dispatches():
    """W-03 again: rejecting a proposed gain is a completed run. Refusal is about
    *whether the verdict was judged*, never about which way it went."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    agent = FakeAgent(candidate=_candidate(overlap=9))  # leakage -> rejected

    result = _runner(tracker, agent).tick()

    assert result.dispatched == ["BRE-1"]
    assert result.refused == {}


def test_the_empirical_lane_requires_the_gates_by_default():
    """Opt-in safety fails quietly, so the default is the safe one. Nobody has to
    remember to pass required_gates."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    runner = Runner(tracker, FakeAgent(), FakeVerifier(), human_allowlist=HUMANS)  # type: ignore[arg-type]

    assert runner._required_gates == REQUIRED_EMPIRICAL_GATES


def test_the_check_can_be_disabled_but_only_deliberately():
    """An explicit empty frozenset is easy to spot in review. The distinction
    that matters is between a chosen exemption and a default nobody chose."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    result = _runner(
        tracker, FakeAgent(), verifier=FakeVerifier(adjudicating=False), required_gates=frozenset()
    ).tick()

    assert result.dispatched == ["BRE-1"]


# ---- GH#33: the agent returns evidence, the runner adjudicates --------------


def test_the_agent_returns_a_candidate_not_a_verdict():
    """The shape change that makes the rest possible. If the agent hands over a
    verdict, every check the runner performs is on data the agent authored."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    verifier = FakeVerifier()
    _runner(tracker, FakeAgent(), verifier=verifier).tick()

    assert len(verifier.seen) == 1
    assert isinstance(verifier.seen[0], Candidate)


def test_an_agent_cannot_promote_itself_by_claiming_it_passed():
    """The point of GH#33, stated as an attack.

    A sandboxed agent that wants a promotion no longer has a channel for it. It
    can return whatever Candidate it likes; `promoted` is derived here, from
    gates it does not run, in a process it does not control.
    """

    class LyingAgent:
        """Returns evidence of a blatant leak while 'reporting' a great result."""

        def run(self, ticket):
            return _candidate(metric=0.99, overlap=500)

    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    _runner(tracker, LyingAgent()).tick()

    body = "\n".join(b for tid, b in tracker.comments if tid == "BRE-1")
    assert "REJECTED" in body
    assert "PROMOTED" not in body


def test_the_verifier_is_the_runners_not_the_agents():
    """A candidate is judged by the runner's verifier even when the agent would
    have preferred a different answer. There is no path from the agent to the
    adjudication."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    verifier = FakeVerifier()

    _runner(tracker, FakeAgent(candidate=_candidate(overlap=7)), verifier=verifier).tick()

    bundle = verifier.run(_candidate(overlap=7))
    assert not bundle.promoted and "no_leakage" in bundle.blocked_by


def test_a_candidate_that_cannot_be_adjudicated_goes_to_needs_human():
    """Distinct from a rejection. A rejected experiment was judged; this one
    could not be, and treating the two the same would file 'the verifier crashed'
    as a scientific result."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    verifier = FakeVerifier(raises=RuntimeError("gate exploded"))

    result = _runner(tracker, FakeAgent(), verifier=verifier).tick()

    assert result.dispatched == []
    assert result.failed == ["BRE-1"]
    assert [s for tid, s in tracker.states if tid == "BRE-1"][-1] == STATE_NEEDS_HUMAN
    body = "\n".join(b for tid, b in tracker.comments if tid == "BRE-1")
    assert "could not be adjudicated" in body


def test_an_agent_that_raises_is_still_handled():
    """Unchanged by the refactor, and worth re-asserting: the failure path now
    happens before adjudication rather than after."""
    tracker = FakeTracker([_ticket()], actors={"BRE-1": "bmr070"})
    result = _runner(tracker, FakeAgent(raises=RuntimeError("sandbox died"))).tick()

    assert result.failed == ["BRE-1"]
    assert [s for tid, s in tracker.states if tid == "BRE-1"][-1] == STATE_NEEDS_HUMAN


# ---- reconciliation: the lost job, and the breaker ---------------------------
#
# NEXT.md recorded this gap plainly: "Nothing calls `sweep()`, so the ticket-side
# half of 'lost job -> needs-human' is absent." `sweep` is the only thing in the
# system that can notice a lost job, so until the runner called it, a job that
# died left its ticket in progress forever — the exact failure the registry
# docstring says it exists to prevent.


class FakeJobs:
    """Stands in for JobRegistry. `test_the_real_registry_satisfies_the_protocol`
    is what keeps this fake honest."""

    def __init__(self, lost=(), breaker: str | None = None) -> None:
        self._lost = list(lost)
        self._breaker = breaker
        self.sweeps = 0

    def sweep(self):
        self.sweeps += 1
        out, self._lost = self._lost, []  # a loss is reported once, like the real one
        return out

    def breaker_reason(self):
        return self._breaker


class _Lost:
    def __init__(self, handle: str, ticket: str) -> None:
        self.handle = handle
        self.ticket = ticket


def test_a_lost_job_grounds_its_ticket_and_says_why():
    tracker = FakeTracker([_ticket("BRE-1")], actors={"BRE-1": "bmr070"})
    jobs = FakeJobs(lost=[_Lost("job-7", "BRE-1")])

    result = _runner(tracker, FakeAgent(), jobs=jobs).tick()

    assert result.lost == {"BRE-1": "job-7"}
    assert ("BRE-1", STATE_NEEDS_HUMAN) in tracker.states
    body = tracker.comments[0][1]
    assert "job-7" in body and "not retried" in body.lower()


def test_a_lost_job_is_never_auto_retried():
    """The registry is explicit: a job whose state is unknown may still be
    running and still burning budget, so resubmitting can double-spend."""
    tracker = FakeTracker([_ticket("BRE-1")], actors={"BRE-1": "bmr070"})
    agent = FakeAgent()

    _runner(tracker, agent, jobs=FakeJobs(lost=[_Lost("job-7", "BRE-1")])).tick()

    assert agent.seen == [], "the agent was re-dispatched against a lost job"


def test_a_ticket_grounded_this_tick_is_not_also_dispatched():
    """The tracker is polled once per tick, so a ticket grounded by the sweep
    still looks dispatchable in the list read before it. Without this the same
    tick would move a ticket to needs-human and then hand it to an agent."""
    tracker = FakeTracker([_ticket("BRE-1")], actors={"BRE-1": "bmr070"})
    agent = FakeAgent()

    result = _runner(tracker, agent, jobs=FakeJobs(lost=[_Lost("j", "BRE-1")])).tick()

    assert result.dispatched == []
    assert agent.seen == []
    assert "grounded this tick" in result.skipped["BRE-1"]


def test_an_open_breaker_halts_dispatch():
    """The registry already refuses job *submission* while the breaker is open.
    That alone costs one full agent session per ticket to discover the halt, and
    costs nothing at all on a lane that submits no jobs.

    A breaker only the spender consults is not a breaker.
    """
    tickets = [_ticket("BRE-1"), _ticket("BRE-2")]
    tracker = FakeTracker(tickets, actors={t.id: "bmr070" for t in tickets})
    agent = FakeAgent()

    result = _runner(
        tracker, agent, max_concurrent=9, jobs=FakeJobs(breaker="job-3 vanished")
    ).tick()

    assert result.dispatched == []
    assert agent.seen == []
    assert all("compute breaker open" in r for r in result.skipped.values())
    assert "job-3 vanished" in result.skipped["BRE-1"]


def test_an_ineligible_ticket_reports_its_own_reason_not_the_breaker():
    """A ticket that was never dispatchable should say why. Attributing it to a
    breaker it never reached would send a reader to the wrong problem."""
    tracker = FakeTracker([_ticket("BRE-1", labels=(LANE_EMPIRICAL,))], actors={"BRE-1": "bmr070"})

    result = _runner(tracker, FakeAgent(), jobs=FakeJobs(breaker="unrelated")).tick()

    assert result.skipped["BRE-1"] == "not agent-ready"


def test_without_a_registry_nothing_sweeps_and_dispatch_still_works():
    """The deterministic lane submits no GPU work. Optional, but the docstring
    says plainly what its absence costs on the empirical lane."""
    tracker = FakeTracker([_ticket("BRE-1")], actors={"BRE-1": "bmr070"})
    result = _runner(tracker, FakeAgent()).tick()

    assert result.dispatched == ["BRE-1"]
    assert result.lost == {}


def test_the_sweep_runs_even_when_every_ticket_is_ineligible():
    """Reconciliation is not a step inside dispatch. A board where nothing is
    dispatchable is exactly when a lost job would otherwise sit unnoticed."""
    tracker = FakeTracker([_ticket("BRE-9", labels=())], actors={})
    jobs = FakeJobs(lost=[_Lost("job-1", "BRE-9")])

    result = _runner(tracker, FakeAgent(), jobs=jobs).tick()

    assert jobs.sweeps == 1
    assert result.lost == {"BRE-9": "job-1"}


def test_the_real_registry_satisfies_the_protocol():
    """The load-bearing test for the seam. `JobLedger` is a structural protocol,
    so nothing checks it against the real class unless something asks — and a
    protocol the real registry does not satisfy is worse than no protocol, since
    every test above would keep passing against the fake.
    """
    from expfactory.registry import JobRecord, JobRegistry, JobState

    # `JobLedger` is all methods, so the class check works directly.
    assert issubclass(JobRegistry, JobLedger)

    # `LostJob` has property members, which `issubclass` cannot see — only
    # `isinstance` can. Checking an instance is the stronger check anyway: it is
    # what `_reconcile_lost` actually receives.
    record = JobRecord(
        handle="h",
        ticket="BRE-1",
        submitted_at=0.0,
        deadline_at=1.0,
        cost_estimate_usd=0.01,
        state=JobState.SUBMITTED,
    )
    assert isinstance(record, LostJob)
    assert (record.handle, record.ticket) == ("h", "BRE-1")


def test_a_job_lost_this_tick_counts_against_review_capacity():
    """The two controls interact, and the tracker is read once per tick.

    A ticket the sweep just moved to needs-human still looks idle in the list
    read before it. Counting review capacity from that stale list would let the
    runner hand a human one more ticket than the bound allows, every tick that
    also lost a job.
    """
    tickets = [_ticket("BRE-1"), _ticket("BRE-2")]
    tracker = FakeTracker(tickets, actors={t.id: "bmr070" for t in tickets})
    jobs = FakeJobs(lost=[_Lost("job-1", "BRE-2")])

    result = _runner(tracker, FakeAgent(), max_concurrent=9, max_awaiting_human=1, jobs=jobs).tick()

    assert result.lost == {"BRE-2": "job-1"}
    assert result.dispatched == [], "BRE-1 filled the human's queue on top of the lost job"
    assert "review queue full" in result.skipped["BRE-1"]
