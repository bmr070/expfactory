"""
The GitHub Issues adapter for `runner.Tracker`.

Three things are actually worth testing here. One is `label_actor`, because the
runner's whole trust boundary rests on it. The second is that the adapter cannot
be talked into writing `agent-ready` — the rule "the runner may not grant itself
dispatch rights" is only real if something enforces it. The third is pagination,
which is the first two wearing a different hat: a timeline read one page deep
answers "who applied this label" from a prefix, and a prefix that stops before a
bot's re-application still contains an earlier human's.

So the pagination fixtures below are not "does the loop loop". They are: does the
second page change the answer, and does a walk that cannot finish refuse instead
of reporting what it got.

The rest is shape-mapping, tested thinly on purpose.
"""

from __future__ import annotations

import re

import pytest

from expfactory.github_tracker import (
    MAX_PER_PAGE,
    STATE_LABELS,
    GitHubTracker,
    HttpTransport,
    LabelNotPresent,
    LabelWriteRefused,
    Page,
    PageWalkRefused,
    UnorderableEvent,
)
from expfactory.runner import (
    LABEL_AGENT_READY,
    LANE_EMPIRICAL,
    STATE_IN_PROGRESS,
    STATE_IN_REVIEW,
    STATE_RUNNING_UNATTENDED,
    Runner,
)

REPO = "bmr070/expfactory"


def _adjudicating_verifier():
    """A verifier configured the way the hill-climb runner must configure one.

    The preregistration gate names are grafted on rather than produced by filing
    a real prereg: these tests are about the GitHub adapter driving the runner,
    and standing up a ledger would turn them into G-07 tests. The refusal path
    has its own coverage in tests/test_runner.py.
    """
    import dataclasses

    from expfactory.runner import REQUIRED_EMPIRICAL_GATES
    from expfactory.verifier import GateVerifier

    class _V:
        def run(self, candidate, ticket=None):
            bundle = GateVerifier(id_factory=lambda: "e1").run(candidate)
            return dataclasses.replace(
                bundle, gate_names=(*bundle.gate_names, *sorted(REQUIRED_EMPIRICAL_GATES))
            )

    return _V()


def _page_number(path: str) -> int:
    match = re.search(r"[?&]page=(\d+)", path)
    return int(match.group(1)) if match else 1


class FakeHttp:
    """Serves canned pages the way GitHub does: a `page=` parameter and a `Link`
    header naming the next one.

    A response value is either one page (a list of items) or several (a list of
    lists). The `Link` header is *generated* rather than written into each
    fixture, because a header typed by hand in a fixture is how a pagination test
    comes to pass against a walk that never followed it.
    """

    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses = responses or {}
        self.posts: list[tuple[str, dict]] = []
        self.deletes: list[str] = []
        self.gets: list[str] = []

    @staticmethod
    def _pages(value: object) -> list[list]:
        assert isinstance(value, list)
        if value and all(isinstance(item, list) for item in value):
            return value
        return [value]

    def get(self, path: str) -> Page:
        self.gets.append(path)
        # Longest prefix first. "/repos/x/issues" also prefixes
        # "/repos/x/issues/1/timeline", so insertion order would silently serve
        # the issue list where a timeline was asked for — which is exactly the
        # kind of fake that makes a passing test meaningless.
        for key in sorted(self.responses, key=len, reverse=True):
            if not path.startswith(key):
                continue
            pages = self._pages(self.responses[key])
            index = _page_number(path)
            body = pages[index - 1] if 1 <= index <= len(pages) else []
            headers = {}
            if index < len(pages):
                headers["Link"] = (
                    f'<https://api.github.com{key}?page={index + 1}>; rel="next", '
                    f'<https://api.github.com{key}?page={len(pages)}>; rel="last"'
                )
            return Page(body, headers)
        return Page([], {})

    def post(self, path: str, body: dict):
        self.posts.append((path, body))
        return {}

    def delete(self, path: str):
        self.deletes.append(path)
        return {}


def _issue(number=1, labels=(LABEL_AGENT_READY, LANE_EMPIRICAL), **over):
    d = {
        "number": number,
        "title": "t",
        "body": "b",
        "labels": [{"name": n} for n in labels],
    }
    d.update(over)
    return d


