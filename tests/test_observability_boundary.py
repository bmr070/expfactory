"""
The adjudicating modules cannot see the observability layer (M2-06).

Established since Map II: MLflow for tracing, never for adjudication — the ledger
keeps the verdict, and a green dashboard line is never a promotion signal. That
was prose, and prose does not ratchet (invariant 8).

This is the wall. If no module that decides `promoted` can import a tracking
client, then no dashboard can influence a verdict, whatever anyone later
believes about the boundary.

Written as an import check rather than a text search on purpose. What matters is
whether the adjudicating code *can reach* the tracker, and a substring match
would fire on any comment that mentioned it — which is how a firewall test
becomes something people learn to skim.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "expfactory"

# The modules that decide whether a result is real. Kept in step with
# `_HARNESS_PATHS` in gates_v1.py, minus the ones that only carry work to and
# from the decision (trackers, substrate, egress).
ADJUDICATING = (
    "harness.py",
    "gates_v1.py",
    "verifier.py",
    "prereg.py",
    "holdout.py",
    "scorer.py",
    "adversarial_suite.py",
    "gate_probe.py",
    "selfcheck.py",
)

# Anything that could carry a number in from a dashboard. Named individually
# rather than pattern-matched, because a wildcard here would be a rule nobody
# can predict the behaviour of.
OBSERVABILITY = frozenset(
    {
        "mlflow",
        "wandb",
        "tensorboard",
        "torch.utils.tensorboard",
        "comet_ml",
        "neptune",
        "langsmith",
        "clearml",
    }
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    # Compare on the top-level package: `mlflow.tracking` is still mlflow.
    return {name.split(".")[0] for name in found}


@pytest.mark.parametrize("module", ADJUDICATING)
def test_no_adjudicating_module_can_reach_a_tracking_client(module: str):
    """M2-06's boundary, enforced rather than intended.

    The failure this prevents is not someone deliberately gating on a dashboard.
    It is the ordinary version: a metric read back from the tracker "just to
    compare", which quietly makes the verdict depend on a mutable external store
    that nothing in this repo controls.
    """
    leaked = _imports(SRC / module) & {o.split(".")[0] for o in OBSERVABILITY}
    assert not leaked, (
        f"{module} imports {sorted(leaked)}. The ledger adjudicates; the tracker "
        "observes. A module that can read the tracker can be made to promote on "
        "what it says."
    )


def test_the_guarded_list_still_matches_the_files_on_disk():
    """Guards the guard. A module renamed or added without being listed here is
    unprotected while this file still reads as protection — the exact failure
    CODEOWNERS warns about in its own header comment.
    """
    missing = [m for m in ADJUDICATING if not (SRC / m).exists()]
    assert not missing, f"listed but absent: {missing} — this test protects nothing for them"


def test_the_check_would_actually_fire(tmp_path: Path):
    """A firewall test that cannot fail is decoration. Feed it a module that
    does the forbidden thing and require it to object."""
    bad = tmp_path / "pretend_gate.py"
    bad.write_text("import mlflow\n\n\ndef gate(): return mlflow.get_run('x')\n", encoding="utf-8")

    assert _imports(bad) & {"mlflow"}


def test_a_nested_import_is_still_caught(tmp_path: Path):
    """`import mlflow.tracking` and `from mlflow.tracking import X` both reach
    mlflow. Comparing full dotted paths against a flat set would miss both."""
    nested = tmp_path / "nested.py"
    nested.write_text("import mlflow.tracking\nfrom mlflow.entities import Run\n", encoding="utf-8")

    assert _imports(nested) & {"mlflow"}
