"""The compute seam names no provider and no device class.

M2-03 was ratified on its *shape* and overturned on its *noun*: the verdict said
"Modal `spawn`", C-01 replaced the provider with the owner's local GPU four days
later, and the decision's own header already said the provider was unnamed. See
`docs/decisions/M2-03-RATIFICATION-shape-not-provider.md`.

Prose does not ratchet (invariant 8), so the ratification is worth nothing on its
own. This is the check.

**Two distinct claims, and they are checked separately because they failed
separately.**

1. *The seam names no vendor.* A provider name in `registry.py` is how a swap
   stops being a constructor change: the next person writes `if modal:` beside
   it, and reversibility — the thing the decision was ratified on — is gone.

2. *The seam names no hardware.* BRE-33 found `ComputeSubstrate`'s docstring
   saying GPU. `LocalGpuSubstrate` is the accelerator-bound implementation and it
   sits *behind* the seam; most work entering this factory needs no accelerator
   at all. Reading the seam as "the accelerator protocol" is how a device class
   ends up in a signature that also has to serve edge boards and rented
   instances.

`local_substrate.py` is deliberately exempt from the vendor check. It is the
implementation, its docstring records *why* it exists ("Modal was named as the
eventual provider; this is the first real one"), and deleting that history to
satisfy a lint would lose the reason the seam is shaped this way.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "expfactory"

# The seam itself. Not a glob: this list is short on purpose, and a module that
# joins it should be a deliberate edit rather than a filename that happened to
# match.
_SEAM_MODULES = ("registry.py", "runner.py")

# Word-bounded so `modality` and `beamforming` do not match — both are plausible
# in an audio codebase, and a check that cries wolf gets deleted.
_VENDORS = re.compile(
    r"\b(modal|northflank|beam|runpod|coreweave|lambdalabs|vast\.ai|sagemaker|"
    r"vertex ?ai|bedrock)\b",
    re.IGNORECASE,
)

# Device classes. `gpu` is the one that actually happened (BRE-33).
_HARDWARE = re.compile(r"\b(gpu|cuda|nvidia|tpu|a100|h100|rtx|jetson|coral)\b", re.IGNORECASE)


def _protocol_source(module: str, name: str) -> str:
    """The full source of one Protocol class, docstring included.

    Parsed rather than grepped. A grep over the whole file answers about
    `JobRegistry`'s internals too, and this repo has twice had a test match its
    own docstring — see docs/GOTCHAS.md.
    """
    path = _SRC / module
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"{name} is gone from {module}; this test is now checking nothing")


def test_the_compute_seam_names_no_vendor() -> None:
    """A provider name here is how a swap stops being a constructor change."""
    source = _protocol_source("registry.py", "ComputeSubstrate")
    found = _VENDORS.findall(source)
    assert not found, (
        f"ComputeSubstrate names {sorted(set(found))}. M2-03 was ratified on the shape "
        "and overturned on the noun: the seam takes a substrate, not a company."
    )


def test_the_compute_seam_names_no_device_class() -> None:
    """BRE-33's finding, kept fixed.

    Most work entering this factory needs no accelerator. `LocalGpuSubstrate` is
    the accelerator-bound implementation and sits behind this seam.
    """
    source = _protocol_source("registry.py", "ComputeSubstrate")
    found = _HARDWARE.findall(source)
    assert not found, (
        f"ComputeSubstrate names {sorted(set(found))}. The axis is duration and trust, "
        "not silicon — see the docstring's own warning."
    )


@pytest.mark.parametrize("module", _SEAM_MODULES)
def test_no_seam_module_imports_a_provider_sdk(module: str) -> None:
    """Imports, read from the AST rather than matched in text.

    An import is the version of this failure that a docstring rule cannot catch
    and that actually breaks reversibility: once `registry.py` imports a provider
    client, swapping substrates is no longer a constructor argument.
    """
    tree = ast.parse((_SRC / module).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    offenders = [name for name in imported if _VENDORS.search(name.split(".")[0])]
    assert not offenders, f"{module} imports {offenders}"


def test_the_ratification_exists_and_names_what_it_overturned() -> None:
    """The test and the decision have to point at each other.

    A check whose reasoning has been deleted is a rule nobody can evaluate, and
    the next person to hit it removes the check rather than the violation.
    """
    doc = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "decisions"
        / "M2-03-RATIFICATION-shape-not-provider.md"
    )
    text = doc.read_text(encoding="utf-8")

    assert "C-01" in text, "the ratification must say what superseded the provider"
    assert "Prefect" in text, "the fallback must stay named; the decline got weaker, not stronger"