def _labeled(login: str, name: str = LABEL_AGENT_READY, at: str = "2026-07-01T00:00:00Z"):
    return {
        "event": "labeled",
        "label": {"name": name},
        "actor": {"login": login},
        "created_at": at,
    }


def test_transport_protocol_is_satisfied_structurally():
    assert isinstance(FakeHttp(), HttpTransport)


# ---- the check the trust boundary rests on ---------------------------------


def test_label_actor_reads_the_timeline():
    http = FakeHttp({f"/repos/{REPO}/issues/1/timeline": [_labeled("bmr070")]})
    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) == "bmr070"


def test_label_actor_ignores_other_labels():
    http = FakeHttp(
        {f"/repos/{REPO}/issues/1/timeline": [_labeled("bmr070", name="lane:empirical")]}
    )
    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) is None


def test_the_most_recent_application_wins():
    """Removed and re-applied by someone else: report who most recently granted
    it, not whoever did first. Otherwise an old human application would launder
    a later one by a bot."""
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues/1/timeline": [
                _labeled("bmr070", at="2026-07-01T00:00:00Z"),
                _labeled("expfactory-agent[bot]", at="2026-07-02T00:00:00Z"),
            ]
        }
    )
    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) == "expfactory-agent[bot]"


def test_no_timeline_means_no_actor():
    assert GitHubTracker(REPO, FakeHttp()).label_actor("1", LABEL_AGENT_READY) is None


def test_an_application_with_no_actor_login_is_not_attributed_to_an_older_one():
    """The most recent grant is the one that counts. Falling back to the previous
    application would let an unattributable re-application wear the name of the
    human who applied it before."""
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues/1/timeline": [
                _labeled("bmr070", at="2026-07-01T00:00:00Z"),
                {
                    "event": "labeled",
                    "label": {"name": LABEL_AGENT_READY},
                    "actor": None,
                    "created_at": "2026-07-02T00:00:00Z",
                },
            ]
        }
    )
    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) is None


# ---- pagination: the second page is where the answer changes ----------------
#
# BRE-32 defect 3. These are not loop mechanics. Reading one page deep makes
# `label_actor` answer invariant 7's question from a prefix, and the prefix that
# ends before a bot's re-application still contains the human's earlier one — so
# the truncation does not merely lose information, it fails open.


def test_a_labeled_event_on_the_second_page_is_found():
    """The whole point of the walk, stated as a single assertion.

    Page one carries a human's application and nothing else, so a one-page read
    returns `bmr070` and the runner dispatches. Page two carries the bot that
    re-applied it afterwards. Same board, same token, opposite decision.
    """
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues/1/timeline": [
                [_labeled("bmr070", at="2026-07-01T00:00:00Z")],
                [_labeled("expfactory-agent[bot]", at="2026-07-02T00:00:00Z")],
            ]
        }
    )

    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) == "expfactory-agent[bot]"
    assert len(http.gets) == 2, "the second page was never fetched"


def test_the_truncated_read_would_have_said_something_else():
    """The positive control for the test above. Without it, a walk that silently
    returned the last page only would pass it just as well."""
    first_page_only = FakeHttp(
        {f"/repos/{REPO}/issues/1/timeline": [_labeled("bmr070", at="2026-07-01T00:00:00Z")]}
    )
    assert GitHubTracker(REPO, first_page_only).label_actor("1", LABEL_AGENT_READY) == "bmr070"


def test_a_failure_mid_walk_raises_rather_than_returning_the_prefix():
    """A prefix is indistinguishable from a complete history to every caller
    above, so returning what arrived would report a *human* grant that the
    unread page had already superseded."""

    class BreaksOnPageTwo(FakeHttp):
        def get(self, path: str) -> Page:
            if _page_number(path) > 1:
                raise PermissionError("401 while paginating")
            return super().get(path)

    http = BreaksOnPageTwo(
        {
            f"/repos/{REPO}/issues/1/timeline": [
                [_labeled("bmr070")],
                [_labeled("expfactory-agent[bot]")],
            ]
        }
    )

    # The transport's own exception type, not an adapter-flavoured one: whoever
    # is above has to tell a 401 from a rate limit to know whether a human or a
    # backoff is the right response.
    with pytest.raises(PermissionError):
        GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY)


