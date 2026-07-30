"""
review_fleet — which reviewers a pull request gets, decided by code.

BRE-35, advisory half. The fleet runs linters and multiple LLM reviewers on every
pull request and posts findings. **It has no merge authority and this module
grants none.** There is no function here that returns a merge decision, and
`test_review_fleet.py` asserts that by inspecting the module rather than trusting
this sentence.

## Why the router is a table and not a prompt

The latent/deterministic split, applied to the fleet itself. *"Which reviewers
should run on a PR touching `gates_v1.py`?"* has the same correct answer every
time, so it is a config lookup. *"Is this change correct?"* is judgment, and that
is what the reviewer is for.

An LLM deciding which reviewers to skip is an LLM deciding what does not get
checked. That is the merge-key problem wearing a different hat, and it is why the
routing lives in `_ROUTES` below where a diff shows a change to it.

## Why this module is verification substrate

It is in `_HARNESS_PATHS`. Not because it adjudicates a result — it does not —
but because it decides *what gets looked at*. An agent able to edit the routing
table could quietly remove the adversarial lens from harness changes, and the
fleet would still report a clean run. That is the same shape as a gate that
cannot fail: the output looks identical whether the check happened or not.

## The two rules that keep it from becoming decoration

1. **The protected-path lens is not skippable by config.** If a PR touches
   `_HARNESS_PATHS`, `ADVERSARIAL` runs whatever the table says. Otherwise the
   table becomes the place to disable the check that matters most.
2. **A router miss fails toward more review.** An unmatched path gets the full
   default set rather than nothing, for the same reason an unreadable ledger
   means spend is unknown rather than zero.

## Upstream, for the record

`docs/research/pr-review-and-merge-2026.md` surveyed this question. Nobody lets a
language model decide whether a PR may merge: gh-aw's ten gates are all
deterministic and its `submit-pull-request-review` defaults to `COMMENT`,
Factory's action reviews and never approves, Open SWE opens draft PRs. Factory
also ships its lenses as three distinct behaviours rather than one "review this"
prompt, which is the multi-lens shape below.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from expfactory.gates_v1 import _HARNESS_BASENAMES, _basename


@dataclass(frozen=True)
class Lens:
    """One reviewer, with the question it is pointed at.

    Frozen because a lens whose prompt can be edited at runtime is a lens whose
    findings mean whatever the caller wanted them to mean.

    `agent` names a reviewer definition rather than embedding a prompt, so the
    lenses stay portable across projects while the routing stays local. That is
    the same split skills already use here.
    """

    name: str
    agent: str
    asks: str


# The catalog. Distinct lenses rather than N copies of one general reviewer:
# perspective diversity catches failure modes redundancy cannot, and a reviewer
# that always fires teaches people to skim it.
ADVERSARIAL: Final = Lens(
    "adversarial",
    "pr-review-toolkit:silent-failure-hunter",
    "how would this change let a fake result through, or let a real one be refused?",
)
CORRECTNESS: Final = Lens(
    "correctness",
    "pr-review-toolkit:code-reviewer",
    "does this do what the ticket said, and where would it break?",
)
SILENT_FAILURE: Final = Lens(
    "silent-failure",
    "pr-review-toolkit:silent-failure-hunter",
    "what error is swallowed, defaulted, or reported as success here?",
)
TYPE_DESIGN: Final = Lens(
    "type-design",
    "pr-review-toolkit:type-design-analyzer",
    "does the type make the invalid state unrepresentable, or merely unlikely?",
)
TEST_COVERAGE: Final = Lens(
    "test-coverage",
    "pr-review-toolkit:pr-test-analyzer",
    "does a test here fail if the behaviour regresses, or only if it crashes?",
)
TEST_INTEGRITY: Final = Lens(
    "test-integrity",
    "pr-review-toolkit:pr-test-analyzer",
    "was an assertion weakened, removed, or made unreachable?",
)
CLAIM_ACCURACY: Final = Lens(
    "claim-accuracy",
    "pr-review-toolkit:comment-analyzer",
    "does the prose still match the code it describes?",
)
PERMISSIONS: Final = Lens(
    "permissions",
    "security-scan",
    "what can this change reach that it could not reach before?",
)
SUPPLY_CHAIN: Final = Lens(
    "supply-chain",
    "security-scan",
    "is every new dependency pinned, and does anything fetch at run time?",
)

ALL_LENSES: Final = (
    ADVERSARIAL,
    CORRECTNESS,
    SILENT_FAILURE,
    TYPE_DESIGN,
    TEST_COVERAGE,
    TEST_INTEGRITY,
    CLAIM_ACCURACY,
    PERMISSIONS,
    SUPPLY_CHAIN,
)

# Routing. First match does not win — every matching row contributes, because a
# file can be several things at once and the union is the honest answer.
_ROUTES: Final[tuple[tuple[str, tuple[Lens, ...]], ...]] = (
    ("src/**/*.py", (CORRECTNESS, SILENT_FAILURE, TYPE_DESIGN, TEST_COVERAGE)),
    ("tests/**/*.py", (TEST_INTEGRITY, TEST_COVERAGE)),
    ("examples/**/*.py", (CORRECTNESS, CLAIM_ACCURACY)),
    ("docs/**", (CLAIM_ACCURACY,)),
    ("*.md", (CLAIM_ACCURACY,)),
    (".github/**", (PERMISSIONS, TEST_INTEGRITY)),
    ("provision/**", (PERMISSIONS,)),
    ("pyproject.toml", (SUPPLY_CHAIN, TEST_INTEGRITY)),
    ("*.lock", (SUPPLY_CHAIN,)),
)

# A path nothing matches gets everything. Failing toward more review costs
# attention; failing toward less costs a missed defect, and only one of those is
# recoverable after the fact.
DEFAULT_LENSES: Final = ALL_LENSES


def touches_protected(paths: tuple[str, ...]) -> bool:
    """Whether any path is in the harness protected set, by basename.

    Same basename rule the tamper gate and `substrate_guard` use, read from the
    same constant, so the three cannot drift into disagreeing about what counts
    as the substrate.

    They had drifted anyway, because "same rule" was three copies of one
    expression rather than one function (BRE-39). All three matched
    case-sensitively on forward slashes only, so a renamed or differently-cased
    protected module dropped the adversarial lens here at the same moment it
    stopped tripping the guard — the two controls failing together, for one
    reason, which is exactly what having two is supposed to prevent.
    """
    return any(_basename(p) in _HARNESS_BASENAMES for p in paths)


def _matches(path: str, pattern: str) -> bool:
    """Glob match that treats `**` as crossing separators.

    `fnmatch` does not distinguish `*` from `**`, so `src/*.py` would match
    `src/a/b.py`. Normalising `**/` to `*` and comparing the whole path keeps the
    patterns above readable while still matching what they appear to say.
    """
    return fnmatch.fnmatch(path, pattern.replace("**/", "*")) or fnmatch.fnmatch(path, pattern)


def lenses_for(paths: tuple[str, ...]) -> tuple[Lens, ...]:
    """The reviewers this changeset gets. Pure, total, and order-stable.

    Pure so the same PR always draws the same fleet: a router that varied would
    make "the fleet found nothing" unfalsifiable. Order-stable so a diff of two
    runs is readable.

    An empty changeset gets nothing — there is no code to review, and running the
    full set on it would be the noise that teaches people to ignore findings.
    """
    if not paths:
        return ()

    selected: dict[str, Lens] = {}
    matched_any = False
    for path in paths:
        for pattern, lenses in _ROUTES:
            if _matches(path, pattern):
                matched_any = True
                for lens in lenses:
                    selected[lens.name] = lens

    if not matched_any:
        selected = {lens.name: lens for lens in DEFAULT_LENSES}

    # Not skippable by config, and applied after the table rather than inside it
    # so no edit to `_ROUTES` can remove it. A harness change gets the
    # adversarial lens even if every other row is deleted.
    if touches_protected(paths):
        selected[ADVERSARIAL.name] = ADVERSARIAL

    return tuple(lens for lens in ALL_LENSES if lens.name in selected)


def plan(paths: tuple[str, ...]) -> str:
    """A human-readable routing decision, for the workflow log.

    Printed before the reviewers run so the record shows what was asked as well
    as what came back. A finding nobody can trace to a question is hard to act
    on, and a lens that silently did not run is worse.
    """
    chosen = lenses_for(paths)
    if not chosen:
        return "no files changed; no reviewers scheduled"

    lines = [f"{len(chosen)} lens(es) for {len(paths)} changed file(s):"]
    lines += [f"  {lens.name:<16} {lens.asks}" for lens in chosen]
    if touches_protected(paths):
        lines.append("")
        lines.append(
            "  This changeset touches the harness protected set, so the adversarial\n"
            "  lens is included and cannot be routed away. Findings are advisory:\n"
            "  substrate-guard still refuses the merge and a human still overrides it."
        )
    return "\n".join(lines)


def changed_paths(base: str, head: str = "HEAD") -> tuple[str, ...]:
    """Files differing between `base` and `head`, via the merge base.

    Three-dot, matching `substrate_guard.changed_paths`, so a stale branch does
    not draw reviewers for the base's own progress. Duplicated deliberately
    rather than imported: this module must not import the guard, or a future
    change here could reach the wall.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
        # 0 off Windows; suppresses a console flash on it.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return tuple(line.strip() for line in out.stdout.splitlines() if line.strip())


def main(argv: Sequence[str] | None = None) -> int:
    """Print the routing decision. **Always exits 0.**

    Advisory by construction: an exit code is a gate, and this module is not one.
    A fleet that could fail a build would be a reviewer able to block a merge,
    which is the authority `docs/research/pr-review-and-merge-2026.md` found
    nobody upstream grants and invariant 2 forbids.

    The deterministic half runs today. The LLM lenses attach where the plan is
    printed, and need an agent identity that is not the owner's — BRE-18. Until
    then this reports what *would* be asked, which is the shadow mode BRE-35 asks
    for before any judge is trusted with influence.
    """
    ap = argparse.ArgumentParser(description="Which reviewers this changeset draws.")
    ap.add_argument("--base", default="origin/main", help="base ref to compare against")
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args(argv)

    paths = changed_paths(args.base, args.head)
    print(plan(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
