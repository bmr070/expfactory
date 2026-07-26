"""
gates_v1 — hardenings beyond the prototype's six gates.

The prototype's six gates operate on run *results*. This module adds gates that
operate on everything else the record carries:

- `gate_no_test_tampering` reads the candidate's *diff*, because the most
  dangerous fooling — reaching green by mutilating verification — is invisible
  in the metrics. It is the empirical-lane analogue of the deterministic lane's
  "the agent may not edit the verifier".
- `gate_no_single_seed_dominance` needs no baseline, catching the lucky-seed
  case that the baseline-dependent variance gate misses.
- `gate_no_group_leakage` (G-09) reads *group* membership, because the standard
  leakage check compares sample ids and the most common leak in sensor data has
  disjoint ids by construction. Added from the literature rather than from a
  bug; see the block comment above it.

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
    # Adjudicates whether a hypothesis is attributed to real literature. An agent
    # that can edit `provenance_of` can cite a paper that does not exist. The
    # corpus it reads is deliberately NOT here -- docs/literature/corpus.json is
    # data, so the reading list can grow without an override on the gate layer.
    "literature.py",
    # Imputes the dollar cost that the registry's caps and breaker are checked
    # against. Edit the cost model to zero and every cap passes forever while
    # still looking enforced.
    "local_substrate.py",
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


# --------------------------------------------------------------------------- #
# G-09 — group-level leakage
#
# `gate_no_leakage` compares train and eval *sample ids* and blocks when they
# intersect. That is necessary and it is not sufficient, because the most common
# leak in sensor data has disjoint ids by construction.
#
# EchoHawk (arXiv 2606.29589, June 2026) documents the case in this repository's
# own domain: a widely used public drone-audio dataset ships pre-segmented into
# short clips, so a clip-level split puts adjacent slices of one continuous
# recording on both sides. Every sample id is distinct. `gate_no_leakage` passes.
# The model learns the session -- its background, its microphone, that specific
# airframe -- and the reported number is measuring memorisation.
#
# The size of the effect, from the paper: enforcing recording-session-grouped
# cross-validation drops a random-forest baseline's detection probability at 1%
# false-alarm rate from 0.796 to 0.745. Five points of Pd, invisible to every
# gate the factory had.
#
# This is the ratchet (W-11) applied to a finding from the literature rather than
# from a bug: the reading becomes a mechanism at the cheapest sufficient point.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DatasetGrouping:
    """A declaration that samples in this dataset cluster, and by what.

    **Supplied by the task, never by the candidate.** This is the same rule that
    governs baselines and guardrail thresholds: a property the agent can choose
    not to declare is not a constraint on the agent. It is passed to the verifier
    at construction, alongside `require_prereg`, so a training function cannot
    reach it.
    """

    group_key: str
    rationale: str
    source: str = ""


def _groups(value: Any) -> frozenset[Any] | None:
    """Read a group collection out of `RunResult.extra`, or None if absent.

    Deliberately strict about types. A string is a common and silent mistake --
    it is iterable, so it would be read as a set of characters and would almost
    always look disjoint.
    """
    if value is None:
        return None
    if isinstance(value, str | bytes):
        return None
    try:
        return frozenset(value)
    except TypeError:
        return None


def gate_no_group_leakage(
    exp: Experiment, grouping: DatasetGrouping | None = None, **_: Any
) -> GateResult:
    """Train and eval must be disjoint at the group level, not only per sample.

    Three states, and the middle one is the point:

    - **No grouping declared** -> non-blocking warning. Plenty of data genuinely
      has no group structure, and blocking there would make the gate something
      everyone routes around. The warning still says what was *not* checked, so a
      reader is never told more than was verified.
    - **Grouping declared, run recorded no groups** -> blocks. The task said this
      data clusters; a run that then declines to record which cluster each sample
      came from has not shown its split is clean. Fail-closed, because the
      alternative is that omitting a field is the way to pass.
    - **Groups intersect** -> blocks, naming the shared groups.
    """
    if grouping is None:
        return GateResult(
            "no_group_leakage",
            True,
            "no grouping declared for this task; sample-id disjointness only, "
            "which does not exclude session-level leakage",
            blocking=False,
        )

    shared: set[Any] = set()
    undeclared: list[int] = []
    for r in exp.runs:
        tr = _groups(r.extra.get("train_groups"))
        ev = _groups(r.extra.get("eval_groups"))
        if tr is None or ev is None or not tr or not ev:
            undeclared.append(r.seed)
            continue
        shared |= tr & ev

    if undeclared:
        return GateResult(
            "no_group_leakage",
            False,
            f"task declares grouping by '{grouping.group_key}' ({grouping.rationale}) "
            f"but seeds {sorted(undeclared)} recorded no train/eval group ids, so "
            "disjointness is unproven",
            blocking=True,
        )

    ok = not shared
    sample = sorted(map(str, shared))[:5]
    detail = (
        f"train/eval disjoint by '{grouping.group_key}'"
        if ok
        else f"GROUP LEAK: {len(shared)} shared '{grouping.group_key}' value(s) "
        f"across train and eval, e.g. {sample} — sample ids may be distinct while "
        "the recordings are the same"
    )
    return GateResult("no_group_leakage", ok, detail, blocking=True)