def test_a_link_header_that_points_backwards_is_refused_not_followed():
    """A cursor returning to a page already read cannot make progress, and
    whatever lies past it is unreachable — which is a truncated read by another
    name, and gets the same answer."""

    class Loops(FakeHttp):
        def get(self, path: str) -> Page:
            return Page(
                [_labeled("bmr070")],
                {"Link": f'<https://api.github.com/repos/{REPO}/issues/1/timeline>; rel="next"'},
            )

    with pytest.raises(PageWalkRefused, match="looped"):
        GitHubTracker(REPO, Loops()).label_actor("1", LABEL_AGENT_READY)


def test_a_link_header_that_never_ends_is_bounded():
    """Distinct pages forever is not a loop the seen-set can catch. Bounded so a
    server that always says "one more" cannot hold the runner's tick open."""

    class Endless(FakeHttp):
        def __init__(self):
            super().__init__()
            self.n = 0

        def get(self, path: str) -> Page:
            self.n += 1
            return Page(
                [],
                {"Link": f'<https://api.github.com/x?page={self.n}>; rel="next"'},
            )

    http = Endless()
    with pytest.raises(PageWalkRefused, match="did not terminate"):
        GitHubTracker(REPO, http).open_tickets()
    assert http.n < 500, "the bound did not bound anything"


def test_a_next_link_pointing_at_another_host_stays_on_this_one():
    """Only the path and query survive. A `Link` rewritten in transit must not be
    able to aim an authenticated read — and the token behind it — somewhere
    else."""

    class Redirects(FakeHttp):
        def get(self, path: str) -> Page:
            self.gets.append(path)
            if len(self.gets) == 1:
                return Page([], {"Link": '<https://evil.example/steal?x=1>; rel="next"'})
            return Page([], {})

    http = Redirects()
    http.gets.clear()
    GitHubTracker(REPO, http).open_tickets()

    assert http.gets[1] == "/steal?x=1"
    assert not any("evil.example" in got for got in http.gets)


def test_the_link_header_is_found_whatever_its_case():
    """`requests` hands back a case-insensitive mapping; a plain dict does not.
    Guessing wrong stops the walk after one page, which is the defect."""

    class LowerCase(FakeHttp):
        def get(self, path: str) -> Page:
            self.gets.append(path)
            if len(self.gets) == 1:
                return Page(
                    [_labeled("bmr070", at="2026-07-01T00:00:00Z")],
                    {"link": f'<https://api.github.com/repos/{REPO}/issues/1/x>; rel="next"'},
                )
            return Page([_labeled("expfactory-agent[bot]", at="2026-07-02T00:00:00Z")], {})

    http = LowerCase()
    http.gets.clear()
    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) == "expfactory-agent[bot]"


def test_a_link_url_containing_a_comma_is_parsed_whole():
    """`labels=a,b` is a legal query. Splitting the header on commas would cut
    the URL in half and end the walk one page early."""

    class Comma(FakeHttp):
        def get(self, path: str) -> Page:
            self.gets.append(path)
            if len(self.gets) == 1:
                return Page(
                    [],
                    {"Link": '<https://api.github.com/i?labels=a,b&page=2>; rel="next"'},
                )
            return Page([], {})

    http = Comma()
    http.gets.clear()
    GitHubTracker(REPO, http).open_tickets()

    assert http.gets[1] == "/i?labels=a,b&page=2"


def test_a_body_that_is_not_a_collection_is_refused():
    """GitHub's error envelope is a dict. Treating it as an empty page would read
    as "no results", which is the shape of an idle tick rather than an outage."""

    class Envelope(FakeHttp):
        def get(self, path: str) -> Page:
            return Page({"message": "Bad credentials"}, {})

    with pytest.raises(PageWalkRefused, match="not a JSON array"):
        GitHubTracker(REPO, Envelope()).open_tickets()


def test_per_page_above_githubs_ceiling_is_refused_at_construction():
    """GitHub clamps a larger value server-side without saying so, leaving the
    caller believing it configured a page size it did not get."""
    with pytest.raises(ValueError, match="between 1 and 100"):
        GitHubTracker(REPO, FakeHttp(), per_page=MAX_PER_PAGE + 1)

    with pytest.raises(ValueError):
        GitHubTracker(REPO, FakeHttp(), per_page=0)


