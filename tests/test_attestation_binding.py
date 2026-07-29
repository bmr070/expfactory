"""BRE-31 — G-10 stops passing checks it never performed.

G-10 already knew how to compare an artifact digest, an exit code and a ticket.
It never compared any of them. `JobRegistry.attested_job` returned handle,
ticket, state, submission time and artifact reference — and nothing else — so
every comparison read `record.get(...) is None`, took the `in (None, ...)`
branch, and passed.

That is worse than an absent check. The gate reported **"run attested by job
X"** and had verified that the handle existed and no more. A candidate could
cite a genuine handle with an arbitrary digest and an arbitrary exit code.

Two changes here:

- The substrate now reports a `CompletionRecord`, the registry writes it to its
  own append-only log at `resolve()`, and `attested_job` hands it to the gate.
  The comparisons stop being vacuous.
- The gate distinguishes **absent** from **matching**. A substrate that cannot
  say what it ran leaves the field unchecked and G-10 says so in its detail,
  rather than reporting a clean check it did not perform.

And one check nobody asked for: **the command**. A genuine handle, for a genuine
job, whose command never ran the evaluation is attested and worthless. TRL's
`opencode` reward pays -0.1 for the same failure from the RL side, commented
*"never ran its code, kills blind-write / prose-dump / give-up"*.
"""

from __future__ import annotations

from typing import Any

from expfactory.gates_v1 import gate_attested_run
from expfactory.harness import Experiment
from expfactory.registry import RunAttestation

_DIGEST = "ab" * 32
_OTHER_DIGEST = "cd" * 32
_EVAL_CMD = ("python", "evaluate.py", "--holdout")

ATTESTATION = RunAttestation(
    job_handle="BRE-1-real",
    command=_EVAL_CMD,
    exit_code=0,
    wall_seconds=1234.0,
    artifact_sha256=_DIGEST,
)


def _record(**over: Any) -> dict[str, Any]:
    """A fully-populated `attested_job` row: what the registry returns now."""
    row: dict[str, Any] = {
        "handle": "BRE-1-real",
        "ticket": "BRE-1",
        "state": "resolved",
        "submitted_at": 1000.0,
        "artifact_ref": "s3://bucket/out.json",
        "requested_command": _EVAL_CMD,
        "completion_command": _EVAL_CMD,
        "exit_code": 0,
        "wall_seconds": 1234.0,
        "artifact_sha256": _DIGEST,
        "source_revision": "deadbeef",
    }
    row.update(over)
    return row


class FakeLog:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def attested_job(self, handle: str) -> dict[str, Any] | None:
        if self._row is None or handle != self._row["handle"]:
            return None
        return self._row


def _run(row: dict[str, Any] | None, *, ticket: str | None = None, att: Any = ATTESTATION):
    return gate_attested_run(
        Experiment(exp_id="e1", parent_id=None, hypothesis="h", config={}, code_hash="c"),
        attestation=att,
        attestations=FakeLog(row),
        ticket=ticket,
    )


# ---------------------------------------------------------------------------
# The vacuous checks, now real
# ---------------------------------------------------------------------------


def test_a_clean_record_passes_and_says_nothing_went_unchecked() -> None:
    result = _run(_record())
    assert result.passed
    assert "not verified" not in result.detail


def test_a_mismatched_artifact_digest_now_blocks() -> None:
    """Previously passed. The gate compared against a key that was never present.

    This is the regression the whole ticket is about: the comparison existed,
    read `None`, and reported success.
    """
    result = _run(_record(artifact_sha256=_OTHER_DIGEST))
    assert not result.passed
    assert "artifact digest" in result.detail


def test_a_mismatched_exit_code_now_blocks() -> None:
    result = _run(_record(exit_code=1))
    assert not result.passed


def test_a_failed_job_is_not_a_result_even_when_everyone_agrees() -> None:
    """The subtler half of the exit-code check.

    Comparing for *agreement* is not enough: an attestation honestly reporting a
    crash agrees with a recorded crash, and agreement was the only thing being
    asked. A job that exited non-zero produced nothing to promote.
    """
    failed = RunAttestation(
        job_handle="BRE-1-real",
        command=_EVAL_CMD,
        exit_code=3,
        wall_seconds=12.0,
        artifact_sha256=_DIGEST,
    )
    result = _run(_record(exit_code=3), att=failed)
    assert not result.passed
    assert "exited 3" in result.detail


def test_a_handle_borrowed_from_another_ticket_blocks() -> None:
    """The run happened. It was not for the work being claimed."""
    result = _run(_record(ticket="BRE-99"), ticket="BRE-1")
    assert not result.passed
    assert "BRE-99" in result.detail


# ---------------------------------------------------------------------------
# The command check — a genuine job that ran the wrong thing
# ---------------------------------------------------------------------------


def test_a_job_that_ran_something_other_than_what_was_asked_blocks() -> None:
    """Genuine handle, genuine job, clean exit, matching digest — and the
    substrate ran `echo` instead of the evaluation.

    Every other check in this gate passes. Only comparing what was *asked* to
    what was *run* catches it.
    """
    result = _run(_record(completion_command=("echo", "done")))
    assert not result.passed
    assert "echo" in result.detail


def test_an_attestation_claiming_a_command_the_substrate_did_not_run_blocks() -> None:
    """The candidate's own claim is checked against the substrate's, not trusted.

    Here the registry asked for and the substrate ran `echo`; the attestation
    says it ran the evaluation. The requested-vs-ran check fires first, which is
    the right order — what the substrate did is the ground truth.
    """
    result = _run(_record(requested_command=("echo", "hi"), completion_command=("echo", "hi")))
    assert not result.passed
    assert "attestation claims" in result.detail


# ---------------------------------------------------------------------------
# Absent is not agreement
# ---------------------------------------------------------------------------


def test_a_substrate_that_cannot_say_leaves_the_fields_named_as_unchecked() -> None:
    """The old behaviour, now honest about itself.

    A record with no completion still passes — plenty of substrates cannot
    report one, and blocking would make G-10 something everyone bypasses. What
    changed is that it no longer claims to have checked. The detail names every
    field it could not verify.
    """
    bare = _record()
    for key in ("artifact_sha256", "exit_code", "completion_command"):
        bare.pop(key)
    result = _run(bare)

    assert result.passed
    assert "not verified" in result.detail
    for field in ("artifact digest", "exit code", "command"):
        assert field in result.detail


def test_an_unknown_handle_still_blocks() -> None:
    assert not _run(None).passed


def test_no_attestation_on_a_substrate_lane_still_blocks() -> None:
    assert not _run(_record(), att=None).passed


def test_no_source_configured_is_still_a_non_blocking_warning() -> None:
    """The deterministic lane has no job behind it, and blocking there would
    make this a gate everyone routes around."""
    result = gate_attested_run(
        Experiment(exp_id="e1", parent_id=None, hypothesis="h", config={}, code_hash="c"),
        attestation=None,
        attestations=None,
    )
    assert result.passed
    assert not result.blocking
