"""
CLAUDE.md and AGENTS.md must not drift into two copies of the same rules.

They are the *same kind of file* for different vendors. `agents.md` is an open
standard read by Codex, Cursor, Gemini CLI, Copilot, Aider, Zed and others;
`CLAUDE.md` is Claude Code's. Neither is an org chart of LLM personas — that was
a misconception this repo briefly acted on, and the roster it produced now lives
in `docs/ROLES.md`, which is documentation about *this system's* trust
boundaries rather than instructions to an agent.

So the arrangement is: **CLAUDE.md is canonical, AGENTS.md points at it.** The
failure mode being ratcheted (invariant 8) is a safety rule copied into both and
then updated in one — a stale copy of a rule is worse than no copy, because it
reads as authoritative.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE = ROOT / "CLAUDE.md"
AGENTS = ROOT / "AGENTS.md"
ROLES = ROOT / "docs" / "ROLES.md"


def test_all_three_exist():
    """Guards the guard: a missing file would make the assertions below vacuous."""
    for path in (CLAUDE, AGENTS, ROLES):
        assert path.exists(), f"{path.name} is missing"


def test_agents_md_points_at_claude_md():
    """An agent that reads only AGENTS.md must be told where the rest is."""
    text = AGENTS.read_text(encoding="utf-8")
    assert "CLAUDE.md" in text


def test_agents_md_stays_thin():
    """Thin is the whole design. If AGENTS.md grows toward CLAUDE.md's size, the
    two are being maintained in parallel and one of them is already wrong."""
    agents_lines = len(AGENTS.read_text(encoding="utf-8").splitlines())
    claude_lines = len(CLAUDE.read_text(encoding="utf-8").splitlines())

    assert agents_lines < 80, f"AGENTS.md is {agents_lines} lines; it is a pointer, not a copy"
    assert agents_lines < claude_lines / 2, (
        f"AGENTS.md ({agents_lines}) is approaching CLAUDE.md ({claude_lines}). "
        "Duplicated instructions drift, and the stale copy still reads as authoritative."
    )


def test_the_numbered_invariants_live_in_exactly_one_file():
    """They are cited by number from source comments. Two numbered lists in two
    files is how citation 6 comes to mean different things in different places."""
    marker = "promoted` is derived, never settable"

    assert marker in CLAUDE.read_text(encoding="utf-8")
    assert marker not in AGENTS.read_text(encoding="utf-8")
    assert marker not in ROLES.read_text(encoding="utf-8")


def test_roles_is_not_mistaken_for_a_persona_org_chart():
    """The distinction that was got wrong once, kept where a reader will hit it."""
    text = ROLES.read_text(encoding="utf-8")
    assert ".claude/agents" in text, "must say where real subagent definitions live"
    assert "not** a persona org-chart" in text


def test_the_rules_agents_md_does_keep_are_the_ones_a_stranger_needs_first():
    """A thin pointer is only safe if the few things it *does* carry are the ones
    that prevent damage before CLAUDE.md is ever opened."""
    text = AGENTS.read_text(encoding="utf-8")
    for rule in ("Never push to `main`", "substrate-guard", "correctly rejected"):
        assert rule in text, f"AGENTS.md dropped a rule a stranger needs first: {rule}"
