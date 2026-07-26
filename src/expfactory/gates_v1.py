"""
gates_v1 — hardenings added in ticket 03.

The prototype's six gates operate on run *results*. This module adds a gate that
operates on the candidate's *diff*, because the most dangerous fooling — reaching
green by mutilating verification — is invisible in the metrics. It is the
empirical-lane analogue of the deterministic lane's "the agent may not edit the
verifier".

Every gate here traces to a fixture in the ticket-04 suite (standing rule from W-09).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from expfactory.harness import Experiment, GateResult


@dataclass(frozen=True)
class DiffEvidence:
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    touched_paths: list[str] = field(default_factory=list)


# Paths whose modification is inherently suspect: the verification substrate.
_HARNESS_PATHS = (
    "harness.py",
    "gates_v1.py",
    "verifier.py",
    "conftest.py",
    # G-07/G-08 live here, so editing it edits the gate set.
    "prereg.py",
    # The known-answer fixtures are how the harness is judged. An agent that can
    # rewrite the answer key can certify itself.
    "adversarial_suite.py",
    # The holdout lockbox and its durable budget.
    "holdout.py",
    # The boundary test CI runs. Editing it edits what "verified" means.
    "selfcheck.py",
    # Holds the GPU credential and the cost caps; editing it edits the breaker.
    "registry.py",
    # Builds Candidates from train_fn output; editing it can fabricate run records.
    "pipeline.py",
    # Decides what gets dispatched. Editing it edits the trust boundary itself.
    "runner.py",
    # Answers "who applied this label", which is what the trust boundary rests on.
    # Make label_actor always return the owner and eligibility is bypassed wholesale.
    "github_tracker.py",
    # The PR-level wall. Listed here so it guards itself: a change that disables
    # the check cannot merge past the check.
    "substrate_guard.py",
)

# Modules in this package that are deliberately NOT verification substrate.
#
# This package IS the verification layer, so the default is "protected" and the
# exemptions are the thing that needs justifying. A test asserts every module is
# in one list or the other, which makes adding a module force the decision
# instead of relying on someone remembering — prereg.py, selfcheck.py and
# registry.py were each missed on first addition, three for three. Since the
# check went in it has caught runner.py and github_tracker.py automatically.
_NOT_SUBSTRATE = (
    "__init__.py",  # re-exports only; no logic to weaken
)

# Markers that suppress a test rather than fix it.
_SKIP_MARKERS = (
    re.compile(r"@pytest\.mark\.skip"),
    re.compile(r"@pytest\.mark\.xfail"),
    re.compile(r"\bpytest\.skip\("),
)

_ASSERT = re.compile(r"^\s*assert\b")
_COVERAGE_FLOOR = re.compile(r"fail_under\s*=\s*(\d+)")


def _is_test_path(path: str) -> bool:
    p = path.rsplit("/", 1)[-1]
    return p.startswith("test_") or p.endswith("_test.py") or p in ("conftest.py",)


def gate_no_test_tampering(diff: DiffEvidence) -> GateResult:
    """Block a candidate whose diff weakens verification.

    Reasons, any of which blocks:
      - an assertion was removed
      - a skip/xfail marker was added
      - a coverage floor was lowered
      - the harness/verifier itself was edited
    Adding assertions or new tests is explicitly fine.
    """
    reasons: list[str] = []

    # 1. editing the verification substrate itself
    for path in diff.touched_paths:
        if path.rsplit("/", 1)[-1] in _HARNESS_PATHS:
            reasons.append(f"edits the harness ({path})")

    # 2. removed assertions (from any test file)
    for line in diff.removed_lines:
        if _ASSERT.match(line):
            reasons.append("removes an assertion")
            break

    # 3. added skip/xfail markers
    for line in diff.added_lines:
        if any(m.search(line) for m in _SKIP_MARKERS):
            reasons.append("adds a skip/xfail marker")
            break

    # 4. lowered coverage floor
    old_floor = _floor(diff.removed_lines)
    new_floor = _floor(diff.added_lines)
    if old_floor is not None and new_floor is not None and new_floor < old_floor:
        reasons.append(f"lowers coverage floor {old_floor}->{new_floor}")

    ok = not reasons
    detail = "no verification weakening" if ok else "TAMPER: " + "; ".join(reasons)
    return GateResult("no_test_tampering", ok, detail, blocking=True)


def _floor(lines: list[str]) -> int | None:
    for line in lines:
        m = _COVERAGE_FLOOR.search(line)
        if m:
            return int(m.group(1))
    return None


# --------------------------------------------------------------------------- #
# Baseline-free single-seed-dominance gate (ticket 03 recalibration)
# --------------------------------------------------------------------------- #


def gate_no_single_seed_dominance(exp: Experiment, dominance: float = 0.5, **_: Any) -> GateResult:
    """Reject a candidate whose apparent performance rests on one lucky seed.

    The prototype's seed_variance gate needs a baseline; with none, a single-seed
    spike promoted. This gate needs no baseline: if removing the single best seed
    collapses the mean by more than `dominance` of the gap between best and rest,
    the result is a seed lottery, not a finding.

    Calibrated against the ticket-04 fixtures rather than a hand-picked delta.
    """
    vals = sorted((r.val_metric for r in exp.runs), reverse=True)
    if len(vals) < 3:
        return GateResult(
            "no_single_seed_dominance", False, "need >=3 seeds to judge dominance", blocking=True
        )
    n = len(vals)
    best, rest = vals[0], vals[1:]
    rest_mean = sum(rest) / len(rest)
    full_mean = sum(vals) / n
    lift = full_mean - rest_mean  # how much the full mean sits above the rest
    # the single best seed contributes exactly (best - rest_mean)/n to the full mean
    single_seed_contrib = (best - rest_mean) / n
    frac = (single_seed_contrib / lift) if lift > 1e-12 else 0.0
    dominated = lift > 1e-9 and frac > dominance
    ok = not dominated
    detail = (
        "balanced across seeds"
        if ok
        else f"SEED LOTTERY: one seed ({best:.3f}) accounts for {frac:.0%} of the "
        f"lift over rest-mean {rest_mean:.3f}"
    )
    return GateResult("no_single_seed_dominance", ok, detail, blocking=True)