def test_every_read_asks_for_the_configured_page_size():
    http = FakeHttp({f"/repos/{REPO}/issues": [_issue(1)]})
    tracker = GitHubTracker(REPO, http, per_page=25)

    tracker.open_tickets()
    tracker.label_actor("1", LABEL_AGENT_READY)

    assert all("per_page=25" in got for got in http.gets)


# ---- the ordering policy ----------------------------------------------------


def test_pages_arriving_out_of_order_are_corrected():
    """The server's order is a hint. A `Link` chain assembled from a different
    sort, or a proxy, would otherwise reorder the history that "most recent
    application wins" depends on — and reversing two applications reverses the
    dispatch decision.
    """
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues/1/timeline": [
                # Page one holds the *later* event. Server order is wrong.
                [_labeled("expfactory-agent[bot]", at="2026-07-09T00:00:00Z")],
                [_labeled("bmr070", at="2026-07-02T00:00:00Z")],
            ]
        }
    )
    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) == "expfactory-agent[bot]"


def test_tickets_come_back_oldest_first_across_pages():
    """Which ticket gets the runner's last unit of concurrency should be a policy,
    not a coincidence. Sorted on `number`, which is monotonic in creation order
    and always present, so no timestamp parsing is involved — and note 9 before
    10, which a string sort of the ids would get wrong."""
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues": [
                [_issue(10), _issue(3)],
                [_issue(9), _issue(1)],
            ]
        }
    )
    assert [t.id for t in GitHubTracker(REPO, http).open_tickets()] == ["1", "3", "9", "10"]


def test_the_issues_read_also_asks_the_server_for_ascending_creation_order():
    """Requested as well as enforced. Re-sorting a page is cheap; re-sorting a
    collection the server truncated by a different key is not possible at all."""
    http = FakeHttp({f"/repos/{REPO}/issues": [_issue(1)]})
    GitHubTracker(REPO, http).open_tickets()

    assert "sort=created" in http.gets[0]
    assert "direction=asc" in http.gets[0]


def test_an_application_with_no_timestamp_is_refused_not_placed():
    """It cannot be ordered against the others, and a guess about where it
    belongs is a guess about who granted dispatch."""
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues/1/timeline": [
                _labeled("bmr070"),
                {
                    "event": "labeled",
                    "label": {"name": LABEL_AGENT_READY},
                    "actor": {"login": "someone"},
                },
            ]
        }
    )
    with pytest.raises(UnorderableEvent, match="created_at"):
        GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY)


def test_an_unrelated_event_missing_a_timestamp_does_not_refuse_the_check():
    """Narrow first, order second. Only applications of *this* label decide the
    answer, so an unrelated malformed event must not be able to stop the runner
    reading a board."""
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues/1/timeline": [
                {"event": "commented"},
                _labeled("bmr070", name="lane:empirical"),
                _labeled("bmr070", at="2026-07-03T00:00:00Z"),
            ]
        }
    )
    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) == "bmr070"


# ---- the adapter cannot grant dispatch rights ------------------------------


def test_agent_ready_is_not_writable():
    """The rule is only real if something enforces it. `agent-ready` is absent
    from the allowlist, so there is no path through this adapter that applies it."""
    assert LABEL_AGENT_READY not in set(STATE_LABELS.values())
    tracker = GitHubTracker(REPO, FakeHttp())
    with pytest.raises(LabelWriteRefused, match="dispatch"):
        tracker._write_label("1", LABEL_AGENT_READY)


def test_an_unknown_state_is_refused_rather_than_guessed():
    with pytest.raises(LabelWriteRefused, match="no label mapping"):
        GitHubTracker(REPO, FakeHttp()).set_state("1", "Done")


def test_the_unattended_state_can_be_written():
    """BRE-32 defect 2. This mapping had no entry for it, so `_detach` raised
    `LabelWriteRefused` — after the GPU job it was parking had already started."""
    http = FakeHttp()
    GitHubTracker(REPO, http).set_state("1", STATE_RUNNING_UNATTENDED)

    assert http.posts == [
        (f"/repos/{REPO}/issues/1/labels", {"labels": ["state:running-unattended"]}),
    ]


