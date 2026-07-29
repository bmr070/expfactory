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
