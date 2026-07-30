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

## Pagination, and why it is a trust property here too

Every Linear list is a cursor-paginated connection with a default page size of
50, and `pageInfo { hasNextPage endCursor }` is the only thing that says whether
what came back is the whole of it. Reading one page was measured, not theorised:
`issues(first: 3)` returned `hasNextPage: true` on a workspace with one team and
about thirty tickets (2026-07-28).

The consequence is the same as on the GitHub side and it is not "some tickets go
unseen". `label_actor` reads issue history to establish **which human applied
`agent-ready`** (invariant 7). A history read one page deep answers that question
from a prefix, and a prefix that happens to end before a bot's re-application
still contains an earlier human's, so it answers *yes* to a grant that was
superseded. That is an authorization decision made on partial data and it fails
open.

So every connection here is walked to `hasNextPage: false` or it raises. Nothing
returns the nodes it managed to collect: a prefix is indistinguishable from a
complete list to every caller above. That covers all four reads — issues,
history, an issue's labels, and the team's workflow states — because each one
truncates into a different wrong answer (a missing ticket, a laundered grant, an
unattributable label, a state the adapter reports it cannot reach).

## Ordering policy

**Ascending creation time, enforced here, not inherited from the API.**

Linear's connections order by `updatedAt` descending unless told otherwise, and
`orderBy` accepts no direction argument — so the order nodes arrive in is not the
order this adapter needs, and asking nicely cannot make it so. Both lists are
therefore re-sorted on `createdAt` after the walk completes.

For history that is the whole basis of "most recent application wins". For issues
it decides which ticket the runner dispatches when its concurrency budget is
smaller than the queue, and oldest-first is a policy, whereas whatever the server
returns is a coincidence.

`createdAt` is a Z-suffixed UTC ISO-8601 string, which sorts lexicographically
exactly as it sorts chronologically, so no parsing is involved. An entry that
lacks it is refused rather than defaulted: for a history entry that added the
label, a guess about where it belongs is a guess about who granted dispatch.

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
    STATE_RUNNING_UNATTENDED,
    Ticket,
)

# Linear models workflow position as a *state* on the issue, not as a label.
# That is a better fit than GitHub's labels-as-state and means this adapter never
# touches labels at all — which removes a whole class of mistake, since the label
# it must not write is the one that grants dispatch eligibility.
#
# `Running Unattended` joined the set in BRE-32. Its absence made the detach path
# unreachable through this adapter: the runner parked a ticket, this refused the
# state, and the refusal arrived *after* a GPU job was already running. Note what
# was NOT done to fix that — the allowlist was not widened to "anything the
# runner asks for". It gained one named state, and `agent-ready` remains
# unwritable by construction because this adapter mutates no labels at all.
_WRITABLE_STATES = frozenset(
    {STATE_IN_PROGRESS, STATE_IN_REVIEW, STATE_NEEDS_HUMAN, STATE_RUNNING_UNATTENDED}
)

# Linear's default. Deliberately not its 250 ceiling: page size trades request
# count against the size of the response held in memory, and the walk reads every
# page either way, so the only thing a bigger number buys is fewer round trips at
# a larger blast radius when one of them fails.
DEFAULT_PAGE_SIZE = 50

# The API's own cap. Asking for more is an error server-side rather than a silent
# clamp, but refusing here means the caller learns at construction instead of on
# the first poll.
MAX_PAGE_SIZE = 250

# A walk longer than this is a loop or a workspace nobody should be polling in
# one tick. Bounded so a cursor that never stops advancing cannot spin forever
# holding the runner's tick open.
_MAX_PAGES = 200


class StateWriteRefused(RuntimeError):
    """The adapter was asked to move an issue to a state outside its allowlist."""


class PageWalkRefused(RuntimeError):
    """A connection could not be walked to its end, so none of it is returned.

    All-or-nothing on purpose. Handing back the nodes that did arrive would give
    a caller a prefix wearing the shape of a complete list, and for `label_actor`
    that is an authorization decision made on partial data.
    """


