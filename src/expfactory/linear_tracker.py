"""
linear_tracker — the work queue the runner actually reads (W-07 amendment).

W-07 originally specified a one-way Linear -> GitHub Issues sync so the runner
could read Issues while work was managed in Linear. The amendment removed the
mirror instead of making it safe: **remove the mirror and the two-way state race
cannot occur**, which W-07 never considered.

So this is a second `Tracker`, alongside `github_tracker`, and the runner reads
this one. GitHub keeps code, PRs and CI.

## Why Linear is the better side for the trust boundary

The runner's load-bearing check is not "does this ticket carry `agent-ready`" —
it is **which human applied it** (invariant 7). GitHub answers that by matching
login strings out of an issue timeline, which is a string comparison against a
namespace an attacker partly controls.

Linear answers it structurally. Its `IssueHistory` entries carry `actor` and
`botActor` as *distinct fields*, so "a bot did this" is a type in the schema
rather than a naming convention. `label_actor` returns None for a bot rather
than a name that has to be recognised as bot-shaped.

That is a stronger primitive, and it is why M2-08 concluded the free Linear
identity is the load-bearing one while the GitHub App is a later nicety.

## What this adapter may write

The same allowlist rule as the GitHub adapter, for the same reason. This may set
issue *state* and post comments. It may **never** write the label that grants
dispatch eligibility — an adapter that can grant its own runner permission to
work on a ticket has removed the human from the loop that invariant 7 exists to
keep them in.

Enforced by `_WRITABLE_STATES` rather than documented, because a rule in a
constant survives someone being in a hurry and a rule in a comment does not.

## Credentials

The transport is injected. The token stays outside this class, every path here
is testable without a network or an account, and the seam is where a rate
limiter or a retry policy goes later without touching this logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from expfactory.runner import (
    STATE_IN_PROGRESS,
    STATE_IN_REVIEW,
    STATE_NEEDS_HUMAN,
    Ticket,
)

# Linear models workflow position as a *state* on the issue, not as a label.
# That is a better fit than GitHub's labels-as-state and means this adapter never
# touches labels at all — which removes a whole class of mistake, since the label
# it must not write is the one that grants dispatch eligibility.
_WRITABLE_STATES = frozenset({STATE_IN_PROGRESS, STATE_IN_REVIEW, STATE_NEEDS_HUMAN})


class StateWriteRefused(RuntimeError):
    """The adapter was asked to move an issue to a state outside its allowlist."""


class LinearApiError(RuntimeError):
    """The API returned errors. Raised rather than returning an empty result,
    because "no tickets" and "the query failed" must not look the same to a
    poller — one is an idle tick and the other is an outage."""


@runtime_checkable
class GraphQLTransport(Protocol):
    """The whole Linear surface this adapter needs: one authenticated call."""

    def query(self, document: str, variables: dict[str, Any]) -> dict[str, Any]: ...


_ISSUES = """
query($teamKey: String!) {
  issues(filter: {team: {key: {eq: $teamKey}}, state: {type: {nin: ["completed", "canceled"]}}}) {
    nodes {
      id
      identifier
      title
      description
      state { name }
      labels { nodes { name } }
    }
  }
}
"""

# `actor` and `botActor` are separate fields in the schema. That distinction is
# the entire reason the trust check is better here than on GitHub.
_HISTORY = """
query($issueId: String!) {
  issue(id: $issueId) {
    history {
      nodes {
        addedLabelIds
        actor { name displayName }
        botActor { name }
      }
    }
  }
}
"""

_LABELS = """
query($issueId: String!) {
  issue(id: $issueId) { labels { nodes { id name } } }
}
"""

_COMMENT = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) { success }
}
"""

_SET_STATE = """
mutation($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: {stateId: $stateId}) { success }
}
"""

_STATES = """
query($teamKey: String!) {
  workflowStates(filter: {team: {key: {eq: $teamKey}}}) { nodes { id name } }
}
"""


