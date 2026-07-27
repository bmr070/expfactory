"""
The Linear adapter — the work queue the runner actually reads.

W-07's amendment made Linear the queue and removed the GitHub Issues mirror, so
this is the `Tracker` that matters. The tests that matter are the refusals and
the attribution, not the happy path.

The attribution one is the point of the whole adapter: Linear reports `actor` and
`botActor` as **distinct fields**, so "a bot did this" is a type in the schema
rather than a naming convention. GitHub answers the same question by matching
login strings, which is a comparison against a namespace an attacker partly
controls.

Driven through a fake transport rather than mocks of this class, so the tests
exercise the real query/response handling.
"""

from __future__ import annotations

from typing import Any

import pytest

from expfactory.linear_tracker import (
    GraphQLTransport,
    LinearApiError,
    LinearTracker,
    StateWriteRefused,
)
from expfactory.runner import STATE_IN_PROGRESS, STATE_IN_REVIEW, STATE_NEEDS_HUMAN, Tracker

TEAM = "BRE"
ISSUE = "uuid-1111"


class FakeGraphQL:
    """Answers by matching a distinctive fragment of each document."""

    def __init__(self, **overrides: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._overrides = overrides

    def query(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((document, variables))
        for key, value in self._overrides.items():
            if key in document:
                return value if isinstance(value, dict) else {"data": value}
        return {"data": {}}


def _issues_payload(labels: list[str], state: str = "Todo") -> dict[str, Any]:
    return {
        "data": {
            "issues": {
                "nodes": [
                    {
                        "id": ISSUE,
                        "identifier": "BRE-1",
                        "title": "do the thing",
                        "description": "body text",
                        "state": {"name": state},
                        "labels": {"nodes": [{"name": n} for n in labels]},
                    }
                ]
            }
        }
    }


def _history(actor: str | None = None, bot: str | None = None, label_id: str = "lab-1"):
    entry: dict[str, Any] = {"addedLabelIds": [label_id]}
    if actor:
        entry["actor"] = {"name": actor, "displayName": actor}
    if bot:
        entry["botActor"] = {"name": bot}
    return {"data": {"issue": {"history": {"nodes": [entry]}}}}


_LABELS_OK = {"data": {"issue": {"labels": {"nodes": [{"id": "lab-1", "name": "agent-ready"}]}}}}
_STATES_OK = {
    "data": {
        "workflowStates": {
            "nodes": [
                {"id": "st-1", "name": STATE_IN_PROGRESS},
                {"id": "st-2", "name": STATE_IN_REVIEW},
                {"id": "st-3", "name": STATE_NEEDS_HUMAN},
            ]
        }
    }
}


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #


def test_it_satisfies_the_tracker_protocol():
    assert isinstance(LinearTracker(TEAM, FakeGraphQL()), Tracker)


def test_the_transport_is_the_only_thing_that_holds_a_credential():
    """Injected so the token stays outside this class and every path is testable
    without a network or an account."""
    assert isinstance(FakeGraphQL(), GraphQLTransport)


# --------------------------------------------------------------------------- #
# Attribution — the reason this adapter exists
# --------------------------------------------------------------------------- #


def test_a_human_who_applied_the_label_is_named():
    transport = FakeGraphQL(labels=_LABELS_OK, history=_history(actor="Brett R"))
    tracker = LinearTracker(TEAM, transport)

    assert tracker.label_actor(ISSUE, "agent-ready") == "Brett R"


def test_a_bot_actor_returns_none_rather_than_a_name():
    """The point of preferring Linear here.

    `botActor` is its own field, so this is a schema-level distinction. Returning
    the bot's *name* would push the decision onto the runner, which would then
    have to recognise whether a name looks bot-shaped — exactly the string
    comparison that races on GitHub.
    """
    transport = FakeGraphQL(labels=_LABELS_OK, history=_history(bot="expfactory-agent"))
    tracker = LinearTracker(TEAM, transport)

    assert tracker.label_actor(ISSUE, "agent-ready") is None


def test_a_label_the_issue_does_not_carry_is_unattributable():
    transport = FakeGraphQL(labels={"data": {"issue": {"labels": {"nodes": []}}}})
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


def test_a_label_present_but_absent_from_history_is_unattributable():
    """Present with no record of who added it is not a yes."""
    transport = FakeGraphQL(
        labels=_LABELS_OK, history={"data": {"issue": {"history": {"nodes": []}}}}
    )
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


def test_the_most_recent_application_wins():
    """A label removed and re-applied is attributed to whoever put it back.

    Also the attack case: a human applies it, a bot re-applies it, and reading
    the *first* matching entry would credit the human for the bot's action.
    """
    history = {
        "data": {
            "issue": {
                "history": {
                    "nodes": [
                        {"addedLabelIds": ["lab-1"], "actor": {"displayName": "first"}},
                        {"addedLabelIds": ["lab-1"], "botActor": {"name": "a-bot"}},
                    ]
                }
            }
        }
    }
    transport = FakeGraphQL(labels=_LABELS_OK, history=history)
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_open_tickets_carry_labels_and_state():
    transport = FakeGraphQL(issues=_issues_payload(["agent-ready", "lane:empirical"]))
    (ticket,) = LinearTracker(TEAM, transport).open_tickets()

    assert ticket.id == ISSUE
    assert "agent-ready" in ticket.labels
    assert ticket.body == "body text"
    assert "BRE-1" in ticket.title


def test_the_ticket_id_is_the_uuid_not_the_identifier():
    """Mutations key on the UUID. Using the human identifier for one call and the
    UUID for another silently updates nothing."""
    transport = FakeGraphQL(issues=_issues_payload([]))
    (ticket,) = LinearTracker(TEAM, transport).open_tickets()

    assert ticket.id == ISSUE
    assert ticket.id != "BRE-1"


def test_an_issue_with_no_description_reads_as_empty_not_none():
    payload = _issues_payload([])
    payload["data"]["issues"]["nodes"][0]["description"] = None
    (ticket,) = LinearTracker(TEAM, FakeGraphQL(issues=payload)).open_tickets()

    assert ticket.body == ""


def test_api_errors_are_raised_not_returned_as_an_empty_queue():
    """ "No tickets" and "the query failed" must not look the same to a poller:
    one is an idle tick, the other is an outage."""
    transport = FakeGraphQL(issues={"errors": [{"message": "unauthorized"}]})
    with pytest.raises(LinearApiError, match="unauthorized"):
        LinearTracker(TEAM, transport).open_tickets()


def test_a_response_with_no_data_is_an_error():
    with pytest.raises(LinearApiError):
        LinearTracker(TEAM, FakeGraphQL(issues={"data": None})).open_tickets()


# --------------------------------------------------------------------------- #
# Writes — what the adapter may not do
# --------------------------------------------------------------------------- #


def test_the_three_workflow_states_can_be_set():
    for state in (STATE_IN_PROGRESS, STATE_IN_REVIEW, STATE_NEEDS_HUMAN):
        transport = FakeGraphQL(workflowStates=_STATES_OK, issueUpdate={"data": {"ok": True}})
        LinearTracker(TEAM, transport).set_state(ISSUE, state)


def test_the_adapter_refuses_to_move_a_ticket_to_done():
    """The runner does not approve its own work. A tracker that can close a
    ticket is one override away from doing so."""
    transport = FakeGraphQL(workflowStates=_STATES_OK)
    with pytest.raises(StateWriteRefused, match="does not approve its own work"):
        LinearTracker(TEAM, transport).set_state(ISSUE, "Done")


def test_an_arbitrary_state_is_refused():
    transport = FakeGraphQL(workflowStates=_STATES_OK)
    with pytest.raises(StateWriteRefused):
        LinearTracker(TEAM, transport).set_state(ISSUE, "Backlog")


def test_a_missing_workflow_state_is_a_loud_failure():
    """Silently failing to move tickets would leave the runner believing work is
    in progress while the board says Todo."""
    transport = FakeGraphQL(workflowStates={"data": {"workflowStates": {"nodes": []}}})
    with pytest.raises(StateWriteRefused, match="no workflow state"):
        LinearTracker(TEAM, transport).set_state(ISSUE, STATE_IN_PROGRESS)


def test_the_adapter_never_writes_a_label():
    """`agent-ready` is the human's channel for granting dispatch rights. An
    adapter that can write it has removed the human from the loop invariant 7
    exists to keep them in.

    Asserted against the source: Linear models workflow position as state rather
    than as a label, so this adapter has no reason to touch labels at all, and
    the absence of any label mutation is the cleanest form of the guarantee.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "src" / "expfactory" / "linear_tracker.py"
    ).read_text(encoding="utf-8")

    for mutation in ("issueAddLabel", "issueRemoveLabel", "labelCreate", "issueLabelCreate"):
        assert mutation not in source, f"adapter can write labels via {mutation}"


def test_workflow_states_are_fetched_once_and_reused():
    """A state lookup per transition would triple the API calls a tick makes, and
    the mapping does not change during a run."""
    transport = FakeGraphQL(workflowStates=_STATES_OK, issueUpdate={"data": {"ok": True}})
    tracker = LinearTracker(TEAM, transport)

    tracker.set_state(ISSUE, STATE_IN_PROGRESS)
    tracker.set_state(ISSUE, STATE_IN_REVIEW)

    state_queries = [d for d, _ in transport.calls if "workflowStates" in d]
    assert len(state_queries) == 1


def test_a_comment_is_posted_against_the_uuid():
    transport = FakeGraphQL(commentCreate={"data": {"commentCreate": {"success": True}}})
    LinearTracker(TEAM, transport).comment(ISSUE, "proof of work")

    (_, variables) = next((d, v) for d, v in transport.calls if "commentCreate" in d)
    assert variables["issueId"] == ISSUE
    assert variables["body"] == "proof of work"
