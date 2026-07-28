"""
The project skills are well-formed and point at files that exist.

A skill is only useful if Claude Code can (a) find it, which needs valid
frontmatter, and (b) trust it, which needs its references to resolve. A skill
naming `docs/GOTCHAS.md` after someone renames that file is worse than no skill:
it reads as guidance and sends the reader nowhere.

Same ratchet as `test_docs_links.py`, applied to a different directory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"

# A *path* claim inside backticks: it has to contain a separator. A bare
# `gate_probe.py` in prose is a module name, not a link, and treating it as one
# made this test fail on four healthy skills the first time it ran. A check that
# cries wolf gets skimmed, which is the failure the `ratchet` skill warns about.
PATHISH = re.compile(r"`([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.*-]+)+\.(?:md|py|json|toml|yml))`")


def _skills() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def test_skills_were_found():
    """Guards the guard: an empty glob makes every assertion below vacuous."""
    assert len(_skills()) >= 6, f"expected the workflow skills under {SKILLS}"


@pytest.mark.parametrize("skill", _skills(), ids=lambda p: p.parent.name)
def test_frontmatter_is_present_and_complete(skill: Path):
    """Without `name` and `description` the skill is invisible to the model, and
    an invisible skill is indistinguishable from one that was never written."""
    text = skill.read_text(encoding="utf-8")

    assert text.startswith("---\n"), f"{skill.parent.name}: no frontmatter block"
    _, frontmatter, _ = text.split("---", 2)

    assert re.search(r"^name:\s*\S+", frontmatter, re.M), "missing name"
    assert re.search(r"^description:\s*\S+", frontmatter, re.M), "missing description"


@pytest.mark.parametrize("skill", _skills(), ids=lambda p: p.parent.name)
def test_the_name_matches_the_directory(skill: Path):
    """They are looked up by directory. A mismatch means the skill the model
    reads about is not the one it can invoke."""
    declared = re.search(r"^name:\s*(\S+)", skill.read_text(encoding="utf-8"), re.M)
    assert declared is not None
    assert declared.group(1) == skill.parent.name


@pytest.mark.parametrize("skill", _skills(), ids=lambda p: p.parent.name)
def test_the_description_says_when_to_use_it(skill: Path):
    """A description that only says what the skill *is* never fires. The model
    matches on the situation, so the trigger has to be in there."""
    text = skill.read_text(encoding="utf-8")
    description = re.search(r"^description:\s*(.+)$", text, re.M)
    assert description is not None

    body = description.group(1).lower()
    assert "use when" in body or "trigger" in body, (
        f"{skill.parent.name}: description does not say when to use it, so it will not be selected"
    )
    assert len(description.group(1)) > 80, "too terse to match a real request"


@pytest.mark.parametrize("skill", _skills(), ids=lambda p: p.parent.name)
def test_every_repo_path_it_names_exists(skill: Path):
    """A skill pointing at a renamed file reads as guidance and sends the reader
    nowhere. Globs are allowed because several point at directories of records."""
    missing = []
    for raw in PATHISH.findall(skill.read_text(encoding="utf-8")):
        if "*" in raw:
            if not list(ROOT.glob(raw)):
                missing.append(raw)
        elif not (ROOT / raw).exists():
            missing.append(raw)

    assert not missing, f"{skill.parent.name} references missing paths: {missing}"


def test_the_check_would_actually_fire(tmp_path: Path):
    """A validator that cannot fail is decoration."""
    bad = tmp_path / "SKILL.md"
    bad.write_text("no frontmatter here\n", encoding="utf-8")

    assert not bad.read_text(encoding="utf-8").startswith("---\n")
    assert PATHISH.findall("see `docs/nope.md` for details") == ["docs/nope.md"]
    # and the narrowing that stopped the false alarms still holds
    assert PATHISH.findall("`gate_probe.py` sweeps properties") == []