class UnorderableEntry(RuntimeError):
    """A node carries no `createdAt`, so it cannot be placed against the others.

    Refused rather than defaulted to either end. For a history entry that added
    the label, "which application is the most recent" is "which account granted
    dispatch", and an entry of unknown age is equally consistent with being the
    newest or the oldest.
    """


class LinearApiError(RuntimeError):
    """The API returned errors. Raised rather than returning an empty result,
    because "no tickets" and "the query failed" must not look the same to a
    poller — one is an idle tick and the other is an outage."""


@runtime_checkable
class GraphQLTransport(Protocol):
    """The whole Linear surface this adapter needs: one authenticated call."""

    def query(self, document: str, variables: dict[str, Any]) -> dict[str, Any]: ...


# Every list below takes `$first`/`$after` and returns `pageInfo`, including the
# two nested connections. A nested connection paginates independently of its
# parent — an issue with 200 history entries truncates at 50 inside a query that
# reports itself complete — so `pageInfo` on the outer object would say nothing
# about it.
_ISSUES = """
query($teamKey: String!, $first: Int!, $after: String) {
  issues(
    filter: {team: {key: {eq: $teamKey}}, state: {type: {nin: ["completed", "canceled"]}}}
    first: $first
    after: $after
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      description
      createdAt
      state { name }
      labels(first: 100) {
        pageInfo { hasNextPage }
        nodes { name }
      }
    }
  }
}
"""

# `actor` and `botActor` are separate fields in the schema. That distinction is
# the entire reason the trust check is better here than on GitHub.
_HISTORY = """
query($issueId: String!, $first: Int!, $after: String) {
  issue(id: $issueId) {
    history(first: $first, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        addedLabelIds
        createdAt
        actor { name displayName }
        botActor { name }
      }
    }
  }
}
"""

