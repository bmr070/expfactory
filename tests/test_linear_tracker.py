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
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    GraphQLTransport,
    LinearApiError,
    LinearTracker,
    PageWalkRefused,
    StateWriteRefused,
    UnorderableEntry,
)
from expfactory.runner import (
    STATE_IN_PROGRESS,
    STATE_IN_REVIEW,
    STATE_NEEDS_HUMAN,
    STATE_RUNNING_UNATTENDED,
    Tracker,
)

TEAM = "BRE"
ISSUE = "uuid-1111"

# Which document is which, by a fragment that appears in exactly one of them.
# Matching on the bare word would be wrong: the issues query selects `labels`
# too, so `"labels" in document` answers the labels fixture where the issues one
# was asked for — a fake that quietly serves the wrong response is worse than no
# fake, because every test above it still passes.
_DOCUMENTS = (
    ("issues", "issues("),
    ("history", "history("),
    ("labels", "labels(first: $first"),
    ("workflowStates", "workflowStates("),
    ("commentCreate", "commentCreate"),
    ("issueUpdate", "issueUpdate"),
)


def _which(document: str) -> str | None:
    return next((name for name, fragment in _DOCUMENTS if fragment in document), None)


class FakeGraphQL:
    """Answers each known document from a canned payload.

    An override may be a single payload, or a list of payloads served to
    successive calls against the same document — which is how a multi-page walk
    is driven without a network.
    """

    def __init__(self, **overrides: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._overrides = overrides

    def query(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((document, variables))
        name = _which(document)
        if name is None or name not in self._overrides:
            return {"data": {}}
        value = self._overrides[name]
        if isinstance(value, list):
            served = sum(1 for d, _ in self.calls if _which(d) == name) - 1
            return value[min(served, len(value) - 1)]
        return value if isinstance(value, dict) else {"data": value}

    def variables_for(self, name: str) -> list[dict[str, Any]]:
        return [v for d, v in self.calls if _which(d) == name]


def _conn(nodes: list[Any], *, next_cursor: str | None = None) -> dict[str, Any]:
    """A GraphQL connection envelope. `next_cursor` makes it claim another page."""
    return {
        "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
        "nodes": nodes,
    }


def _issue_node(
    issue_id: str = ISSUE,
    identifier: str = "BRE-1",
    labels: tuple[str, ...] = (),
    state: str = "Todo",
    created_at: str = "2026-07-01T00:00:00Z",
    **over: Any,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": issue_id,
        "identifier": identifier,
        "title": "do the thing",
        "description": "body text",
        "createdAt": created_at,
        "state": {"name": state},
        "labels": _conn([{"name": n} for n in labels]),
    }
    node.update(over)
    return node


def _issues_payload(labels: list[str], state: str = "Todo") -> dict[str, Any]:
    return {"data": {"issues": _conn([_issue_node(labels=tuple(labels), state=state)])}}


def _entry(
    actor: str | None = None,
    bot: str | None = None,
    label_id: str = "lab-1",
    at: str = "2026-07-01T00:00:00Z",
    display: str = "Brett R",
) -> dict[str, Any]:
    """`actor` is the account **id**, which is what `label_actor` returns.

    `display` is deliberately a *different* string and defaults to the same value
    for every actor. Any test that passes by matching a display name is therefore
    a test that would pass for an impostor (BRE-42), and this helper is the reason
    such a test cannot be written by accident.
    """
    entry: dict[str, Any] = {"addedLabelIds": [label_id], "createdAt": at}
    if actor:
        entry["actor"] = {"id": actor, "name": display, "displayName": display}
    if bot:
        entry["botActor"] = {"name": bot}
    return entry


def _history(actor: str | None = None, bot: str | None = None, label_id: str = "lab-1"):
    return {"data": {"issue": {"history": _conn([_entry(actor, bot, label_id)])}}}


_LABELS_OK = {"data": {"issue": {"labels": _conn([{"id": "lab-1", "name": "agent-ready"}])}}}
_STATES_OK = {
    "data": {
        "workflowStates": _conn(
            [
                {"id": "st-1", "name": STATE_IN_PROGRESS},
                {"id": "st-2", "name": STATE_IN_REVIEW},
                {"id": "st-3", "name": STATE_NEEDS_HUMAN},
                {"id": "st-4", "name": STATE_RUNNING_UNATTENDED},
            ]
        )
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
    transport = FakeGraphQL(labels=_LABELS_OK, history=_history(actor="usr-brett"))
    tracker = LinearTracker(TEAM, transport)

    assert tracker.label_actor(ISSUE, "agent-ready") == "usr-brett"


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
    transport = FakeGraphQL(labels=_LABELS_OK, history={"data": {"issue": {"history": _conn([])}}})
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


def test_the_most_recent_application_wins():
    """A label removed and re-applied is attributed to whoever put it back.

    Also the attack case: a human applies it, a bot re-applies it, and reading
    the *first* matching entry would credit the human for the bot's action.
    """
    history = {
        "data": {
            "issue": {
                "history": _conn(
                    [
                        _entry(actor="first", at="2026-07-01T00:00:00Z"),
                        _entry(bot="a-bot", at="2026-07-02T00:00:00Z"),
                    ]
                )
            }
        }
    }
    transport = FakeGraphQL(labels=_LABELS_OK, history=history)
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


# --------------------------------------------------------------------------- #
# Pagination — the second page is where the answer changes
# --------------------------------------------------------------------------- #
#
# BRE-32 defect 3, and it was measured rather than imagined: `issues(first: 3)`
# returned `hasNextPage: true` against the live API on a workspace with one team
# and about thirty tickets. These fixtures are not loop mechanics. A history read
# one page deep answers invariant 7 from a prefix, and a prefix that ends before
# a bot's re-application still contains an earlier human's — so it does not
# merely lose information, it answers *yes* to a grant that was superseded.


def test_a_history_entry_on_the_second_page_is_found():
    """The whole point of the walk, in one assertion.

    Page one holds only the human's application, so a one-page read returns
    "Brett R" and the runner dispatches. Page two holds the bot that re-applied
    it afterwards. Same board, same token, opposite decision.
    """
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history=[
            {
                "data": {
                    "issue": {
                        "history": _conn(
                            [_entry(actor="usr-brett", at="2026-07-01T00:00:00Z")],
                            next_cursor="cur-2",
                        )
                    }
                }
            },
            {
                "data": {
                    "issue": {
                        "history": _conn(
                            [_entry(bot="expfactory-agent", at="2026-07-02T00:00:00Z")]
                        )
                    }
                }
            },
        ],
    )

    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None
    assert len(transport.variables_for("history")) == 2, "the second page was never fetched"


def test_the_truncated_read_would_have_said_something_else():
    """The positive control. Without it, a walk that returned only the last page
    would satisfy the test above just as well."""
    transport = FakeGraphQL(labels=_LABELS_OK, history=_history(actor="usr-brett"))
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") == "usr-brett"


def test_the_cursor_from_one_page_is_sent_with_the_next():
    """A walk that re-requested page one forever, or dropped the cursor and
    re-read the same page, would still terminate — with the wrong answer."""
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history=[
            {"data": {"issue": {"history": _conn([_entry(actor="a")], next_cursor="cur-2")}}},
            {"data": {"issue": {"history": _conn([])}}},
        ],
    )
    LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready")

    first, second = transport.variables_for("history")
    assert first["after"] is None
    assert second["after"] == "cur-2"


def test_a_failure_mid_walk_raises_rather_than_returning_the_prefix():
    """A prefix is indistinguishable from a complete history to every caller
    above. Returning what arrived would report the *human* grant that the unread
    page had already superseded."""

    class BreaksOnPageTwo(FakeGraphQL):
        def query(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
            if _which(document) == "history" and variables.get("after"):
                raise PermissionError("401 while paginating")
            return super().query(document, variables)

    transport = BreaksOnPageTwo(
        labels=_LABELS_OK,
        history={"data": {"issue": {"history": _conn([_entry(actor="a")], next_cursor="cur-2")}}},
    )

    # The transport's own exception type, not an adapter-flavoured one: whoever
    # is above needs to tell an auth failure from a rate limit to know whether a
    # human or a backoff is the right response.
    with pytest.raises(PermissionError):
        LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready")


def test_an_api_error_mid_walk_is_also_all_or_nothing():
    """Same property through the adapter's own error path. `LinearApiError` on
    page two must not resolve into "here is page one"."""
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history=[
            {"data": {"issue": {"history": _conn([_entry(actor="a")], next_cursor="cur-2")}}},
            {"errors": [{"message": "rate limited"}]},
        ],
    )
    with pytest.raises(LinearApiError, match="rate limited"):
        LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready")


def test_a_cursor_that_does_not_advance_is_refused():
    """A repeated cursor cannot make progress, and whatever lies past it is
    unreachable — a truncated read by another name, so it gets the same answer.
    The alternative is a poll that never returns."""
    stuck = {"data": {"issues": _conn([_issue_node()], next_cursor="same")}}
    transport = FakeGraphQL(issues=stuck)

    with pytest.raises(PageWalkRefused, match="looped"):
        LinearTracker(TEAM, transport).open_tickets()

    assert len(transport.variables_for("issues")) == 2, "it kept going after the repeat"


def test_hasnextpage_without_an_endcursor_is_refused():
    """A "there is more" with no "here is where it continues" cannot be crossed.
    One without the other is a page boundary that cannot be crossed."""
    transport = FakeGraphQL(
        issues={
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": True, "endCursor": None},
                    "nodes": [_issue_node()],
                }
            }
        }
    )
    with pytest.raises(PageWalkRefused, match="endCursor"):
        LinearTracker(TEAM, transport).open_tickets()


