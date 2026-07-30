"""Regressions for defects found by adversarial review of the 2026-07-30 diff.

Four fresh-context reviewers went at ~7,200 changed lines. Everything here was
**reproduced by execution** before it was fixed, and each test is written so it
fails against the code as it was.

The reviews are the argument for invariant 3 — the reviewer runs in fresh
context, because the author's context rubber-stamps its own reasoning. Every
finding below was in code that shipped green, with tests, written in the same
session that was congratulating itself on verifying by breaking things.
"""

from __future__ import annotations

import dataclasses

import pytest

from expfactory.github_tracker import Page, PageWalkRefused, _next_path
from expfactory.linear_tracker import LinearTracker
from expfactory.linear_tracker import PageWalkRefused as LinearPageWalkRefused
from expfactory.verifier import VerdictBundle

# --------------------------------------------------------------------------- #
# Invariant 1 — `promoted` is derived, never settable
#
# "If a caller can set it, the layer is theatre." It was settable three ways.
# --------------------------------------------------------------------------- #


def _bundle(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "exp_id": "e1",
        "promoted": True,
        "blocked_by": (),
        "config": {},
        "code_hash": "abc",
        "seeds": (0, 1, 2),
        "gate_names": ("no_leakage",),
        "mean_metric": 0.5,
        "cost_usd": 0.0,
        "artifact": {},
    }
    base.update(over)
    return base


def test_a_promoted_verdict_cannot_carry_a_blocking_gate() -> None:
    """The direct construction. Was accepted."""
    with pytest.raises(ValueError, match="contradicts blocked_by"):
        VerdictBundle(**_bundle(promoted=True, blocked_by=("preregistration",)))  # type: ignore[arg-type]


def test_a_blocked_verdict_cannot_claim_it_was_not_promoted_by_nothing() -> None:
    """The other direction. A verdict nothing blocked is promoted, by definition."""
    with pytest.raises(ValueError, match="contradicts blocked_by"):
        VerdictBundle(**_bundle(promoted=False, blocked_by=()))  # type: ignore[arg-type]


def test_replace_cannot_flip_promoted() -> None:
    """`frozen=True` stops mutation after construction; it does not make the
    field derived. `replace` builds a new object and used to accept anything."""
    honest = VerdictBundle(**_bundle(promoted=False, blocked_by=("tamper",)))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="contradicts blocked_by"):
        dataclasses.replace(honest, promoted=True)


def test_a_deserialized_verdict_cannot_smuggle_a_promotion() -> None:
    """The one that matters most.

    `verifier.py`'s docstring anticipates this seam becoming a process boundary
    with bundles arriving as JSON. At that point `from_dict` IS the trust
    boundary and `promoted` is a field the far side writes — invariant 9
    inverted. It used to round-trip a contradiction intact.
    """
    forged = {
        "exp_id": "e1",
        "promoted": True,
        "blocked_by": ["preregistration", "attested_run"],
        "config": {},
        "code_hash": "abc",
        "seeds": [0, 1, 2],
        "gate_names": ["preregistration"],
        "mean_metric": 0.99,
        "cost_usd": 0.0,
        "artifact": {},
    }
    with pytest.raises(ValueError, match="contradicts blocked_by"):
        VerdictBundle.from_dict(forged)