_LABELS = """
query($issueId: String!, $first: Int!, $after: String) {
  issue(id: $issueId) {
    labels(first: $first, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id name }
    }
  }
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
query($teamKey: String!, $first: Int!, $after: String) {
  workflowStates(filter: {team: {key: {eq: $teamKey}}}, first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id name }
  }
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

    def __init__(
        self,
        team_key: str,
        transport: GraphQLTransport,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        """`page_size` sizes each request, never the total: every connection is
        walked to its end regardless."""
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(
                f"page_size must be between 1 and {MAX_PAGE_SIZE}, got {page_size}. "
                "Linear rejects a larger `first` outright, and finding that out on the "
                "first poll rather than at construction is a worse place to learn it."
            )
        self._team = team_key
        self._transport = transport
        self._page_size = page_size
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

    def _walk(
        self, document: str, variables: dict[str, Any], *, path: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        """Every node of one connection, or an exception. Never a prefix.

        `path` names where the connection sits in the response — `("issues",)`
        for a top-level one, `("issue", "history")` for a nested one — so the
        same walk covers both rather than having a bespoke loop per query, which
        is how three of the four came to lack one.

        A transport failure part-way through is deliberately not caught. It
        propagates with its type intact, because whoever is above needs to tell
        an auth failure from a rate limit, and flattening both into one adapter
        error erases the distinction that decides whether a human or a backoff is
        the right response. What matters here is that `nodes` is local: nothing
        partially collected escapes.
        """
        nodes: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        pages = 0

        while True:
            pages += 1
            if pages > _MAX_PAGES:
                raise PageWalkRefused(
                    f"{'.'.join(path)} did not terminate within {_MAX_PAGES} pages. Either "
                    "the connection is larger than anything that should be read in one "
                    "poll, or the cursor is advancing without ever ending."
                )
            data = self._call(document, {**variables, "first": self._page_size, "after": cursor})

            connection: Any = data
            for key in path:
                if not isinstance(connection, dict) or key not in connection:
                    # **The fail-open a review found, and the one this module's
                    # docstring already claimed was closed.**
                    #
                    # This used to be `(connection or {}).get(key) or {}`, which
                    # degraded an unlocatable connection to an empty dict. Then
                    # `nodes` got nothing, `pageInfo` was `{}`, `hasNextPage` was
                    # falsy, and the loop *returned the pages it already had*.
                    #
                    # Reproduced: page one of an issue's history carrying a
                    # human's `agent-ready` application with `hasNextPage: true`,
                    # page two arriving as `{"data": {"issue": null}}` with no
                    # `errors` key so `_call` does not raise. `label_actor`
                    # answered `'Brett R'` for a ticket whose most recent grant
                    # was a bot's — a superseded grant read as current, which is
                    # precisely the authorization-on-partial-data failure the
                    # pagination work exists to remove.
                    #
                    # Cannot-locate is not empty. The GitHub adapter already
                    # refuses the equivalent shape (a non-list body); this is the
                    # same refusal, and its absence here was the asymmetry.
                    raise PageWalkRefused(
                        f"{'.'.join(path)} could not be located in the response on page "
                        f"{pages} (stopped at {key!r}). A connection that cannot be found "
                        "is not an empty connection, and returning what was collected so "
                        "far would answer an authorization question from a prefix."
                    )
                connection = connection[key]
            if connection is None:
                raise PageWalkRefused(
                    f"{'.'.join(path)} was null on page {pages}. Null is not empty, and "
                    "a partial list is not a list."
                )
            nodes.extend(connection.get("nodes") or [])

            info = connection.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                # Includes the case of no `pageInfo` at all. A response that does
                # not claim there is more is taken at its word — there is no
                # third answer available, and assuming truncation would make
                # every read fail on a server that omits the field.
                #
                # A review argued this should refuse, since `hasNextPage` is
                # `Boolean!` in Linear's schema and a conforming server always
                # sends it, so the tolerance protects an impossible case. That is
                # a fair argument and it is NOT taken here: the behaviour is
                # deliberate, `test_a_response_with_no_pageinfo_is_taken_at_its_word`
                # encodes it, and overturning a recorded decision belongs in a
                # ticket rather than in a security patch. Filed.
                #
                # The reachable half of that finding — a connection that cannot
                # be *located* at all — is refused above, and that is the one
                # that produced a live fail-open.
                return nodes

            next_cursor = info.get("endCursor")
            if not next_cursor:
                # "There is more" and "here is where it continues" have to arrive
                # together. One without the other is a page boundary that cannot
                # be crossed, and the nodes past it are unreachable.
                raise PageWalkRefused(
                    f"{'.'.join(path)} reported hasNextPage with no endCursor. The rest of "
                    "the connection is unreachable, and a partial list is not a list."
                )
            if next_cursor in seen:
                # A cursor that repeats cannot make progress. Bounded above too,
                # but caught precisely here so the message names the real fault
                # instead of blaming the page budget.
                raise PageWalkRefused(
                    f"pagination looped: cursor {next_cursor!r} was already used while "
                    f"walking {'.'.join(path)}. The rest of the connection is unreachable."
                )
            seen.add(str(next_cursor))
            cursor = str(next_cursor)

    @staticmethod
    def _by_created_at(nodes: list[dict[str, Any]], what: str) -> list[dict[str, Any]]:
        """Ascending creation time, established here rather than requested.

        Linear orders connections by `updatedAt` descending by default and
        `orderBy` takes no direction, so the arrival order is never the one this
        adapter needs. `createdAt` is Z-suffixed UTC ISO-8601 and therefore sorts
        lexicographically exactly as it sorts chronologically.
        """
        for node in nodes:
            if not node.get("createdAt"):
                raise UnorderableEntry(
                    f"a {what} entry has no createdAt, so it cannot be placed against the "
                    "others. Refused rather than assumed: order is what decides which "
                    "application of a label is the most recent one."
                )
        return sorted(nodes, key=lambda node: str(node["createdAt"]))

    def open_tickets(self) -> Sequence[Ticket]:
        """Issues in this team that are not completed or cancelled.

        Filtered server-side by state *type* rather than by name, because state
        names are user-editable and a renamed column must not silently empty the
        queue.
        """
        nodes = self._walk(_ISSUES, {"teamKey": self._team}, path=("issues",))
        out: list[Ticket] = []
        # Oldest first, so which ticket gets the runner's last unit of
        # concurrency is a policy rather than a coincidence of server ordering.
        for node in self._by_created_at(nodes, "issue"):
            label_page = node.get("labels") or {}
            if (label_page.get("pageInfo") or {}).get("hasNextPage"):
                # The nested label connection is requested at 100 and not walked.
                # Refusing beats a second query per ticket: an issue carrying
                # more than 100 labels is a data problem, and a truncated label
                # set is not merely incomplete — losing `needs-human` from it
                # would make an escalated ticket read as dispatchable.
                raise PageWalkRefused(
                    f"issue {node.get('identifier', node.get('id'))} carries more than 100 "
                    "labels, so its label set came back truncated. Eligibility is decided "
                    "from these labels, and a missing needs-human reads as dispatchable."
                )
            labels = frozenset(
                label["name"] for label in label_page.get("nodes", []) if label.get("name")
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

        The whole history is walked. One page deep, "most recent" meant "most
        recent among the first fifty entries", which is a different question
        wearing the same answer — and one whose wrong answer is *yes*.
        """
        label_id = self._label_id(ticket_id, label)
        if label_id is None:
            return None

        nodes = self._walk(_HISTORY, {"issueId": ticket_id}, path=("issue", "history"))

        # Narrow first, order second. Only entries that added *this* label decide
        # the answer, so an unrelated entry missing a timestamp must not refuse
        # the whole check — while every entry that does decide it has to be
        # placeable against the others.
        applications = [entry for entry in nodes if label_id in (entry.get("addedLabelIds") or [])]
        if not applications:
            return None

        # Most recent application wins: a label removed and re-applied is
        # attributed to whoever put it back, not to whoever put it there first.
        # Ordered here rather than by reading the list backwards, which only
        # worked while the server happened to return history ascending.
        latest = self._by_created_at(applications, "history")[-1]
        if latest.get("botActor"):
            # A bot applied it. Deliberately not returned as a name: the runner
            # would then have to decide whether that name is a bot.
            return None
        actor = latest.get("actor") or {}
        name = actor.get("displayName") or actor.get("name")
        return str(name) if name else None

    def _label_id(self, ticket_id: str, label: str) -> str | None:
        nodes = self._walk(_LABELS, {"issueId": ticket_id}, path=("issue", "labels"))
        for node in nodes:
            if node.get("name") == label:
                return str(node["id"])
        return None

    # -- writes ------------------------------------------------------------

    def comment(self, ticket_id: str, body: str) -> None:
        self._call(_COMMENT, {"issueId": ticket_id, "body": body})

    def writable_states(self) -> frozenset[str]:
        """The runner states this adapter may set.

        Asked before dispatch, so a state the allowlist cannot reach is a wiring
        error caught while nothing is running. Previously the runner found out by
        catching `StateWriteRefused` at the moment it tried to park a ticket —
        after the GPU job behind it had already started.
        """
        return _WRITABLE_STATES

    def set_state(self, ticket_id: str, state: str) -> None:
        """Move an issue to one of the states this adapter may set.

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
            # Walked, not first-paged. A truncated state list turns the refusal
            # below into a lie: it would report the team has no such state when
            # the state exists and simply arrived on page two.
            nodes = self._walk(_STATES, {"teamKey": self._team}, path=("workflowStates",))
            self._state_ids = {node["name"]: node["id"] for node in nodes}
        state_id = self._state_ids.get(name)
        if state_id is None:
            raise StateWriteRefused(
                f"team {self._team!r} has no workflow state named {name!r}. "
                f"Known: {sorted(self._state_ids)}. Create it rather than letting the "
                "runner silently fail to move tickets."
            )
        return str(state_id)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "GraphQLTransport",
    "LinearApiError",
    "LinearTracker",
    "PageWalkRefused",
    "StateWriteRefused",
    "UnorderableEntry",
]
