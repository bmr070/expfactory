"""
The PR-level substrate guard.

The property worth testing is that it keys on *what changed* and nothing else —
no author, no approval, no label. That is the whole reason it survives the agent
identity being wrong, which is the failure CODEOWNERS cannot survive.

Driven through real git repositories rather than mocks: the thing being tested is
partly "does the diff range do what we think", and a mock would assert my
assumption about `git diff` rather than git's behaviour.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from expfactory.gates_v1 import _HARNESS_PATHS
from expfactory.substrate_guard import (
    changed_paths,
    diff_evidence,
    main,
    protected_diffstat,
    touched_protected,
)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    return repo


def _branch_touching(repo: Path, path: str) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "change")


def test_changed_paths_uses_the_merge_base(tmp_path: Path, monkeypatch):
    """Three-dot, so a stale branch does not report the base's own progress as
    this pull request's changes."""
    repo = _repo(tmp_path)
    _branch_touching(repo, "docs/notes.md")
    # base moves on after the branch was cut
    _git(repo, "checkout", "-q", "main")
    (repo / "unrelated.md").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base moves")
    _git(repo, "checkout", "-q", "feature")

    monkeypatch.chdir(repo)
    assert changed_paths("main") == ["docs/notes.md"]


def test_an_ordinary_change_passes(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    _branch_touching(repo, "docs/notes.md")
    monkeypatch.chdir(repo)
    assert main(["--base", "main"]) == 0


def test_touching_the_substrate_blocks(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    _branch_touching(repo, "src/expfactory/verifier.py")
    monkeypatch.chdir(repo)
    assert main(["--base", "main"]) == 1


def test_it_blocks_regardless_of_who_authored_the_commit(tmp_path: Path, monkeypatch):
    """The point of the whole exercise.

    CODEOWNERS asks who authored this and whether an owner approved. Both
    questions are unanswerable if the agent runtime opens PRs as the triggering
    human. This check never asks.
    """
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "src" / "expfactory").mkdir(parents=True)
    (repo / "src" / "expfactory" / "prereg.py").write_text("weakened\n")
    _git(repo, "add", "-A")
    # authored as the repository owner, which is exactly the impersonation case
    _git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "-c",
        "user.name=bmr070",
        "-c",
        "user.email=bmr070@users.noreply.github.com",
        "commit",
        "-q",
        "-m",
        "looks like a human wrote it",
    )
    monkeypatch.chdir(repo)
    assert main(["--base", "main"]) == 1


def test_every_protected_module_is_actually_caught(tmp_path: Path, monkeypatch):
    """Derived from _HARNESS_PATHS rather than a hand-copied list, so a module
    added to the protected set is covered here without anyone remembering."""
    for module in _HARNESS_PATHS:
        if module == "conftest.py":
            continue  # not a shipped module
        repo = _repo(tmp_path / module.replace(".", "_"))
        _branch_touching(repo, f"src/expfactory/{module}")
        monkeypatch.chdir(repo)
        assert main(["--base", "main"]) == 1, f"{module} is protected but was not caught"


def test_the_guard_guards_itself(tmp_path: Path, monkeypatch):
    """substrate_guard.py is itself in the protected set, so a PR that disables
    the check cannot merge past the check."""
    assert "substrate_guard.py" in _HARNESS_PATHS
    repo = _repo(tmp_path)
    _branch_touching(repo, "src/expfactory/substrate_guard.py")
    monkeypatch.chdir(repo)
    assert main(["--base", "main"]) == 1


def test_diff_evidence_carries_paths_only(tmp_path: Path, monkeypatch):
    """Line-level checks stay with the candidate gate. Widening this to line
    content would trip ordinary refactors and teach everyone to reach for the
    override, which is how a wall becomes a formality."""
    repo = _repo(tmp_path)
    _branch_touching(repo, "docs/notes.md")
    monkeypatch.chdir(repo)
    ev = diff_evidence("main")
    assert ev.touched_paths == ["docs/notes.md"]
    assert ev.added_lines == [] and ev.removed_lines == []


