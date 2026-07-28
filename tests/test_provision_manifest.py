"""
The GitHub App manifest grants what it claims, and withholds what it must.

`provision/create-github-app.html` inlines a copy of
`provision/github-app-manifest.json` because a `file://` page cannot fetch its
sibling under most browsers' CORS rules. Two copies of a permission set drift,
and the drifted one still opens a real GitHub page and creates a real App, so the
copy is pinned here rather than trusted.

The load-bearing assertions are the *absences*. `administration` would let the
agent edit branch protection and unlock its own cage; `issues: write` would let
it apply `agent-ready` to a ticket it filed itself, which is the whole dispatch
boundary (invariant 7).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROVISION = Path(__file__).resolve().parent.parent / "provision"
MANIFEST = PROVISION / "github-app-manifest.json"
PAGE = PROVISION / "create-github-app.html"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _inlined() -> dict:
    """The object literal the HTML posts, parsed rather than eyeballed."""
    source = PAGE.read_text(encoding="utf-8")
    body = re.search(r"JSON\.stringify\((\{.*?\})\);", source, re.S)
    assert body, "could not find the inlined manifest in the page"
    # JS object literal -> JSON: quote the bare keys. Anchored to line starts,
    # because an unanchored `(\w+):` also rewrites the `https:` inside the url
    # value and produces something that is not JSON at all. The literal is
    # formatted one key per line, which makes the anchor sufficient.
    return json.loads(re.sub(r"^(\s*)(\w+):", r'\1"\2":', body.group(1), flags=re.M))


def test_both_files_exist():
    """Guards the guard: a missing file would make everything below vacuous."""
    assert MANIFEST.is_file() and PAGE.is_file()


def test_the_page_posts_exactly_what_the_json_declares():
    """The drift check. A stale copy still creates a real App with real
    permissions, so the two must be byte-equivalent as parsed objects."""
    assert _inlined() == _manifest()


def test_administration_is_withheld():
    """The load-bearing absence. An agent that can edit branch protection can
    unlock its own cage, and every other control becomes decorative."""
    assert "administration" not in _manifest()["default_permissions"]


def test_issues_is_read_only_so_the_agent_cannot_label():
    """`agent-ready` is the dispatch boundary. Write access here would let the
    agent promote a ticket it filed itself (invariant 7)."""
    assert _manifest()["default_permissions"]["issues"] == "read"


def test_it_grants_only_what_it_needs():
    """No fifth permission arrives without someone changing this test, which is
    the moment to ask what it is for."""
    assert _manifest()["default_permissions"] == {
        "contents": "write",
        "pull_requests": "write",
        "issues": "read",
        "metadata": "read",
    }


def test_no_webhook_events_are_requested():
    """The runner polls. A webhook subscription would be a second, unpolled
    trigger path nothing in the trust model accounts for."""
    assert _manifest()["default_events"] == []


def test_the_app_is_not_public():
    assert _manifest()["public"] is False


@pytest.mark.parametrize(
    "warning",
    [
        "administration",
        "agent-ready",
        "CODEOWNERS",
    ],
)
def test_the_page_explains_the_dangerous_edits(warning: str):
    """Whoever opens this page is about to grant permissions by clicking a
    button. The reasons have to be on that page, not in a doc they did not open."""
    assert warning in PAGE.read_text(encoding="utf-8")