class LinearTracker:
    """`Tracker` over the Linear GraphQL API.

    Ticket ids are Linear's internal UUIDs, not the human-readable `BRE-12`
    identifier. Mutations key on the UUID, and using the identifier for one and
    the UUID for another is the kind of mismatch that silently updates nothing.
    `Ticket.id` therefore carries the UUID and the identifier goes in the title,
    where a human reading a comment can still see it.
    """

    def __init__(self, team_key: str, transport: GraphQLTransport) -> None:
        self._team = team_key
        self._transport = transport
        self._state_ids: dict[str, str] | None = None

    # -- reads -------------------------------------------------------------

    def _call(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        result = self._transport.query(document, variables)
        if "errors" in result and result["errors"]:
            raise LinearApiError(f"Linear API returned errors: {result['errors']}")
        data = result.get("data")
        if data is None:
            raise LinearApiError("Linear API returned no data")
        return dict(data)

    def open_tickets(self) -> Sequence[Ticket]:
        """Issues in this team that are not completed or cancelled.

        Filtered server-side by state *type* rather than by name, because state
        names are user-editable and a renamed column must not silently empty the
        queue.
        """
        data = self._call(_ISSUES, {"teamKey": self._team})
        out: list[Ticket] = []
        for node in data.get("issues", {}).get("nodes", []):
            labels = frozenset(
                label["name"]
                for label in node.get("labels", {}).get("nodes", [])
                if label.get("name")
            )
            out.append(
                Ticket(
                    id=node["id"],
                    title=f"{node.get('identifier', '?')} {node.get('title', '')}".strip(),
                    body=node.get("description") or "",
                    labels=labels,
                    state=(node.get("state") or {}).get("name", "Todo"),
                )
            )
        return out

    def label_actor(self, ticket_id: str, label: str) -> str | None:
        """Who applied `label`, or None if that cannot be established.

        Returns None — never a name — when the actor was a bot. Linear reports
        `botActor` as its own field, so this is a schema-level distinction rather
        than a guess about whether a login looks bot-shaped. The runner's
        allowlist then rejects it without having to recognise bot naming
        conventions, which is exactly the check that races on GitHub.

        None is also returned when nothing in the history added the label. An
        unattributable label is not a yes.
        """
        label_id = self._label_id(ticket_id, label)
        if label_id is None:
            return None

        data = self._call(_HISTORY, {"issueId": ticket_id})
        issue = data.get("issue") or {}
        # Most recent application wins: a label removed and re-applied is
        # attributed to whoever put it back, not to whoever put it there first.
        for entry in reversed(issue.get("history", {}).get("nodes", [])):
            if label_id not in (entry.get("addedLabelIds") or []):
                continue
            if entry.get("botActor"):
                # A bot applied it. Deliberately not returned as a name: the
                # runner would then have to decide whether that name is a bot.
                return None
            actor = entry.get("actor") or {}
            return actor.get("displayName") or actor.get("name")
        return None

    def _label_id(self, ticket_id: str, label: str) -> str | None:
        data = self._call(_LABELS, {"issueId": ticket_id})
        issue = data.get("issue") or {}
        for node in issue.get("labels", {}).get("nodes", []):
            if node.get("name") == label:
                return str(node["id"])
        return None

    # -- writes ------------------------------------------------------------

    def comment(self, ticket_id: str, body: str) -> None:
        self._call(_COMMENT, {"issueId": ticket_id, "body": body})

    def set_state(self, ticket_id: str, state: str) -> None:
        """Move an issue to one of the three states this adapter may set.

        Refused for anything else. The adapter must not be able to move a ticket
        to Done — the runner does not approve its own work, and a tracker that
        can close a ticket is one override away from doing so.
        """
        if state not in _WRITABLE_STATES:
            raise StateWriteRefused(
                f"{state!r} is not a state this adapter may set. "
                f"Allowed: {sorted(_WRITABLE_STATES)}. In particular it may not move a "
                "ticket to Done — the runner does not approve its own work."
            )
        state_id = self._state_id(state)
        self._call(_SET_STATE, {"issueId": ticket_id, "stateId": state_id})

    def _state_id(self, name: str) -> str:
        if self._state_ids is None:
            data = self._call(_STATES, {"teamKey": self._team})
            self._state_ids = {
                node["name"]: node["id"] for node in data.get("workflowStates", {}).get("nodes", [])
            }
        state_id = self._state_ids.get(name)
        if state_id is None:
            raise StateWriteRefused(
                f"team {self._team!r} has no workflow state named {name!r}. "
                f"Known: {sorted(self._state_ids)}. Create it rather than letting the "
                "runner silently fail to move tickets."
            )
        return str(state_id)


__all__ = [
    "GraphQLTransport",
    "LinearApiError",
    "LinearTracker",
    "StateWriteRefused",
]