def test_the_honest_shapes_still_construct() -> None:
    """A refusal that refused everything would pass the tests above and be
    useless. Both named constructors must still work."""
    assert VerdictBundle(**_bundle(promoted=True, blocked_by=())).promoted is True  # type: ignore[arg-type]
    assert (
        VerdictBundle(**_bundle(promoted=False, blocked_by=("no_leakage",))).promoted is False  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# The Link header: dropping the host was not enough
# --------------------------------------------------------------------------- #


def test_a_protocol_relative_next_path_is_refused() -> None:
    """The token-exfiltration path.

    `https://api.github.com//evil.example/steal` has netloc `api.github.com` and
    path `//evil.example/steal`. The netloc check saw nothing wrong; the
    surviving path is protocol-relative, so any transport composing it with
    `urljoin` or an httpx `base_url` sends the read — and the `Authorization`
    header bound to it — to `evil.example`. Verified before the fix: it resolved
    to `https://evil.example/steal?x=1`.
    """
    with pytest.raises(PageWalkRefused, match="protocol-relative"):
        _next_path({"Link": '<https://api.github.com//evil.example/steal?x=1>; rel="next"'})


def test_an_upward_traversing_next_path_is_refused() -> None:
    """Stays on-host, so no token leaks, but it can aim page two of one issue's
    timeline at another issue's — and a history assembled from two issues
    answers an authorization question about neither."""
    with pytest.raises(PageWalkRefused, match="traverses upward"):
        _next_path({"Link": '<https://api.github.com/repos/o/r/../../../orgs/x>; rel="next"'})


def test_a_multi_valued_rel_is_still_followed() -> None:
    """`rel` is a token *set* per RFC 8288, and `rel="next last"` is the legal
    form on a final page. Matching it exactly meant the walk read that as "no
    next page" and silently returned a prefix."""
    assert _next_path({"Link": '<https://api.github.com/x?page=2>; rel="next last"'}) == "/x?page=2"


def test_an_ordinary_foreign_host_still_has_its_host_dropped() -> None:
    """The documented behaviour must survive the new refusals."""
    assert _next_path({"Link": '<https://evil.example/x?page=2>; rel="next"'}) == "/x?page=2"


def test_a_page_cannot_be_built_without_headers() -> None:
    """`headers` defaulted to `{}`, which preserved exactly the omission the type
    was introduced to make impossible: `Page(json.loads(body))` type-checks,
    satisfies the protocol, and silently makes every read one page deep."""
    with pytest.raises(TypeError):
        Page([])  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Linear: cannot-locate is not empty
# --------------------------------------------------------------------------- #


class _TwoPages:
    """Page one well-formed with `hasNextPage`; page two unlocatable.

    `{"data": {"issue": null}}` with no `errors` key is what a partial GraphQL
    failure looks like, and `_call` does not raise on it.
    """

    def __init__(self) -> None:
        self.calls = 0

    def query(self, document: str, variables: dict[str, object]) -> dict[str, object]:
        if "labels" in document:
            # Complete and well formed: `label_actor` resolves the label id here
            # first, and an empty answer would short-circuit before the history
            # walk this test is about.
            return {
                "data": {
                    "issue": {
                        "labels": {
                            "nodes": [{"id": "L1", "name": "agent-ready"}],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        if "history" not in document:
            return {"data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
        self.calls += 1
        if self.calls == 1:
            return {
                "data": {
                    "issue": {
                        "history": {
                            "nodes": [
                                {
                                    "createdAt": "2026-07-01T00:00:01.000Z",
                                    "actor": {"name": "Brett R", "displayName": "Brett R"},
                                    "botActor": None,
                                    "addedLabelIds": ["L1"],
                                }
                            ],
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                        }
                    }
                }
            }
        return {"data": {"issue": None}}


def test_an_unlocatable_connection_is_refused_not_treated_as_empty() -> None:
    """The live fail-open, and the one this module's docstring already claimed
    was closed.

    The walk used to degrade an unlocatable connection to `{}`, which made
    `nodes` empty, `pageInfo` empty, `hasNextPage` falsy — and then *returned the
    pages it already had*. Reproduced before the fix: `label_actor` answered
    `'Brett R'` for a ticket whose most recent grant was a bot's, because the
    page carrying the bot's re-application was the one that went missing.

    A superseded grant read as current is an authorization decision made on
    partial data, which is the whole failure the pagination work exists to
    remove.
    """
    tracker = LinearTracker("BRE", _TwoPages())
    with pytest.raises(LinearPageWalkRefused, match="could not be located"):
        tracker.label_actor("BRE-1", "agent-ready")