def test_a_connection_that_never_ends_is_bounded():
    """Distinct cursors forever is not something the seen-set can catch. Bounded
    so a server that always says "one more" cannot hold the runner's tick open."""

    class Endless(FakeGraphQL):
        def query(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((document, variables))
            n = len(self.calls)
            return {"data": {"issues": _conn([], next_cursor=f"cur-{n}")}}

    transport = Endless()
    with pytest.raises(PageWalkRefused, match="did not terminate"):
        LinearTracker(TEAM, transport).open_tickets()
    assert len(transport.calls) < 500, "the bound did not bound anything"


def test_a_response_with_no_pageinfo_is_taken_at_its_word():
    """There is no third answer available. Assuming truncation whenever the field
    is absent would make every read fail against a server that omits it."""
    transport = FakeGraphQL(issues={"data": {"issues": {"nodes": [_issue_node()]}}})
    assert len(LinearTracker(TEAM, transport).open_tickets()) == 1


def test_every_connection_is_walked_not_just_the_issue_list():
    """Labels and workflow states are connections too, and each truncates into a
    different wrong answer: an unattributable label, and a state the adapter
    reports the team does not have."""
    transport = FakeGraphQL(
        labels=[
            {"data": {"issue": {"labels": _conn([{"id": "x", "name": "other"}], next_cursor="c")}}},
            {"data": {"issue": {"labels": _conn([{"id": "lab-1", "name": "agent-ready"}])}}},
        ],
        history=_history(actor="usr-brett"),
    )
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") == "usr-brett"


def test_a_workflow_state_on_the_second_page_is_reachable():
    """Truncation here turns the "no such state" refusal into a lie: the state
    exists and simply arrived later."""
    transport = FakeGraphQL(
        workflowStates=[
            {"data": {"workflowStates": _conn([{"id": "st-1", "name": "Todo"}], next_cursor="c")}},
            {"data": {"workflowStates": _conn([{"id": "st-9", "name": STATE_RUNNING_UNATTENDED}])}},
        ],
        issueUpdate={"data": {"ok": True}},
    )
    LinearTracker(TEAM, transport).set_state(ISSUE, STATE_RUNNING_UNATTENDED)

    (variables,) = transport.variables_for("issueUpdate")
    assert variables["stateId"] == "st-9"


def test_a_truncated_label_set_on_an_issue_is_refused():
    """The nested label connection is requested at 100 and not walked, so a
    truncation there is refused rather than paged. Eligibility is decided from
    these labels, and losing `needs-human` from the set reads as dispatchable —
    the failure is not "incomplete", it is "open"."""
    node = _issue_node(labels=("agent-ready",))
    node["labels"]["pageInfo"] = {"hasNextPage": True}
    transport = FakeGraphQL(issues={"data": {"issues": _conn([node])}})

    with pytest.raises(PageWalkRefused, match="needs-human"):
        LinearTracker(TEAM, transport).open_tickets()


def test_a_page_size_outside_linears_range_is_refused_at_construction():
    for bad in (0, MAX_PAGE_SIZE + 1):
        with pytest.raises(ValueError, match="page_size"):
            LinearTracker(TEAM, FakeGraphQL(), page_size=bad)


def test_the_default_page_size_is_linears_own():
    transport = FakeGraphQL(issues=_issues_payload([]))
    LinearTracker(TEAM, transport).open_tickets()

    assert transport.variables_for("issues")[0]["first"] == DEFAULT_PAGE_SIZE


# --------------------------------------------------------------------------- #
# The ordering policy
# --------------------------------------------------------------------------- #


def test_history_arriving_in_the_wrong_order_is_corrected():
    """Linear orders connections by `updatedAt` descending by default and
    `orderBy` takes no direction, so arrival order is never the one this adapter
    needs. Reading the list backwards only worked while the server happened to
    agree — and reversing two applications reverses the dispatch decision.
    """
    history = {
        "data": {
            "issue": {
                "history": _conn(
                    [
                        # The later entry arrives first. Server order is wrong.
                        _entry(bot="a-bot", at="2026-07-09T00:00:00Z"),
                        _entry(actor="usr-brett", at="2026-07-02T00:00:00Z"),
                    ]
                )
            }
        }
    }
    transport = FakeGraphQL(labels=_LABELS_OK, history=history)
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


def test_ordering_is_established_across_a_page_boundary_too():
    """Sorting each page as it arrives would still concatenate them in server
    order. The sort runs once, over everything the walk collected."""
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history=[
            {
                "data": {
                    "issue": {
                        "history": _conn(
                            [_entry(bot="a-bot", at="2026-07-09T00:00:00Z")], next_cursor="c"
                        )
                    }
                }
            },
            {
                "data": {
                    "issue": {
                        "history": _conn([_entry(actor="usr-brett", at="2026-07-02T00:00:00Z")])
                    }
                }
            },
        ],
    )
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


