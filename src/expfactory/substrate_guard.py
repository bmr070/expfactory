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

from expfactory.gates_v1 import (
    _HARNESS_BASENAMES,
    DiffEvidence,
    _basename,
    gate_no_test_tampering,
)


def changed_paths(base: str, head: str = "HEAD") -> list[str]:
    """Files differing between `base` and `head`, via the merge base.

    Three-dot so a stale branch does not report the base's own progress as this
    pull request's changes.

    **Both sides of a rename, and this is the whole point (BRE-39).** With
    `--name-only`, git prints only the *destination* of a detected rename. Every
    check downstream matches the basename of what git printed against
    `_HARNESS_PATHS`, so the pre-rename name was never in the list — and

        git mv src/expfactory/gates_v1.py src/expfactory/gates.py

    carrying any edit you like inside the moved file produced a **green required
    check that positively asserted the substrate was untouched.** Reproduced
    against the real guard at 57% similarity, so not even a pure rename:

        $ git diff --name-status master...feat
        R057    src/expfactory/verifier.py  src/expfactory/verifier_impl.py
        $ git diff --name-only master...feat
        src/expfactory/verifier_impl.py
        $ python -m expfactory.substrate_guard --base master --head feat
        substrate untouched                                        EXIT=0

    No admin override, no timeline entry. That is the one failure mode this
    design has no second line for: `enforce_admins` is off, CODEOWNERS binds
    non-admins only, and the guard is the control that does not care who authored
    the commit.

    So: `--name-status -M`, and **a rename contributes both paths**. A file that
    leaves the protected set is a change to the protected set.
    """
    out = subprocess.run(
        # -z: NUL-delimited, because a path may legitimately contain whitespace
        # and a rename record is three NUL-separated fields rather than a line.
        ["git", "diff", "--name-status", "-M", "-z", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
        # 0 off Windows; suppresses a console flash on it.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    fields = [f for f in out.stdout.split("\0") if f]
    paths: list[str] = []
    i = 0
    while i < len(fields):
        status = fields[i]
        # R100 / C075 carry a similarity score, and are followed by TWO paths.
        # Everything else (A, M, D, T, U) is followed by one.
        if status[:1] in ("R", "C"):
            paths.extend(fields[i + 1 : i + 3])
            i += 3
        else:
            paths.append(fields[i + 1])
            i += 2
    return paths


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


def touched_protected(paths: Sequence[str]) -> list[str]:
    """The subset of `paths` that is in the protected set, by basename.

    Recomputed from `_HARNESS_PATHS` rather than parsed back out of the gate's
    prose detail. Parse, do not grep — the message is for humans and its wording
    must stay free to change without breaking anything that reads it.

    Uses the gate's own `_basename`/`_HARNESS_BASENAMES`, so this and the verdict
    cannot disagree about what counts as protected (BRE-39). They did: this
    matched case-sensitively on forward slashes only.
    """
    return [p for p in paths if _basename(p) in _HARNESS_BASENAMES]


def protected_diffstat(base: str, head: str, paths: Sequence[str]) -> list[tuple[str, int, int]]:
    """Added/removed line counts for `paths`, as `(path, added, removed)`.

    **This does not change the verdict, and must not.** The gate stays path-based
    on purpose (see `diff_evidence`): judging line content would make ordinary
    refactors trip it and teach everyone to reach for the override, which is how
    a wall becomes a formality.

    It exists because "additions only, nothing removed" and "forty lines deleted
    from `gates_v1.py`" produce the identical red X today, and a human deciding
    whether to spend an admin override should not have to open the diff to tell
    them apart. Reporting is not adjudicating.

    A count of `-1` means git reported `-`, which is a binary file: unknown, and
    said so rather than guessed as zero.
    """
    if not paths:
        return []
    out = subprocess.run(
        ["git", "diff", "--numstat", f"{base}...{head}", "--", *paths],
        capture_output=True,
        text=True,
        check=True,
        # 0 off Windows; suppresses a console flash on it.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    rows: list[tuple[str, int, int]] = []
    for line in out.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        added, removed, path = fields
        rows.append(
            (
                path,
                int(added) if added.isdigit() else -1,
                int(removed) if removed.isdigit() else -1,
            )
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main", help="base ref to compare against")
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args(argv)

    touched = changed_paths(args.base, args.head)
    result = gate_no_test_tampering(diff_evidence(args.base, args.head))
    if result.passed:
        print("substrate untouched")
        return 0

    print(f"BLOCKED: {result.detail}\n")

    # Triage aid only. The verdict above is final and nothing below can move it.
    stat = protected_diffstat(args.base, args.head, touched_protected(touched))
    if stat:
        print("What changed in the protected set:\n")
        for path, added, removed in sorted(stat):
            shape = "binary" if added < 0 or removed < 0 else f"+{added} -{removed}"
            print(f"  {shape:>12}  {path}")
        deletions = sum(r for _, _, r in stat if r > 0)
        print(
            "\n  Additions only - nothing was removed from the protected set.\n"
            if deletions == 0
            else f"\n  {deletions} line(s) removed from the protected set. Read those first.\n"
        )

    print(
        "This pull request edits the verification substrate: the code that decides\n"
        "whether a result is real. That is never an automatic merge.\n\n"
        "If the change is wrong, drop it. If it is right, a human merges it with an\n"
        "explicit admin override, which is recorded in the PR timeline. There is no\n"
        "approval that satisfies this check, deliberately: an approval-based rule can\n"
        "be defeated by getting authorship wrong, and this one cannot.\n\n"
        "  gh pr merge <N> --squash --delete-branch --admin\n\n"
        "This check is REQUIRED and cannot pass while the substrate is touched, so\n"
        "the override is the intended path here rather than a workaround. Additions\n"
        "only is still a substrate change and still needs the deliberate act.\n\n"
        "The protected set is _HARNESS_PATHS in src/expfactory/gates_v1.py."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
