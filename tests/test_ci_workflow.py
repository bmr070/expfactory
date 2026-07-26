"""
CI cannot be arranged so that a check silently does not run.

Written because it was. The substrate guard sat in the middle of the `check`
job, the job shell is `bash -e`, and the guard fails *by design* on any PR
touching the protected set. So on exactly the PRs where the gate set changed,
every step after it — including the adversarial suite, the thing that decides
whether the gates still work — never executed. Two substrate PRs merged that way
before anyone noticed (#29, #31), each with a locally-run suite and a CI that had
not checked it.

The general rule this encodes: **a policy signal must never be able to
short-circuit a correctness check.** The guard answers "should a human look at
this", which an admin override answers back. The boundary test answers "do the
gates still work". Running the first ahead of the second means the override is
granted with less evidence than an ordinary PR gets.

Asserted against the workflow file rather than trusted to review, per invariant 8.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

# Imported hard, not via importorskip. PyYAML is pinned into the `dev` extra for
# this file, and a test about checks-that-silently-skip must not be able to
# silently skip.

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _step_names(job: dict[str, Any]) -> list[str]:
    return [str(s.get("name", s.get("uses", ""))) for s in job.get("steps", [])]


def test_the_workflow_parses_and_has_jobs(workflow):
    """Guards the guard: a parse that silently yielded {} would make every
    assertion below vacuously true."""
    assert workflow.get("jobs"), "no jobs parsed out of ci.yml"


def test_the_substrate_guard_is_its_own_job(workflow):
    """The fix for #30. In its own job it cannot abort anything else."""
    assert "substrate-guard" in workflow["jobs"], (
        "the substrate guard must be a separate job; as a step it aborts every "
        "check after it on exactly the PRs where the gates changed"
    )


def test_no_other_job_runs_the_substrate_guard(workflow):
    """Belt and braces. Re-adding it as a step somewhere would restore the bug
    while this file still passed on the job-exists check alone."""
    for name, job in workflow["jobs"].items():
        if name == "substrate-guard":
            continue
        for step in job.get("steps", []):
            assert "substrate_guard" not in str(step.get("run", "")), (
                f"job {name!r} runs the substrate guard inline; it will abort the "
                "steps after it whenever the guard blocks"
            )


def test_the_boundary_test_runs_in_a_job_that_the_guard_cannot_abort(workflow):
    """The property that actually matters, stated directly."""
    holders = [
        name
        for name, job in workflow["jobs"].items()
        if any("selfcheck" in str(s.get("run", "")) for s in job.get("steps", []))
    ]
    assert holders, "nothing runs python -m expfactory.selfcheck"
    assert "substrate-guard" not in holders


def test_the_boundary_test_and_the_tests_are_in_the_same_job_as_the_install(workflow):
    """A separated job that forgets to install the package fails for a reason
    that has nothing to do with the gates, which reads as a broken tool rather
    than a real verdict."""
    check = workflow["jobs"]["check"]
    names = " ".join(_step_names(check)).lower()
    assert "install" in names
    runs = " ".join(str(s.get("run", "")) for s in check["steps"])
    assert "pytest" in runs and "selfcheck" in runs


def test_the_demo_test_cannot_silently_skip(workflow):
    """Carried from the demo work: CI installs the extra and forces the test.
    Previously asserted in tests/test_demo_drone.py against the raw text; kept
    here too because this file is where CI invariants live now."""
    check = workflow["jobs"]["check"]
    runs = " ".join(str(s.get("run", "")) for s in check["steps"])
    assert '".[dev,demo]"' in runs

    envs = [s.get("env", {}) for s in check["steps"]]
    assert any(str(e.get("EXPFACTORY_REQUIRE_DEMO")) == "1" for e in envs)


def test_the_guard_only_runs_on_pull_requests(workflow):
    """A push to main has no base to diff against. Without the condition the job
    fails on every push for a reason that is not a finding."""
    assert "pull_request" in str(workflow["jobs"]["substrate-guard"].get("if", ""))


def test_the_checkout_for_the_guard_has_history(workflow):
    """Three-dot diffing needs the merge base. A shallow checkout makes the guard
    report whatever the default depth happens to include — which could be
    nothing, and a guard that finds nothing looks exactly like a clean PR."""
    steps = workflow["jobs"]["substrate-guard"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 0