def test_tickets_come_back_oldest_first():
    """Which ticket gets the runner's last unit of concurrency should be a policy
    rather than a coincidence of server ordering."""
    transport = FakeGraphQL(
        issues={
            "data": {
                "issues": _conn(
                    [
                        _issue_node("u-3", "BRE-3", created_at="2026-07-03T00:00:00Z"),
                        _issue_node("u-1", "BRE-1", created_at="2026-07-01T00:00:00Z"),
                        _issue_node("u-2", "BRE-2", created_at="2026-07-02T00:00:00Z"),
                    ]
                )
            }
        }
    )
    assert [t.id for t in LinearTracker(TEAM, transport).open_tickets()] == ["u-1", "u-2", "u-3"]


def test_a_history_entry_with_no_timestamp_is_refused_not_placed():
    """It cannot be ordered against the others, and a guess about where it
    belongs is a guess about who granted dispatch."""
    entry = _entry(actor="usr-brett")
    del entry["createdAt"]
    transport = FakeGraphQL(
        labels=_LABELS_OK, history={"data": {"issue": {"history": _conn([entry])}}}
    )
    with pytest.raises(UnorderableEntry, match="createdAt"):
        LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready")


def test_an_unrelated_history_entry_missing_a_timestamp_does_not_refuse_the_check():
    """Narrow first, order second. Only entries that added *this* label decide
    the answer, so an unrelated malformed one must not stop the runner reading a
    board."""
    other = {"addedLabelIds": ["lab-99"]}
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={"data": {"issue": {"history": _conn([other, _entry(actor="usr-brett")])}}},
    )
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") == "usr-brett"


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


