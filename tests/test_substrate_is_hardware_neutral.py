"""BRE-33 — the compute seam names no hardware, and a check says so.

`ComputeSubstrate` is `submit` / `poll` / `fetch_artifact` / `rate_card`. Not one
signature mentions a GPU, a device class or a vendor, and that is load-bearing
rather than incidental: the GPU is one slice of one lane, and the same seam has
to serve edge boards and rented instances that have no such concept.

The seam was already neutral. What was not neutral was the *docstring*, which
called this "the GPU side of the two-substrate split" — and a protocol read as
"the GPU protocol" is a protocol someone eventually puts a device class into.
W-06's actual axis is duration and trust: an agent session lasting minutes
against a job that outlives it and holds a credential.

Fixing prose is not a ratchet (invariant 8), so this is the check. It parses the
AST rather than grepping the source, because a grep here would match the very
comments that explain the rule — a mistake this repo has already made twice.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from expfactory import registry
from expfactory.registry import ComputeSubstrate

_SEAM_METHODS = ("submit", "poll", "fetch_artifact", "rate_card")

# Words that name a device rather than a job. `image` and `command` are fine:
# they describe what to run, not what silicon runs it.
_HARDWARE_WORDS = (
    "gpu",
    "cuda",
    "nvidia",
    "accelerator",
    "device",
    "sku",
    "vram",
    "tpu",
    "a100",
    "h100",
)


def _seam_ast() -> ast.ClassDef:
    """The `ComputeSubstrate` class node, parsed from the real source file."""
    tree = ast.parse(Path(inspect.getfile(registry)).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ComputeSubstrate":
            return node
    raise AssertionError("ComputeSubstrate is gone from registry.py")


def test_the_seam_still_has_exactly_the_four_methods() -> None:
    """Pinned so a fifth method is a deliberate act, not a drift.

    Anything added here is something every future substrate must implement,
    including ones with no accelerator at all.
    """
    defined = tuple(
        n.name
        for n in _seam_ast().body
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
    )
    assert defined == _SEAM_METHODS


@pytest.mark.parametrize("method", _SEAM_METHODS)
def test_no_seam_signature_names_hardware(method: str) -> None:
    """Parameter names and annotations, checked structurally.

    A `device: str` or `gpu_sku: str` parameter here would force every substrate
    to model an accelerator, which edge and CPU-only compute cannot honestly do.
    """
    node = next(n for n in _seam_ast().body if isinstance(n, ast.FunctionDef) and n.name == method)
    surface = [a.arg for a in node.args.args if a.arg != "self"]
    surface += [ast.unparse(a.annotation) for a in node.args.args if a.annotation is not None]
    if node.returns is not None:
        surface.append(ast.unparse(node.returns))

    offenders = [token for token in surface for word in _HARDWARE_WORDS if word in token.lower()]
    assert not offenders, (
        f"ComputeSubstrate.{method} names hardware in its signature: {offenders}. "
        "The seam has to serve edge, local GPU and infra alike; a device class "
        "belongs in a substrate implementation, not in the contract every "
        "implementation must satisfy."
    )


def test_the_docstring_summary_does_not_call_this_the_gpu_side() -> None:
    """The specific regression BRE-33 fixed.

    Not a style check. A reader who takes this for "the GPU protocol" is the
    reader who adds `gpu_sku` to it, and the summary line is the only thing that
    tells them otherwise.

    **Scoped to the first line on purpose.** The first version of this test
    searched the whole docstring and failed on the body's own explanation that
    "`LocalGpuSubstrate` is the GPU side" — a check matching the prose that
    exists to explain it. That is the mistake `GOTCHAS.md` already records twice
    (`egress` matching "environ", `local_substrate` matching "tasklist"), and it
    happened here on the third try. The summary is where a mischaracterisation
    would actually mislead; the body is allowed to discuss the distinction.
    """
    doc = ComputeSubstrate.__doc__ or ""
    assert doc.strip(), "ComputeSubstrate lost its docstring"
    summary = doc.strip().split("\n", 1)[0].lower()
    assert "gpu" not in summary, (
        f"ComputeSubstrate's summary line names the GPU again: {summary!r}. "
        "LocalGpuSubstrate is the GPU side; this is the long-job side, and the "
        "axis W-06 splits on is duration and trust rather than silicon."
    )
