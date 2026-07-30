"""The intake vocabulary and the guard that enforces it, pinned.

The dispatch rule has always been *only a human-applied `agent-ready` label is
dispatch-eligible* (invariant 7). BRE-36 adds the half that was missing: the
label must also be applied to something **implementable**.

A `stage:wayfinder` node is a question and a `stage:spec` is a design. An agent
dispatched at either will confidently produce something, which is the failure the
pipeline order exists to prevent. `stage:ticket` is the claim that
`wayfinder → spec → tickets` was actually followed — the engineering equivalent
of G-07 refusing a run whose preregistration came afterwards.

These tests exist because that enforcement lives in a GitHub Actions workflow,
which nothing else in the suite reads. Deleting the second step would silently
restore the old behaviour and every other test would stay green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_LABELS = _ROOT / "provision" / "labels.json"
_GUARD = _ROOT / ".github" / "workflows" / "agent-ready-guard.yml"

_REQUIRED_STAGES = ("stage:wayfinder", "stage:spec", "stage:ticket", "stage:review")
_REQUIRED_LANES = ("lane:empirical", "lane:deterministic")


def _labels() -> list[dict[str, str]]:
    return json.loads(_LABELS.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _guard() -> dict[str, Any]:
    return yaml.safe_load(_GUARD.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _names() -> set[str]:
    return {entry["name"] for entry in _labels()}


@pytest.mark.parametrize("stage", _REQUIRED_STAGES)
def test_every_pipeline_stage_has_a_label(stage: str) -> None:
    assert stage in _names()


@pytest.mark.parametrize("lane", _REQUIRED_LANES)
def test_both_lanes_have_labels(lane: str) -> None:
    """A missing lane is refused rather than defaulted.

    The lane decides which verifier owns the outcome, and defaulting it is how
    the deterministic and empirical lanes get conflated — the failure
    `factory-chart.html` calls the one that sinks this.
    """
    assert lane in _names()


def test_labels_are_unique() -> None:
    """Two entries with one name means the second silently wins on apply."""
    names = [entry["name"] for entry in _labels()]
    assert len(names) == len(set(names)), f"duplicate label names: {names}"


def test_every_label_carries_a_description() -> None:
    """An undescribed label gets guessed at, and this set encodes a trust rule."""
    for entry in _labels():
        assert entry.get("description", "").strip(), f"{entry['name']} has no description"


def test_agent_ready_says_it_is_only_valid_on_a_ticket() -> None:
    """The constraint travels with the label, not only with the workflow.

    Someone reading `labels.json` to set up a new project should learn the rule
    there rather than discovering it when the guard strips their label.
    """
    ready = next(entry for entry in _labels() if entry["name"] == "agent-ready")
    assert "stage:ticket" in ready["description"]


def test_the_guard_still_checks_who_applied_the_label() -> None:
    """Invariant 7's original half. Must survive any edit to the workflow."""
    steps = _guard()["jobs"]["guard"]["steps"]
    assert any("bmr070" in str(step.get("if", "")) for step in steps), (
        "the human-allowlist check is gone from agent-ready-guard.yml"
    )


def test_the_guard_also_checks_what_the_label_was_applied_to() -> None:
    """BRE-36's half.

    Pinned by the `stage:ticket` string in the step body rather than by step
    count, because a renamed or reordered step is fine and a deleted check is
    not.
    """
    steps = _guard()["jobs"]["guard"]["steps"]
    assert any("stage:ticket" in str(step.get("run", "")) for step in steps), (
        "the stage:ticket check is gone from agent-ready-guard.yml — "
        "an agent can now be dispatched at a wayfinder question or a spec"
    )


def test_the_guard_only_fires_on_the_dispatch_label() -> None:
    """Scoped to `agent-ready`; it must not start stripping unrelated labels."""
    assert "agent-ready" in str(_guard()["jobs"]["guard"]["if"])


# --------------------------------------------------------------------------- #
# BRE-43 — four dimensions, exactly one label from each
# --------------------------------------------------------------------------- #
#
# A vocabulary nothing checks is a vocabulary that drifts. The failure this
# guards is the one the ticket named: nine labels in a flat namespace, where
# nothing composes, nothing has transitions, and no rule says which are mutually
# exclusive. Every entry in labels.json carries its `dimension`, so "which axis
# does this belong to" has one answer that lives in the data.

_DIMENSIONS = ("stage", "lane", "category", "state")
_TRIAGE_DOC = _ROOT / "docs" / "agents" / "triage-labels.md"