def test_the_blocking_message_is_ascii_only(tmp_path: Path, monkeypatch, capsys):
    """A blocking message that renders as mojibake reads like a broken tool
    rather than a considered refusal, and this runs on Windows consoles where
    cp1252 mangles anything above U+007F."""
    repo = _repo(tmp_path)
    _branch_touching(repo, "src/expfactory/verifier.py")
    monkeypatch.chdir(repo)
    main(["--base", "main"])

    out = capsys.readouterr().out
    offenders = sorted({f"U+{ord(c):04X}" for c in out if ord(c) > 127})
    assert not offenders, f"non-ASCII in blocking output: {offenders}"


# ---------------------------------------------------------------------------
# The triage report (BRE-34). Reporting is not adjudicating: every test below
# asserts the verdict is untouched by what the report says.
# ---------------------------------------------------------------------------


def test_touched_protected_keeps_only_the_protected_paths():
    """Filters by basename, matching how the gate itself decides."""
    got = touched_protected(
        ["docs/notes.md", "src/expfactory/verifier.py", "tests/test_x.py", "README.md"]
    )
    assert got == ["src/expfactory/verifier.py"]


def test_touched_protected_is_derived_not_hand_listed():
    """A module added to _HARNESS_PATHS is covered without anyone remembering."""
    for module in _HARNESS_PATHS:
        assert touched_protected([f"src/expfactory/{module}"]) == [f"src/expfactory/{module}"]


def test_the_diffstat_separates_additions_from_deletions(tmp_path: Path, monkeypatch):
    """The distinction the report exists to surface.

    An additions-only change to the harness and one that deletes forty lines
    from it produce the same red X. A human spending an admin override should be
    able to tell them apart without opening the diff.
    """
    repo = _repo(tmp_path)
    target = repo / "src" / "expfactory" / "verifier.py"
    target.parent.mkdir(parents=True)
    target.write_text("one\ntwo\nthree\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")

    _git(repo, "checkout", "-q", "-b", "feature")
    target.write_text("one\ntwo\nthree\nfour\n")  # pure addition
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add a line")

    monkeypatch.chdir(repo)
    stat = protected_diffstat("main", "HEAD", ["src/expfactory/verifier.py"])
    assert stat == [("src/expfactory/verifier.py", 1, 0)]


def test_the_diffstat_reports_deletions(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    target = repo / "src" / "expfactory" / "gates_v1.py"
    target.parent.mkdir(parents=True)
    target.write_text("one\ntwo\nthree\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")

    _git(repo, "checkout", "-q", "-b", "feature")
    target.write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "remove two lines")

    monkeypatch.chdir(repo)
    stat = protected_diffstat("main", "HEAD", ["src/expfactory/gates_v1.py"])
    assert stat == [("src/expfactory/gates_v1.py", 0, 2)]


def test_the_diffstat_asks_git_for_nothing_when_given_nothing():
    """An empty path list must not become `git diff -- ` over the whole tree."""
    assert protected_diffstat("main", "HEAD", []) == []


def test_an_additions_only_substrate_change_still_blocks(tmp_path: Path, monkeypatch, capsys):
    """The load-bearing one.

    The report says "additions only", and the verdict is still BLOCKED. If this
    ever inverts, the report has started adjudicating and the wall is gone.
    """
    repo = _repo(tmp_path)
    target = repo / "src" / "expfactory" / "verifier.py"
    target.parent.mkdir(parents=True)
    target.write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")

    _git(repo, "checkout", "-q", "-b", "feature")
    target.write_text("one\ntwo\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "pure addition")

    monkeypatch.chdir(repo)
    assert main(["--base", "main"]) == 1
    out = capsys.readouterr().out
    assert "Additions only" in out
    assert "BLOCKED" in out


def test_the_report_names_the_override_command(tmp_path: Path, monkeypatch, capsys):
    """The check can never pass on a substrate PR, so the message must say what
    the actual path forward is rather than leaving the reader to infer it."""
    repo = _repo(tmp_path)
    _branch_touching(repo, "src/expfactory/holdout.py")
    monkeypatch.chdir(repo)
    assert main(["--base", "main"]) == 1
    assert "--admin" in capsys.readouterr().out


def test_a_clean_pr_prints_no_triage_block(tmp_path: Path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    _branch_touching(repo, "docs/notes.md")
    monkeypatch.chdir(repo)
    assert main(["--base", "main"]) == 0
    assert "What changed in the protected set" not in capsys.readouterr().out
