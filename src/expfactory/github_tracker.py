"""
github_tracker — the GitHub Issues implementation of `runner.Tracker`.

**Not the primary work queue.** The W-07 amendment made Linear the queue and
removed the sync entirely, so nothing routine flows through here. This adapter
survives for two reasons: it is the deterministic lane's tracker, where the work
already lives beside the PRs and CI that verify it; and it is the second
implementation that proves `Tracker` is a real seam rather than a shape traced
around one caller.

Read `docs/decisions/W-07-AMENDMENT-linear-as-machine-plane.md` before wiring
this into an empirical-lane runner — that is not what it is for.

## A design tension this surfaced, and how it is resolved

The runner's rule was "never write labels", because labels are the human's channel
for granting dispatch rights and a runner that can set them can grant itself work.

But GitHub Issues have no arbitrary state field — `In Progress` / `In Review` /
`Needs Human` have to live *somewhere*, and on GitHub that somewhere is labels
(or Projects, which is heavier and still label-shaped underneath).

So the rule was too broad. Sharpened: **the runner may not write
dispatch-granting labels.** Writing its own state is its job.

That is enforced here rather than documented — `_WRITABLE_LABELS` is an
allowlist, and `agent-ready` is not in it. An adapter asked to write anything
outside the allowlist raises. A rule that lives in a constant beats a rule that
lives in a comment, because only one of them survives someone being in a hurry.

## Pagination, and why it is a trust property rather than a completeness one

Every REST collection here used to be read one page deep. For `open_tickets`
that loses tickets, which is merely wrong. For `label_actor` it is worse: a
truncated timeline that happens not to contain the `labeled` event answers
"nobody applied it", and the runner reads that as *not* dispatch-eligible —
except when the truncation drops a *later* application by a bot and leaves an
earlier one by a human visible, which is an authorization decision made on
partial data, and it fails open.

So the walk is all-or-nothing. A page fetch that fails mid-walk raises; nothing
here returns the prefix it managed to read, because a prefix is
indistinguishable from a complete history to every caller above.

## Ordering policy

**Ascending creation time, enforced here, not inherited from the API.**

Requested from GitHub (`sort=created&direction=asc`) *and* re-sorted on arrival,
because the request is a hint — a server-side default change, a proxy, or a
`Link` header assembled from a different sort would otherwise silently reorder
the history that `label_actor`'s "most recent application wins" rule depends on.
Order the adapter relies on is order the adapter establishes.

Issues sort on `number`, which is monotonic in creation order and always
present, so no timestamp parsing is involved. Timeline events sort on
`created_at`, whose GitHub form is Z-suffixed UTC ISO-8601 and therefore sorts
lexicographically exactly as it sorts chronologically. A `labeled` event with no
`created_at` is refused rather than defaulted: it cannot be placed against the
others, and a guess about where it belongs is a guess about who granted dispatch.

## Credentials

The transport carries the token; this class never sees it. That keeps the
credential in the runner's secret store rather than anywhere an agent's workspace
could reach (invariant 6), and it makes the whole adapter testable without one.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from expfactory.runner import (
    LABEL_AGENT_READY,
    STATE_IN_PROGRESS,
    STATE_IN_REVIEW,
    STATE_NEEDS_HUMAN,
    STATE_RUNNING_UNATTENDED,
    Ticket,
)

# Runner state -> the label that represents it on GitHub.
STATE_LABELS: dict[str, str] = {
    STATE_IN_PROGRESS: "state:in-progress",
    STATE_IN_REVIEW: "state:in-review",
    STATE_NEEDS_HUMAN: "needs-human",
    # The detached state. Absent until BRE-32, which made `_detach` unreachable
    # through this adapter: the runner asked for a state the mapping could not
    # express, so every detached ticket died on a `LabelWriteRefused` *after* its
    # GPU job was already running. The runner now asks `writable_states()` before
    # it dispatches anything.
    STATE_RUNNING_UNATTENDED: "state:running-unattended",
}

# GitHub's ceiling. Asking for more is silently clamped server-side, so the
# constructor refuses a larger value rather than letting a caller believe it
# configured a page budget it did not get.
MAX_PER_PAGE = 100

# A walk longer than this is a loop or a workspace nobody should be polling in
# one tick. Bounded so a `Link` header that never stops advancing cannot spin
# forever holding the runner's tick open.
_MAX_PAGES = 200

# `<url>; rel="next"`. Anchored on the angle brackets rather than split on
# commas, because a URL may legitimately contain a comma (`labels=a,b`).
_LINK_ENTRY = re.compile(r'<([^>]*)>\s*;\s*rel\s*=\s*"?([^",;]+)"?')

# The only labels this adapter will ever write. `agent-ready` is deliberately
# absent: it is how a human grants dispatch rights, and nothing automated may
# grant itself those.
_WRITABLE_LABELS = frozenset(STATE_LABELS.values())


class LabelWriteRefused(RuntimeError):
    """The adapter was asked to write a label outside its allowlist."""


class LabelNotPresent(LookupError):
    """Removing a label the issue does not carry.

    Named rather than guessed at: the adapter has to distinguish "that label was
    already gone", which is fine, from "the request failed", which is not. A
    transport signals the former by raising this and lets everything else
    propagate — swallowing a broad Exception here would hide an auth failure as a
    successful no-op.
    """


class PageWalkRefused(RuntimeError):
    """A paginated read could not be completed, so none of it is returned.

    The walk is all-or-nothing. Answering with the pages that did arrive would
    hand a caller a prefix wearing the shape of a whole collection, and for
    `label_actor` that is an authorization decision made on partial data.
    """


class UnorderableEvent(RuntimeError):
    """A `labeled` event carries no `created_at`, so it cannot be placed.

    Refused rather than defaulted to either end of the history. "Most recent
    application wins" is what decides who granted dispatch, and an event of
    unknown age is equally consistent with being the newest or the oldest.
    """


@dataclass(frozen=True)
class Page:
    """One REST response: the decoded body, plus the headers that say whether it
    is the whole collection.

    The body alone cannot answer that. GitHub signals continuation only in the
    `Link` header, so a transport handing back nothing but parsed JSON makes
    truncation *structurally invisible* to this adapter — which is how both reads
    here came to be one page deep without anyone deciding they should be.
    """

    body: Any
    headers: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class HttpTransport(Protocol):
    """Minimal GitHub REST surface, injected so the token stays outside this class
    and so every path here is testable without a network or an account."""

    def get(self, path: str) -> Page: ...
    def post(self, path: str, body: dict[str, Any]) -> Any: ...
    def delete(self, path: str) -> Any: ...


def _next_path(headers: Mapping[str, str]) -> str | None:
    """The `rel="next"` target of a `Link` header, as a path this transport takes.

    Header names are matched case-insensitively by hand rather than by trusting
    the transport to supply a case-insensitive mapping. `requests` does; a plain
    dict assembled anywhere else does not, and the cost of guessing wrong is a
    walk that silently stops after one page — the exact defect being fixed.

    GitHub's next link is an absolute URL, and only its path and query survive
    here. The host is dropped rather than followed, so a `Link` rewritten by a
    proxy cannot redirect an authenticated read (and the token bound to it) at
    somewhere else.
    """
    raw = next((value for name, value in headers.items() if name.lower() == "link"), None)
    if not raw:
        return None
    for url, rel in _LINK_ENTRY.findall(raw):
        if rel.strip() != "next":
            continue
        split = urlsplit(str(url).strip())
        return split.path + (f"?{split.query}" if split.query else "")
    return None


class GitHubTracker:
    def __init__(
        self, repo: str, transport: HttpTransport, *, per_page: int = MAX_PER_PAGE
    ) -> None:
        """`repo` is "owner/name".

        `per_page` sizes each request, never the total: every collection is
        walked to its end regardless. Lower it only to make a rate-limited
        account take smaller bites.
        """
        if not 1 <= per_page <= MAX_PER_PAGE:
            raise ValueError(
                f"per_page must be between 1 and {MAX_PER_PAGE}, got {per_page}. GitHub "
                "clamps anything larger server-side without saying so, which would leave "
                "the caller believing it configured a page size it did not get."
            )
        self._repo = repo
        self._http = transport
        self._per_page = per_page

    # -- pagination -----------------------------------------------------------

    def _walk(self, path: str) -> list[Any]:
        """Every item in a paginated collection, or an exception. Never a prefix.

        A transport failure part-way through is deliberately *not* caught. It
        propagates with its own type intact, because the caller above needs to
        tell a 401 from a 403 from a rate limit, and wrapping them into one
        adapter error would flatten exactly the distinction that decides whether
        a human or a backoff is the right response. What matters here is only
        that `items` is local: nothing partially collected escapes.
        """
        items: list[Any] = []
        seen = {path}
        current: str | None = path
        pages = 0

        while current is not None:
            pages += 1
            if pages > _MAX_PAGES:
                raise PageWalkRefused(
                    f"{path!r} did not terminate within {_MAX_PAGES} pages. Either the "
                    "collection is larger than anything that should be read in one poll, "
                    "or the Link header is advancing without ever ending."
                )
            page = self._http.get(current)
            if not isinstance(page.body, list):
                # A dict here is GitHub's error envelope, or a single object
                # where a collection was expected. Extending a list with it
                # would produce a plausible-looking short result.
                raise PageWalkRefused(
                    f"{current!r} returned {type(page.body).__name__}, not a JSON array. "
                    "A non-collection cannot be walked, and treating it as an empty page "
                    "would read as 'no results'."
                )
            items.extend(page.body)

            current = _next_path(page.headers)
            if current is not None and current in seen:
                # A cursor that points back at a page already read cannot make
                # progress, and the pages beyond it are unreachable. That is a
                # truncated read, so it gets the same answer as any other one.
                raise PageWalkRefused(
                    f"pagination looped: {current!r} was already fetched while walking "
                    f"{path!r}. The rest of the collection is unreachable, and a partial "
                    "history is not a history."
                )
            if current is not None:
                seen.add(current)
        return items

    # -- reads --------------------------------------------------------------

    def open_tickets(self) -> Sequence[Ticket]:
        issues = self._walk(
            f"/repos/{self._repo}/issues?state=open&sort=created&direction=asc"
            f"&per_page={self._per_page}"
        )
        out: list[Ticket] = []
        # Ordering policy. `sort=created&direction=asc` above is the request;
        # this is the guarantee. Sorted on `number`, which is monotonic in
        # creation order and always present, so no timestamp parsing is
        # involved — and the runner's "first N tickets within budget" therefore
        # means oldest first rather than whatever order the API felt like.
        for issue in sorted(issues, key=lambda issue: int(issue["number"])):
            # The REST issues endpoint returns PRs too; they are not tickets.
            if "pull_request" in issue:
                continue
            labels = frozenset(
                lbl["name"] if isinstance(lbl, dict) else str(lbl)
                for lbl in issue.get("labels", [])
            )
            out.append(
                Ticket(
                    id=str(issue["number"]),
                    title=issue.get("title") or "",
                    body=issue.get("body") or "",
                    labels=labels,
                    state=self._state_from(labels),
                )
            )
        return out

    @staticmethod
    def _state_from(labels: frozenset[str]) -> str:
        for state, label in STATE_LABELS.items():
            if label in labels:
                return state
        return "Todo"

    def label_actor(self, ticket_id: str, label: str) -> str | None:
        """Who applied `label`, from the issue timeline.

        This is the check the runner's trust boundary rests on. Asking *who*
        applied a label does not race with the label-stripping workflow, whereas
        asking whether it is currently present does.

        The most recent `labeled` event wins: a label removed and re-applied by a
        different account should report the account that most recently granted
        it, not the first one ever to.

        The whole timeline is walked. One page deep, "most recent" meant "most
        recent among the first hundred events", and on a busy ticket that is a
        different question with the same answer shape.
        """
        events = self._walk(
            f"/repos/{self._repo}/issues/{ticket_id}/timeline?per_page={self._per_page}"
        )

        # Narrow first, order second. Only applications of *this* label decide
        # the answer, so an unrelated malformed event must not be able to refuse
        # the whole check — while every event that does decide it has to be
        # placeable against the others.
        applications = [
            event
            for event in events
            if event.get("event") == "labeled" and (event.get("label") or {}).get("name") == label
        ]
        if not applications:
            return None

        for event in applications:
            if not event.get("created_at"):
                raise UnorderableEvent(
                    f"a {label!r} application on issue {ticket_id} has no created_at, so it "
                    "cannot be placed against the others. Refused rather than assumed: which "
                    "application is the most recent is which account granted dispatch."
                )

        # `created_at` is Z-suffixed UTC ISO-8601, so it sorts lexicographically
        # exactly as it sorts chronologically and needs no parsing. Sorting is
        # load-bearing rather than belt-and-braces here: unlike the issues
        # endpoint the timeline takes no sort parameter at all, so arrival order
        # is entirely the server's to choose.
        applications.sort(key=lambda event: str(event["created_at"]))

        login = (applications[-1].get("actor") or {}).get("login")
        # No fallback to an earlier application. An unattributable most-recent
        # grant is not a yes, and inheriting the previous actor's name would let
        # an anonymous re-application wear a human's attribution.
        return str(login) if login else None

    def writable_states(self) -> frozenset[str]:
        """The runner states this adapter can actually express.

        Asked before dispatch, so a state the mapping cannot reach is a wiring
        error caught while nothing is running. Previously the runner found out by
        raising `LabelWriteRefused` at the moment it tried to park a ticket —
        after the GPU job behind it had already started.
        """
        return frozenset(STATE_LABELS)

    # -- writes -------------------------------------------------------------

    def comment(self, ticket_id: str, body: str) -> None:
        self._http.post(f"/repos/{self._repo}/issues/{ticket_id}/comments", {"body": body})

    def set_state(self, ticket_id: str, state: str) -> None:
        """Move the ticket by swapping its state label.

        Refuses any label outside the allowlist, so this cannot become a path to
        writing `agent-ready`.
        """
        label = STATE_LABELS.get(state)
        if label is None:
            raise LabelWriteRefused(
                f"no label mapping for state {state!r}; known: {sorted(STATE_LABELS)}"
            )
        self._write_label(ticket_id, label)
        for other in _WRITABLE_LABELS - {label}:
            self._remove_label(ticket_id, other)

    def _write_label(self, ticket_id: str, label: str) -> None:
        if label not in _WRITABLE_LABELS:
            raise LabelWriteRefused(
                f"refusing to write label {label!r}: not in the adapter's allowlist. "
                f"{LABEL_AGENT_READY!r} in particular is how a human grants dispatch "
                "rights, and nothing automated may grant itself those."
            )
        self._http.post(f"/repos/{self._repo}/issues/{ticket_id}/labels", {"labels": [label]})

    def _remove_label(self, ticket_id: str, label: str) -> None:
        if label not in _WRITABLE_LABELS:
            raise LabelWriteRefused(f"refusing to remove label {label!r}: not in the allowlist")
        # Already absent is fine; anything else must surface.
        with contextlib.suppress(LabelNotPresent):
            self._http.delete(f"/repos/{self._repo}/issues/{ticket_id}/labels/{label}")