def _by_dimension() -> dict[str, set[str]]:
    """Axis -> the labels on it.

    `.get` rather than `[...]`, so one undeclared label fails the test that asks
    about undeclared labels instead of raising KeyError through every other test
    in this section. A cascade names the wrong defect.
    """
    out: dict[str, set[str]] = {d: set() for d in _DIMENSIONS}
    for entry in _labels():
        out.setdefault(str(entry.get("dimension")), set()).add(entry["name"])
    return out


def test_every_label_declares_exactly_one_dimension() -> None:
    """The `dimension` key is the whole mechanism. A label without one is a label
    no rule applies to, which is how the tag cloud starts."""
    for entry in _labels():
        assert entry.get("dimension") in _DIMENSIONS, entry["name"]


@pytest.mark.parametrize("dimension", _DIMENSIONS)
def test_each_dimension_is_populated(dimension: str) -> None:
    """An empty axis would let "exactly one from each" be satisfied vacuously."""
    assert len(_by_dimension().get(dimension, set())) >= 2


def test_no_label_appears_in_two_dimensions() -> None:
    """Disjointness is what makes "exactly one from each" a decidable rule.

    A label on two axes would make a ticket carrying it simultaneously compliant
    and in conflict, and nothing downstream could tell which.
    """
    seen: dict[str, str] = {}
    for entry in _labels():
        name, dimension = entry["name"], entry.get("dimension")
        assert name not in seen, f"{name!r} is in both {seen.get(name)!r} and {dimension!r}"
        seen[name] = dimension


def test_the_category_axis_exists() -> None:
    """BRE-43's one genuine gap. The review findings this week were all bugs in
    shipped code while the BRE-2x series were mostly enhancements, and no label
    distinguished them."""
    assert _by_dimension()["category"] == {"bug", "enhancement"}


def test_the_states_the_pr_rule_needs_exist() -> None:
    """`ready-for-human` is the state we had no name for. Without it, step 2 of
    the draft-first rule has nothing to apply."""
    assert {"needs-triage", "needs-info", "ready-for-human"} <= _by_dimension()["state"]


def test_agent_ready_is_a_state_not_a_stage() -> None:
    """It answers "what happens next", not "where in the pipeline". Filing it
    under stage would make it exclusive with `stage:ticket` — the one stage
    invariant 7 requires it to co-occur with."""
    assert "agent-ready" in _by_dimension()["state"]
    assert "agent-ready" not in _by_dimension()["stage"]


def test_hierarchy_did_not_become_labels() -> None:
    """Linear has `project`, `parentId` and `blockedBy`, and BRE-36 uses all
    three. An epic/story pair would be a second copy of that structure, in a
    place that can disagree with the first."""
    for banned in ("epic", "story", "sub-task", "subtask"):
        assert banned not in _names()


def test_activities_did_not_become_labels() -> None:
    """Each of these is already said by an axis that exists: research is
    `stage:wayfinder`, and training and evaluation are what an empirical
    `stage:ticket` does."""
    for banned in ("research", "model-training", "model-evaluation", "training", "evaluation"):
        assert banned not in _names()


def test_the_role_mapping_is_versioned_not_remembered() -> None:
    """Matt Pocock's indirection: roles are canonical, strings are per-repo. Ours
    differ in exactly one place and that place is load-bearing — the role is
    `ready-for-agent`, the string is `agent-ready`, and the string is written
    into invariant 7, the runner and the guard workflow."""
    doc = _TRIAGE_DOC.read_text(encoding="utf-8")

    assert "ready-for-agent" in doc and "agent-ready" in doc
    for name in _names():
        assert name in doc, f"{name!r} is provisioned but absent from triage-labels.md"


def test_the_draft_first_rule_is_written_where_an_agent_will_read_it() -> None:
    """Prose does not ratchet (invariant 8), so this is the weakest form the rule
    takes. It is here because the mechanism is GitHub's draft field, which no
    test in this repo can exercise."""
    for path in (_TRIAGE_DOC, _ROOT / "docs" / "TRACKING.md"):
        text = path.read_text(encoding="utf-8").lower()
        assert "draft" in text
        assert "ready-for-human" in text


def test_every_description_fits_what_github_will_store() -> None:
    """Measured against the live API, not read from docs: `gh label create`
    returned `HTTP 422 description is too long (maximum is 100 characters)` and
    the `agent-ready` entry was 131.

    A provisioning file that cannot be applied is worse than none — the labels
    before it in the list get created, the run stops, and the repo is left
    holding a partial vocabulary that every other test here still calls valid.
    """
    for entry in _labels():
        assert len(entry["description"]) <= 100, f"{entry['name']}: {len(entry['description'])}"