def test_the_workflow_states_can_be_set():
    for state in (
        STATE_IN_PROGRESS,
        STATE_IN_REVIEW,
        STATE_NEEDS_HUMAN,
        STATE_RUNNING_UNATTENDED,
    ):
        transport = FakeGraphQL(workflowStates=_STATES_OK, issueUpdate={"data": {"ok": True}})
        LinearTracker(TEAM, transport).set_state(ISSUE, state)


def test_the_unattended_state_is_writable():
    """BRE-32 defect 2. This allowlist had three entries, so `_detach` raised
    `StateWriteRefused` — after the GPU job it was parking had already started.

    It gained one named state. Note what did not happen: the allowlist was not
    widened toward "whatever the runner asks for", and this adapter still
    mutates no labels at all, so `agent-ready` stays unwritable by construction
    rather than by exclusion.
    """
    tracker = LinearTracker(TEAM, FakeGraphQL(workflowStates=_STATES_OK))

    assert STATE_RUNNING_UNATTENDED in tracker.writable_states()
    assert "Done" not in tracker.writable_states()
    assert "agent-ready" not in tracker.writable_states()


def test_writable_states_omits_a_state_the_team_does_not_have():
    """BRE-42. This method's whole job is to move a wiring error before dispatch,
    and it was answering from the allowlist constant alone — a question about
    this file, when its docstring promised one about the workspace.

    So a team without a `Running Unattended` column passed the pre-dispatch check
    and then refused the park, which is the failure the method exists to prevent,
    arriving at exactly the moment it was meant to be prevented from arriving.
    Two of the four states are not Linear defaults, so this is the ordinary case
    for a fresh team.
    """
    partial = {
        "data": {
            "workflowStates": _conn(
                [
                    {"id": "st-1", "name": STATE_IN_PROGRESS},
                    {"id": "st-2", "name": STATE_IN_REVIEW},
                ]
            )
        }
    }
    writable = LinearTracker(TEAM, FakeGraphQL(workflowStates=partial)).writable_states()

    assert STATE_IN_PROGRESS in writable
    assert STATE_RUNNING_UNATTENDED not in writable
    assert STATE_NEEDS_HUMAN not in writable


