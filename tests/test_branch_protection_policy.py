"""The declared merge policy and the docs that describe it cannot disagree.

## Why this test exists

On 2026-07-28 `docs/DISPATCH-READINESS.md` §2 listed the required status checks
as `check (3.11)` and `check (3.13)`. The live setting also required
`substrate-guard`. Nobody noticed, because the settings lived only in the GitHub
UI: no reviewer sees them, no diff shows a change, and the doc was the only
artifact anyone read.

The consequence was not cosmetic. Omitting `substrate-guard` hides the fact that
a harness PR has **no normal merge path at all**, which is the single most
surprising property of this repo's workflow.

gh-aw's ADR-27193 rejected exactly this arrangement when it declined "rely on
branch-protection settings" as making policy *"invisible to code reviewers and
hard to version-control."* `provision/branch-protection.json` is the fix: the
policy is now a file. This test is the ratchet that keeps the prose honest about
it (invariant 8 — prose does not ratchet, so a check does).

## What it does not do

It does **not** verify the live GitHub settings. That needs network and auth, so
it would be flaky in CI and unavailable offline. Checking the file against
reality stays a manual step, documented in the JSON itself.

What it can do for free is make the *declared* policy and the *documented*
policy provably identical, which is the half that silently drifted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_POLICY = _ROOT / "provision" / "branch-protection.json"
_READINESS = _ROOT / "docs" / "DISPATCH-READINESS.md"

# The bullet in §2 that enumerates the required checks. Anchored on the phrase
# rather than a line number so reordering the section does not break the test.
_CHECKS_BULLET = re.compile(r"^-\s*status checks required.*$", re.IGNORECASE | re.MULTILINE)
_CODE_SPAN = re.compile(r"`([^`]+)`")


def _policy() -> dict[str, object]:
    return json.loads(_POLICY.read_text(encoding="utf-8"))


def test_the_policy_file_parses_and_declares_its_repository() -> None:
    p = _policy()
    assert p["repository"] == "bmr070/expfactory"
    assert p["branch"] == "main"


def test_substrate_guard_is_a_required_check() -> None:
    """The specific omission that caused this test to exist.

    Pinned by name rather than by count, because "three checks" would still pass
    if one were swapped for another.
    """
    checks = _policy()["required_status_checks"]
    assert isinstance(checks, dict)
    assert "substrate-guard" in checks["contexts"]


def test_enforce_admins_is_off_and_says_why() -> None:
    """Off is correct here and the reason must travel with it.

    A future reader who flips this to `true` without reading the note deadlocks
    the repo: GitHub forbids self-approval, so a solo owner could never merge.
    """
    p = _policy()
    assert p["enforce_admins"] is False
    assert p["_enforce_admins_why"], "the setting is surprising; it must carry its reason"


def test_the_documented_checks_match_the_declared_checks() -> None:
    """The drift that happened, made structurally impossible.

    Compares the code spans in §2's status-check bullet against the policy file's
    contexts as sets. Adding a required check to one and not the other fails
    here rather than surfacing months later as a confusing red X.
    """
    text = _READINESS.read_text(encoding="utf-8")
    bullets = _CHECKS_BULLET.findall(text)
    assert bullets, (
        "docs/DISPATCH-READINESS.md no longer has a 'status checks required' bullet. "
        "If §2 was restructured, update this test rather than deleting the claim."
    )

    # The bullet may wrap onto a continuation line; take the bullet and the line
    # after it, since a wrapped list item is still one item.
    start = text.index(bullets[0])
    window = text[start : start + len(bullets[0]) + 200].split("\n")[:2]
    documented = set(_CODE_SPAN.findall(" ".join(window)))

    checks = _policy()["required_status_checks"]
    assert isinstance(checks, dict)
    declared = set(checks["contexts"])  # type: ignore[arg-type]

    assert documented == declared, (
        f"docs say {sorted(documented)}, policy says {sorted(declared)}. "
        "These must agree — the whole point of the policy file is that the prose "
        "cannot quietly drift from it."
    )


@pytest.mark.parametrize("key", ["allow_force_pushes", "allow_deletions"])
def test_destructive_operations_stay_disabled(key: str) -> None:
    assert _policy()[key] is False