def test_the_unattended_label_is_cleared_when_the_ticket_moves_on():
    """It is in the allowlist, so it is also in the set the next transition
    strips. A ticket in review still wearing `state:running-unattended` counts
    against concurrency forever."""
    http = FakeHttp()
    GitHubTracker(REPO, http).set_state("1", STATE_IN_REVIEW)

    assert any("state:running-unattended" in d for d in http.deletes)


def test_setting_a_state_writes_one_label_and_clears_the_others():
    http = FakeHttp()
    GitHubTracker(REPO, http).set_state("1", STATE_IN_REVIEW)

    assert http.posts == [
        (f"/repos/{REPO}/issues/1/labels", {"labels": ["state:in-review"]}),
    ]
    assert all("state:in-review" not in d for d in http.deletes)
    assert any("state:in-progress" in d for d in http.deletes)


# ---- shape mapping ----------------------------------------------------------


def test_pull_requests_are_not_tickets():
    http = FakeHttp({f"/repos/{REPO}/issues": [_issue(1), _issue(2, pull_request={"url": "..."})]})
    assert [t.id for t in GitHubTracker(REPO, http).open_tickets()] == ["1"]


def test_state_is_derived_from_labels():
    http = FakeHttp(
        {f"/repos/{REPO}/issues": [_issue(1, labels=(LABEL_AGENT_READY, "state:in-progress"))]}
    )
    assert GitHubTracker(REPO, http).open_tickets()[0].state == STATE_IN_PROGRESS


def test_a_null_body_becomes_empty_string():
    """GitHub returns null for an empty body; the runner hands the body to an
    agent and should not hand it None."""
    http = FakeHttp({f"/repos/{REPO}/issues": [_issue(1, body=None)]})
    assert GitHubTracker(REPO, http).open_tickets()[0].body == ""


# ---- it satisfies the seam the runner was built against --------------------


def test_the_adapter_drives_the_real_runner():
    """End to end against the actual Runner, not a fake tracker — the point of
    building against a protocol is that this substitution needs no changes."""
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues": [_issue(1)],
            f"/repos/{REPO}/issues/1/timeline": [_labeled("bmr070")],
        }
    )
    tracker = GitHubTracker(REPO, http)

    class Agent:
        """Returns evidence only. Adjudication is the runner's job (GH#33)."""

        def run(self, ticket, workspace=None):
            from expfactory.verifier import Candidate

            runs = [
                dict(
                    seed=s,
                    val_metric=0.80,
                    train_ids_hash="t",
                    eval_ids_hash="e",
                    overlap_count=0,
                    wall_seconds=0.0,
                )
                for s in range(3)
            ]
            return Candidate(hypothesis="h", config={}, code_hash="c", runs=runs, cost_usd=0.1)

    result = Runner(
        tracker, Agent(), _adjudicating_verifier(), human_allowlist=frozenset({"bmr070"})
    ).tick()

    assert result.dispatched == ["1"]
    assert any("PROMOTED" in body["body"] for _, body in http.posts if "body" in body)


def test_a_bot_applied_label_is_refused_end_to_end():
    """The same wiring, with the label applied by a bot instead. Nothing else
    changes and it must not dispatch."""
    http = FakeHttp(
        {
            f"/repos/{REPO}/issues": [_issue(1)],
            f"/repos/{REPO}/issues/1/timeline": [_labeled("expfactory-agent[bot]")],
        }
    )

    class NeverCalled:
        def run(self, ticket, workspace=None):
            raise AssertionError("must not dispatch")

    result = Runner(
        GitHubTracker(REPO, http),
        NeverCalled(),
        _adjudicating_verifier(),
        human_allowlist=frozenset({"bmr070"}),
    ).tick()
    assert result.dispatched == []


def test_removing_an_absent_label_is_not_an_error():
    class Absent(FakeHttp):
        def delete(self, path: str):
            raise LabelNotPresent(path)

    GitHubTracker(REPO, Absent()).set_state("1", STATE_IN_REVIEW)  # no raise


def test_a_real_transport_failure_is_not_swallowed():
    """Suppressing broadly here would hide an auth failure as a successful no-op,
    and a runner that thinks it moved a ticket it did not is worse than one that
    crashes."""

    class Broken(FakeHttp):
        def delete(self, path: str):
            raise PermissionError("401")

    with pytest.raises(PermissionError):
        GitHubTracker(REPO, Broken()).set_state("1", STATE_IN_REVIEW)
