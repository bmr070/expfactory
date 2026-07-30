"""BRE-35 — the router decides what gets reviewed, and it has no merge authority.

Two properties matter here and the rest is detail.

**The fleet cannot decide a merge.** `docs/research/pr-review-and-merge-2026.md`
found that nobody upstream lets a language model make that call, and invariant 2
forbids a reviewer overriding a blocking gate. A test that reads the module and
refuses a merge-shaped function is cheap; a comment promising the same thing is
worth nothing.

**The adversarial lens cannot be routed away from a harness change.** If it could,
`_ROUTES` becomes the place to quietly disable the check that matters most, and a
clean fleet run would mean the same thing whether it looked or not.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from expfactory import review_fleet
from expfactory.gates_v1 import _HARNESS_PATHS
from expfactory.review_fleet import (
    ADVERSARIAL,
    ALL_LENSES,
    DEFAULT_LENSES,
    lenses_for,
    plan,
    touches_protected,
)


def _names(paths: tuple[str, ...]) -> set[str]:
    return {lens.name for lens in lenses_for(paths)}


# ---------------------------------------------------------------------------
# It cannot decide a merge
# ---------------------------------------------------------------------------


def test_the_module_exposes_no_merge_decision() -> None:
    """Read the module, do not trust its docstring.

    Parse the AST rather than grep the source: a grep for "merge" matches this
    module's own prose about not merging, which is exactly the mistake
    `AGENTS.md` records having made twice.
    """
    tree = ast.parse(Path(inspect.getfile(review_fleet)).read_text(encoding="utf-8"))
    public = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    ]
    forbidden = [n for n in public if any(w in n.lower() for w in ("merge", "approve", "promote"))]
    assert not forbidden, (
        f"review_fleet exposes {forbidden}. The fleet advises; deterministic policy "
        "decides. A reviewer may never override a blocking gate (invariant 2)."
    )


def test_it_returns_lenses_not_verdicts() -> None:
    """Every public entry point answers *who should look*, never *is it ok*."""
    for paths in [("src/expfactory/verifier.py",), ("docs/x.md",), ()]:
        result = lenses_for(paths)
        assert all(isinstance(lens, type(ADVERSARIAL)) for lens in result)
        assert not any(isinstance(lens, bool) for lens in result)


# ---------------------------------------------------------------------------
# The adversarial lens is not skippable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [m for m in _HARNESS_PATHS if m != "conftest.py"])
def test_every_protected_module_draws_the_adversarial_lens(module: str) -> None:
    """Derived from `_HARNESS_PATHS`, so a module added to the protected set is
    covered here without anyone remembering to update a list."""
    assert ADVERSARIAL.name in _names((f"src/expfactory/{module}",))


def test_the_adversarial_lens_survives_an_emptied_routing_table(monkeypatch) -> None:
    """The rule that keeps the table from becoming a place to disable the check.

    Delete every route and a harness change still draws the adversarial lens,
    because it is applied after the table rather than by it.
    """
    monkeypatch.setattr(review_fleet, "_ROUTES", ())
    assert ADVERSARIAL.name in _names(("src/expfactory/gates_v1.py",))


def test_a_harness_path_among_many_still_triggers_it() -> None:
    """One protected file in a large docs PR is still a substrate change."""
    paths = ("docs/a.md", "docs/b.md", "README.md", "src/expfactory/holdout.py")
    assert ADVERSARIAL.name in _names(paths)


def test_touches_protected_reads_the_same_constant_as_the_gate() -> None:
    """Three components decide what counts as substrate — this, the tamper gate,
    and substrate_guard. They must not drift into disagreeing."""
    for module in _HARNESS_PATHS:
        assert touches_protected((f"src/expfactory/{module}",))
    assert not touches_protected(("docs/notes.md", "README.md"))


# ---------------------------------------------------------------------------
# A miss fails toward more review
# ---------------------------------------------------------------------------


def test_an_unmatched_path_gets_the_full_set() -> None:
    """Failing toward more review costs attention; failing toward less costs a
    missed defect, and only one of those is recoverable after the fact."""
    assert _names(("some/unknown/place/thing.rb",)) == {lens.name for lens in DEFAULT_LENSES}


def test_an_empty_changeset_schedules_nothing() -> None:
    """No code to review. Running the full set on nothing is the noise that
    teaches people to ignore findings."""
    assert lenses_for(()) == ()


# ---------------------------------------------------------------------------
# Routing behaviour
# ---------------------------------------------------------------------------


def test_a_docs_only_change_does_not_draw_code_lenses() -> None:
    got = _names(("docs/TRACKING.md", "README.md"))
    assert got == {"claim-accuracy"}


def test_source_draws_the_code_lenses() -> None:
    got = _names(("src/expfactory/drone_audio.py",))
    assert {"correctness", "silent-failure", "type-design", "test-coverage"} <= got


def test_a_test_change_is_checked_for_weakened_assertions() -> None:
    """The failure `gate_no_test_tampering` exists for, asked of a human too."""
    assert "test-integrity" in _names(("tests/test_drone_audio.py",))


def test_workflow_changes_draw_permissions() -> None:
    assert "permissions" in _names((".github/workflows/ci.yml",))


def test_a_manifest_change_draws_supply_chain() -> None:
    assert "supply-chain" in _names(("pyproject.toml",))


def test_the_union_of_every_matching_row_is_taken() -> None:
    """A mixed PR gets every lens its files earn, not just the first match."""
    got = _names(("src/expfactory/scorer.py", "tests/test_scorer.py", "docs/SPEC.md"))
    assert {"correctness", "test-integrity", "claim-accuracy"} <= got


def test_a_star_pattern_does_not_cross_directories_by_accident() -> None:
    """`fnmatch` does not distinguish `*` from `**`, so this is worth pinning:
    a nested source file must still match the `src/**/*.py` row."""
    assert "correctness" in _names(("src/expfactory/deep/nested/thing.py",))


# ---------------------------------------------------------------------------
# Determinism, so "the fleet found nothing" stays falsifiable
# ---------------------------------------------------------------------------


def test_routing_is_deterministic() -> None:
    paths = ("src/expfactory/runner.py", "docs/x.md", "pyproject.toml")
    assert lenses_for(paths) == lenses_for(paths)


def test_routing_is_order_independent() -> None:
    """A PR's lens set must not depend on the order git happened to list files."""
    a = ("src/expfactory/runner.py", "docs/x.md")
    assert lenses_for(a) == lenses_for(tuple(reversed(a)))


