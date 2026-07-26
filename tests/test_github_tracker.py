"""
The GitHub Issues adapter for `runner.Tracker`.

Two things are actually worth testing here. One is `label_actor`, because the
runner's whole trust boundary rests on it. The other is that the adapter cannot
be talked into writing `agent-ready` — the rule "the runner may not grant itself
dispatch rights" is only real if something enforces it.

The rest is shape-mapping, tested thinly on purpose.
"""

from __future__ import annotations

import pytest

from expfactory.github_tracker import (
    STATE_LABELS,
    GitHubTracker,
    HttpTransport,
    LabelNotPresent,
    LabelWriteRefused,
)
from expfactory.runner import (
    LABEL_AGENT_READY,
    LANE_EMPIRICAL,
    STATE_IN_PROGRESS,
    STATE_IN_REVIEW,
    Runner,
)

REPO = "bmr070/expfactory"


class FakeHttp:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses = responses or {}
        self.posts: list[tuple[str, dict]] = []
        self.deletes: list[str] = []

    def get(self, path: str):
        # Longest prefix first. "/repos/x/issues" also prefixes
        # "/repos/x/issues/1/timeline", so insertion order would silently serve
        # the issue list where a timeline was asked for — which is exactly the
        # kind of fake that makes a passing test meaningless.
        for key in sorted(self.responses, key=len, reverse=True):
            if path.startswith(key):
                return self.responses[key]
        return []

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


def _labeled(login: str, name: str = LABEL_AGENT_READY):
    return {"event": "labeled", "label": {"name": name}, "actor": {"login": login}}


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
                _labeled("bmr070"),
                _labeled("expfactory-agent[bot]"),
            ]
        }
    )
    assert GitHubTracker(REPO, http).label_actor("1", LABEL_AGENT_READY) == "expfactory-agent[bot]"


def test_no_timeline_means_no_actor():
    assert GitHubTracker(REPO, FakeHttp()).label_actor("1", LABEL_AGENT_READY) is None


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
        def run(self, ticket):
            from expfactory.verifier import Candidate, GateVerifier

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
            cand = Candidate(hypothesis="h", config={}, code_hash="c", runs=runs, cost_usd=0.1)
            bundle = GateVerifier(id_factory=lambda: "e1").run(cand)
            # A correctly-configured hill-climb session builds its verifier with
            # require_prereg=True, so its verdict carries the G-07/G-08 gates and
            # the runner will accept it. Grafted on here rather than filing a real
            # preregistration: this test is about the GitHub adapter driving the
            # runner, and standing up a ledger would move it to testing G-07.
            # The refusal path itself is covered in tests/test_runner.py.
            import dataclasses

            from expfactory.runner import REQUIRED_EMPIRICAL_GATES

            return dataclasses.replace(
                bundle, gate_names=(*bundle.gate_names, *sorted(REQUIRED_EMPIRICAL_GATES))
            )

    result = Runner(tracker, Agent(), human_allowlist=frozenset({"bmr070"})).tick()

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
        def run(self, ticket):
            raise AssertionError("must not dispatch")

    result = Runner(
        GitHubTracker(REPO, http), NeverCalled(), human_allowlist=frozenset({"bmr070"})
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
