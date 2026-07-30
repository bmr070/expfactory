"""The verifier core imports nothing that is not in the standard library.

M2-10 declined the tier-1 infrastructure stack (Postgres, pgvector, OpenTofu) on
the grounds that the operational store is an append-only JSONL ledger with no
service under it, and that putting one there would make the thing that
adjudicates depend on something that can be down, migrated, or edited. The rule
*"an unreadable ledger means spend is unknown, not zero"* is much harder to keep
when reading the ledger is a network round trip.

That argument rests entirely on `pyproject.toml` saying `dependencies = []`, and
nothing was checking it. Prose does not ratchet (invariant 8), so this is the
check.

**Two claims, checked separately.**

1. The package declares no runtime dependencies. `numpy`, `scipy` and
   `scikit-learn` live under the `demo` extra because they belong to the
   workload, never to the gates.

2. The adjudicating modules import nothing outside the standard library. That is
   the claim with teeth: the declaration in `pyproject.toml` is a promise, and an
   import is what would break it. A module could quietly `import requests` and
   the metadata would still read clean.

The workload modules are exempt and listed by name. `drone_audio.py` needs numpy;
that is what the `demo` extra is for. Exempting by name rather than by pattern
means adding a module is a deliberate choice about which side of the line it is
on, which is the same shape as `_HARNESS_PATHS` in `gates_v1.py`.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "expfactory"

# Modules that may import third-party packages. Each one is workload or tooling,
# never adjudication. Listed rather than pattern-matched: a new module defaults
# to the strict side, and moving it here should be an argued edit.
_MAY_USE_THIRD_PARTY = frozenset(
    {
        "drone_audio.py",  # numpy — feature extraction for the demo workload
        "scorer.py",  # numpy — metric computation
        "pipeline.py",  # numpy — the demo pipeline
    }
)


def _core_modules() -> list[Path]:
    return sorted(
        path
        for path in _SRC.glob("*.py")
        if path.name != "__init__.py" and path.name not in _MAY_USE_THIRD_PARTY
    )


def _top_level_imports(path: Path) -> set[str]:
    """Top-level package name of every import, from the AST.

    Parsed, not grepped: this repo has twice had a check match its own docstring
    (see docs/GOTCHAS.md), and a docstring here that mentions `requests` should
    not fail a test about importing it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — inside the package by definition
                continue
            if node.module:
                found.add(node.module)
    return {name.split(".")[0] for name in found}


def test_the_package_declares_no_runtime_dependencies() -> None:
    """M2-10's load-bearing fact. `demo` and `dev` extras are unaffected."""
    meta = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = meta["project"].get("dependencies", [])
    assert declared == [], (
        f"the verifier core declared {declared}. M2-10 declined Postgres, pgvector and "
        "IaC on the grounds that adjudication depends on nothing that can be down — "
        "adding a runtime dependency is that decision being reversed by a diff."
    )


def test_the_demo_deps_are_an_extra_not_a_dependency() -> None:
    """They belong to the workload. A gate that needed numpy would mean the
    adjudicating layer had grown a reason to care about the task."""
    meta = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    demo = " ".join(meta["project"]["optional-dependencies"]["demo"])
    for package in ("numpy", "scipy", "scikit-learn"):
        assert package in demo


@pytest.mark.parametrize("module", _core_modules(), ids=lambda p: p.name)
def test_no_core_module_imports_a_third_party_package(module: Path) -> None:
    """The claim with teeth. Metadata is a promise; an import is what breaks it."""
    third_party = {
        name
        for name in _top_level_imports(module)
        if name not in sys.stdlib_module_names and name != "expfactory"
    }
    assert not third_party, (
        f"{module.name} imports {sorted(third_party)}. Either it belongs in "
        "_MAY_USE_THIRD_PARTY with a reason, or the import belongs behind the seam."
    )


def test_the_exempt_list_is_not_protecting_nothing() -> None:
    """A renamed or deleted module left in the exempt list reads as protection
    while protecting nothing — the failure CODEOWNERS names in its own header,
    and the same one `test_observability_boundary.py` guards against."""
    for name in _MAY_USE_THIRD_PARTY:
        assert (_SRC / name).exists(), f"{name} is exempt but does not exist"


def test_the_check_would_actually_fire(tmp_path: Path) -> None:
    """A firewall test that cannot fail is decoration."""
    bad = tmp_path / "bad.py"
    bad.write_text("import psycopg2\nfrom sqlalchemy import create_engine\n", encoding="utf-8")

    found = _top_level_imports(bad)

    assert {"psycopg2", "sqlalchemy"} <= found
    assert not ({"psycopg2", "sqlalchemy"} & sys.stdlib_module_names)