def test_output_order_is_stable_for_readable_diffs() -> None:
    got = lenses_for(("src/expfactory/runner.py", "tests/test_runner.py"))
    order = [lens.name for lens in ALL_LENSES]
    assert [lens.name for lens in got] == [n for n in order if n in {x.name for x in got}]


def test_every_lens_states_what_it_asks() -> None:
    """A finding nobody can trace to a question is hard to act on."""
    for lens in ALL_LENSES:
        assert lens.asks.strip(), f"{lens.name} does not say what it asks"
        assert lens.agent.strip(), f"{lens.name} names no reviewer"


def test_lens_names_are_unique() -> None:
    names = [lens.name for lens in ALL_LENSES]
    assert len(names) == len(set(names))


def test_a_lens_cannot_be_mutated_after_construction() -> None:
    """A lens whose question can be edited at runtime produces findings that mean
    whatever the caller wanted them to mean."""
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass raises FrozenInstanceError
        ADVERSARIAL.asks = "anything at all"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The plan is printed, so a lens that did not run is visible
# ---------------------------------------------------------------------------


def test_the_plan_names_every_scheduled_lens() -> None:
    paths = ("src/expfactory/verifier.py",)
    text = plan(paths)
    for lens in lenses_for(paths):
        assert lens.name in text


def test_the_plan_says_findings_are_advisory_on_a_harness_change() -> None:
    """The reader of a fleet report on a substrate PR must not conclude that a
    clean report means it can merge. substrate-guard still refuses."""
    text = plan(("src/expfactory/gates_v1.py",))
    assert "advisory" in text.lower()
    assert "substrate-guard" in text


def test_the_plan_says_so_when_nothing_is_scheduled() -> None:
    assert "no reviewers" in plan(())
