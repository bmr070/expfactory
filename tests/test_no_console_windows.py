"""
No subprocess call may open a console window.

On Windows every child of a windowless parent gets its own console, and this
package spawns children in loops — `poll()` probes liveness, and the polling
loops call it every 50 ms. A single test run put *hundreds* of console windows
on the owner's screen while they were trying to work.

That is not a cosmetic complaint. A tool that disrupts the machine it runs on
stops being run, and a verification layer nobody runs verifies nothing.

The fix was `creationflags=CREATE_NO_WINDOW` at every call site (0 on other
platforms, so it passes unconditionally) and replacing the worst offender —
`tasklist`, shelled out on every poll — with a direct `OpenProcess` call. That
also took the suite from 23s to 14s, because the shell-out cost ~250 ms each
time.

Ratcheted here rather than left to review (invariant 8): a new `subprocess.run`
without the flag is the easiest possible regression to introduce.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "expfactory"

SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}


def _spawn_calls(tree: ast.AST) -> list[ast.Call]:
    """Every `subprocess.<spawner>(...)` call in a module."""
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in SPAWNERS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            found.append(node)
    return found


def _modules() -> list[Path]:
    return sorted(p for p in SRC.glob("*.py") if p.name != "__init__.py")


def test_there_are_modules_to_check():
    """Guards the guard: an empty glob would make the check below vacuous."""
    assert len(_modules()) > 5


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_every_subprocess_call_suppresses_the_console(module: Path):
    source = module.read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders = [
        f"{module.name}:{call.lineno}"
        for call in _spawn_calls(tree)
        if not any(kw.arg == "creationflags" for kw in call.keywords)
    ]
    assert not offenders, (
        "subprocess call without creationflags: " + ", ".join(offenders) + "\n"
        "On Windows this opens a console window. Pass "
        "`creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)` — it is 0 "
        "elsewhere, so it is safe unconditionally."
    )


def test_the_wrapper_script_also_suppresses_it():
    """The wrapper is a string that becomes a separate process, so the AST scan
    above cannot see inside it — and it is the one that runs for hours."""
    from expfactory.local_substrate import _WRAPPER

    assert "CREATE_NO_WINDOW" in _WRAPPER


def test_liveness_does_not_shell_out_on_windows():
    """`tasklist` was the worst of them: called on every poll, ~250 ms each, and
    it opened a window every time. It was also slow enough to be *wrong* — a
    short job could finish inside the probe and be reported LOST.

    Checked against string *literals* rather than the raw text, because the
    comment explaining why it was removed necessarily mentions it. Same mistake
    as the first version of the egress environment check: a prose match is not a
    code check.
    """
    source = (SRC / "local_substrate.py").read_text(encoding="utf-8")
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    # a docstring mentioning it is fine; a command argument is not
    assert not any(lit == "tasklist" for lit in literals)
    assert "OpenProcess" in source


def test_detached_jobs_get_no_console():
    """A job that runs for hours must not put a window on the owner's desktop.

    `DETACHED_PROCESS` alone detaches from the parent's console but leaves the
    child free to allocate one; the payload it launches is a console program.
    """
    source = (SRC / "local_substrate.py").read_text(encoding="utf-8")
    assert "DETACHED_PROCESS" in source and "_NO_WINDOW" in source
