"""
Documentation links resolve.

Written because they did not. `docs/DISPATCH-READINESS.md` was referenced from
AGENTS.md, from a GitHub issue and from the tracker as though it existed — the
command meant to write it had silently failed, and nothing noticed because no
test reads the docs. On a public repo that is a checklist someone is told to
follow and cannot open.

The ratchet (W-11): a recurring failure becomes a check at the cheapest
sufficient point. For a dead link that point is a test, not a habit of
remembering.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# [text](target) where target is not an external URL and not a bare anchor
LINK = re.compile(r"\]\((?!https?:|mailto:)([^)#]+)")


def _markdown_files() -> list[Path]:
    return sorted([*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")])


def test_markdown_files_were_found():
    """Guards the guard: a glob that matches nothing would make every assertion
    below vacuously true."""
    assert len(_markdown_files()) > 5


def test_every_relative_documentation_link_resolves():
    broken = [
        f"{md.relative_to(ROOT)} -> {target}"
        for md in _markdown_files()
        for target in LINK.findall(md.read_text(encoding="utf-8"))
        if not (md.parent / target.strip()).exists()
    ]
    assert not broken, "dead documentation links:\n  " + "\n  ".join(broken)