def test_writable_states_refuses_rather_than_reporting_a_short_list():
    """A walk that cannot complete must not answer. A truncated state list is
    indistinguishable from "the team lacks that column", and that reading sends
    the runner down the same wrong branch by a quieter route."""
    with pytest.raises(PageWalkRefused):
        LinearTracker(TEAM, FakeGraphQL()).writable_states()


def test_writable_states_costs_one_query_however_often_it_is_asked():
    """Shared cache with `_state_id`. Pre-dispatch is a hot path and the answer
    changes only when a human edits the team's board."""
    transport = FakeGraphQL(workflowStates=_STATES_OK, issueUpdate={"data": {"ok": True}})
    tracker = LinearTracker(TEAM, transport)

    tracker.writable_states()
    tracker.writable_states()
    tracker.set_state(ISSUE, STATE_IN_PROGRESS)

    assert len(transport.variables_for("workflowStates")) == 1


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


# --------------------------------------------------------------------------- #
# BRE-42 — identity is an id, and order is an instant
# --------------------------------------------------------------------------- #


def test_the_actor_is_reported_by_id_not_by_display_name():
    """`displayName` and `name` are both self-editable by any workspace member.

    Matching the runner's allowlist on either meant anyone who could file a
    ticket could rename themselves into the allowlist and grant their own
    dispatch — invariant 7 defeated by a settings page. `User.id` is a
    server-assigned UUID nothing in the product can change.
    """
    impostor = _entry(actor="usr-impostor", display="Brett R")
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={"data": {"issue": {"history": _conn([impostor])}}},
    )

    actor = LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready")

    assert actor == "usr-impostor"
    assert actor != "Brett R"


