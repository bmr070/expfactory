"""
substrate_guard — the same tamper rule, applied to a pull request.

`gate_no_test_tampering` already refuses to promote a *candidate* whose diff
weakens verification. This applies that identical rule to a *pull request*, so
the substrate cannot be edited into the repository either. One rule, two
surfaces, one list.

## Why this is not just CODEOWNERS

CODEOWNERS asks "who authored this, and has a code owner approved it". Two things
make that insufficient here:

1. `enforce_admins` is off — it has to be, or a solo owner deadlocks, since
   GitHub forbids approving your own PR. So CODEOWNERS binds non-admins only.
2. If the agent runtime opens PRs *as the triggering human* — which Open SWE
   deliberately does — then authorship carries no signal at all, and no
   review-based rule can distinguish agent work from the owner's.

This check asks a different question: **what changed?** It does not care who
authored the commit, cannot be satisfied by an approval, and cannot be spoofed by
getting the identity wrong. An agent cannot merge past it because an agent has no
admin override. A human can, deliberately, and the override lands in the PR
timeline.

That is the same reasoning that makes a gate stronger than a review comment.
Deliberately crude: substrate changes are supposed to be rare, and friction on
them is the feature rather than the cost.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence

from expfactory.gates_v1 import DiffEvidence, gate_no_test_tampering


def changed_paths(base: str, head: str = "HEAD") -> list[str]:
    """Files differing between `base` and `head`, via the merge base.

    Three-dot so a stale branch does not report the base's own progress as this
    pull request's changes.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def diff_evidence(base: str, head: str = "HEAD") -> DiffEvidence:
    """Build DiffEvidence for the tamper gate from a git range.

    Only `touched_paths` is populated. The line-level checks — removed
    assertions, added skip markers, lowered coverage floors — are deliberately
    left to the candidate-level gate, where the diff being judged is the one an
    agent proposed rather than the whole branch. Widening this to line content
    would make ordinary refactors trip it and teach everyone to use the override,
    which is how a wall becomes a formality.
    """
    return DiffEvidence(added_lines=[], removed_lines=[], touched_paths=changed_paths(base, head))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main", help="base ref to compare against")
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args(argv)

    result = gate_no_test_tampering(diff_evidence(args.base, args.head))
    if result.passed:
        print("substrate untouched")
        return 0

    print(f"BLOCKED: {result.detail}\n")
    print(
        "This pull request edits the verification substrate — the code that decides\n"
        "whether a result is real. That is never an automatic merge.\n\n"
        "If the change is wrong, drop it. If it is right, a human merges it with an\n"
        "explicit admin override, which is recorded in the PR timeline. There is no\n"
        "approval that satisfies this check, deliberately: an approval-based rule can\n"
        "be defeated by getting authorship wrong, and this one cannot.\n\n"
        "The protected set is _HARNESS_PATHS in src/expfactory/gates_v1.py."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
