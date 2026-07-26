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
from expfactory.substrate_guard import changed_paths, diff_evidence, main


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
