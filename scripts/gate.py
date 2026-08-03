"""Run the gate lane and exit nonzero if any part of it failed.

Outside `src/expfactory/` on purpose: this is developer tooling, not verification
substrate, so it needs no classification in `gates_v1.py` and cannot be mistaken
for something a verdict depends on.

## Why this exists

`CLAUDE.md` lists the lane as four separate commands. Run by hand, the natural
shape is to pipe each one to `tail` to keep the output short — and a pipeline's
exit status is the *last* command's, so `ruff format --check src tests | tail -1`
exits 0 whether or not the check passed. That has now shipped unformatted files
once and hidden a `ProbeUnavailable` exit once, in the same session that
documented the trap as already-made.

`CLAUDE.local.md`'s rule is the reason this is a script and not another line of
prose: *"Done it twice by hand? The third time is a command."* Prose does not
ratchet (invariant 8), and "remember not to pipe to tail" is prose.

## Usage

    python scripts/gate.py            # the gate lane: deterministic, free
    python scripts/gate.py --eval     # also the eval lane (needs local Ollama)

The eval lane is separate because it needs a model server, which is why it is
deliberately outside CI. `llm_probe` exits **3** when no server answers, distinct
from a clean report, so that "no server" can never be read as "no findings" —
that distinction is the reason this script reports the probe's exit code rather
than folding it into a boolean.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# `ruff format` runs over `src tests`, not just changed files: CI checks both and
# a stray long line in a test has failed a build here.
GATE_LANE: tuple[tuple[str, list[str]], ...] = (
    ("pytest", [sys.executable, "-m", "pytest", "-q"]),
    ("ruff check", ["ruff", "check", "src", "tests"]),
    ("ruff format --check", ["ruff", "format", "--check", "src", "tests"]),
    ("mypy --strict", ["mypy", "src", "--strict"]),
    ("selfcheck", [sys.executable, "-m", "expfactory.selfcheck"]),
)

# Distinct exit meaning "no model server", per llm_probe's own contract.
PROBE_UNAVAILABLE = 3


def _run(label: str, argv: list[str], env_pythonpath: bool = False) -> int:
    """Run one step, streaming its output, and return its real exit code.

    Nothing is piped. That is the whole point of the file — a pipeline reports
    the last command's status, and every step here is one whose status matters.
    """
    print(f"\n=== {label} " + "=" * max(0, 60 - len(label)), flush=True)
    env = None
    if env_pythonpath:
        import os

        env = {**os.environ, "PYTHONPATH": str(_ROOT / "src")}
    completed = subprocess.run(argv, cwd=_ROOT, env=env)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eval",
        action="store_true",
        help="also run the eval lane (llm_probe against local Ollama)",
    )
    args = parser.parse_args()

    failures: list[tuple[str, int]] = []
    for label, argv in GATE_LANE:
        code = _run(label, argv, env_pythonpath=label == "selfcheck")
        if code != 0:
            failures.append((label, code))

    notes: list[str] = []
    if args.eval:
        # BOTH configurations, because "clean" means different things in each and
        # one number hides which. The default verifier does not arm G-07/G-08 —
        # it prints NOT ARMED: ['preregistration'] — and those are the gates
        # BRE-40 most recently found a promotion bypass in. Running only the
        # default would report a clean eval lane over everything except the area
        # most recently known broken.
        #
        # The default is not simply replaced by the armed one: requiring a
        # preregistration rejects every one-off candidate that has no lineage,
        # which is most of the adversarial fixtures. The two runs cover different
        # things and both are needed.
        for label, extra in (
            ("llm_probe (default)", []),
            ("llm_probe (armed: G-07/G-08)", ["--require-prereg"]),
        ):
            code = _run(label, [sys.executable, "-m", "expfactory.llm_probe", *extra], True)
            if code == PROBE_UNAVAILABLE:
                # Reported, never counted as a pass and never counted as a
                # failure. A missing server is not a clean report and is not a
                # finding — the whole reason that exit code is distinct.
                notes.append(f"{label}: DID NOT RUN (exit 3, no model server). Not a pass.")
            elif code != 0:
                failures.append((label, code))
            else:
                notes.append(f"{label}: clean")
    probe_note = ("\n" + "\n".join(notes)) if notes else ""

    print("\n" + "=" * 68)
    if failures:
        for label, code in failures:
            print(f"FAILED  {label}  (exit {code})")
        print(probe_note.strip())
        return 1

    print("gate lane: PASS" + probe_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