def test_an_actor_with_no_id_is_unattributable():
    """No id is no identity. Falling back to the display name would reintroduce
    the impostor path through the error case."""
    nameless = {
        "addedLabelIds": ["lab-1"],
        "createdAt": "2026-07-01T00:00:00Z",
        "actor": {"name": "Brett R", "displayName": "Brett R"},
    }
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={"data": {"issue": {"history": _conn([nameless])}}},
    )
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


def test_two_applications_at_the_same_instant_by_different_parties_are_refused():
    """Python's sort is stable, so a tie fell back to the order the server sent
    — the order this adapter's own docstring says it cannot trust."""
    same = "2026-07-01T00:00:03.000Z"
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={
            "data": {
                "issue": {
                    "history": _conn(
                        [
                            _entry(actor="usr-brett", at=same),
                            _entry(bot="expfactory-agent", at=same),
                        ]
                    )
                }
            }
        },
    )
    with pytest.raises(UnorderableEntry, match="share the"):
        LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready")


def test_the_same_party_twice_at_one_instant_is_not_a_tie():
    """Only ambiguous when the parties differ. One account re-adding twice in a
    millisecond answers the same either way, and refusing it would be friction
    with no safety in it."""
    same = "2026-07-01T00:00:03.000Z"
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={
            "data": {
                "issue": {
                    "history": _conn(
                        [_entry(actor="usr-brett", at=same), _entry(actor="usr-brett", at=same)]
                    )
                }
            }
        },
    )
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") == "usr-brett"


def test_mixed_precision_timestamps_order_by_instant_not_text():
    """`.` (0x2E) sorts below `Z` (0x5A), so a lexical compare reads
    `...03.500Z` as *earlier* than `...03Z` and names the superseded grant."""
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={
            "data": {
                "issue": {
                    "history": _conn(
                        [
                            _entry(bot="expfactory-agent", at="2026-07-01T00:00:03Z"),
                            _entry(actor="usr-brett", at="2026-07-01T00:00:03.500Z"),
                        ]
                    )
                }
            }
        },
    )
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") == "usr-brett"


def test_an_offset_timestamp_orders_by_instant_not_wall_clock():
    """`-04:00` is four hours *ahead* of the same wall clock in UTC. Compared as
    text it sorts as if it were behind."""
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={
            "data": {
                "issue": {
                    "history": _conn(
                        [
                            # 12:00Z
                            _entry(actor="usr-brett", at="2026-07-01T12:00:00Z"),
                            # 15:00Z, but sorts below '12:00:00Z' as text
                            _entry(bot="expfactory-agent", at="2026-07-01T11:00:00-04:00"),
                        ]
                    )
                }
            }
        },
    )
    assert LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready") is None


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45T99:99:99Z"])
def test_an_unparseable_timestamp_is_refused(bad: str):
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={"data": {"issue": {"history": _conn([_entry(actor="usr-brett", at=bad)])}}},
    )
    with pytest.raises(UnorderableEntry):
        LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready")


def test_a_naive_timestamp_is_refused():
    """No timezone names a wall clock, not an instant."""
    transport = FakeGraphQL(
        labels=_LABELS_OK,
        history={
            "data": {
                "issue": {"history": _conn([_entry(actor="usr-brett", at="2026-07-01T00:00:00")])}
            }
        },
    )
    with pytest.raises(UnorderableEntry, match="timezone"):
        LinearTracker(TEAM, transport).label_actor(ISSUE, "agent-ready")
