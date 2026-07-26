"""
The local GPU behind the ComputeSubstrate seam.

Two properties carry this module, and both are tested against real processes
rather than mocks, because a mock would assert my assumption about how detached
processes behave rather than how they do:

1. **A job outlives the process that submitted it.** If it does not, W-06's
   two-substrate split is decorative and the registry has nothing to register.
2. **Cost is never zero.** The registry's caps and breaker are denominated in
   dollars. A local GPU has no invoice, and the obvious `return 0.0` makes every
   cap pass forever while still reading as enforced — the same shape as the
   demo's zero-width noise band.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from expfactory.local_substrate import (
    CostModel,
    GpuInfo,
    LocalGpuSubstrate,
    SubstrateRefused,
    describe_local_compute,
    probe_gpus,
)
from expfactory.registry import JobSpec, JobState

PY = sys.executable


def _spec(command: list[str], ticket: str = "T-1", gpu: str | None = None, **env: str) -> JobSpec:
    return JobSpec(ticket=ticket, command=tuple(command), image="local", gpu=gpu, env=env or None)


def _wait_for(sub: LocalGpuSubstrate, handle: str, timeout: float = 60.0) -> JobState:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = sub.poll(handle)
        if state is not JobState.SUBMITTED:
            return state
        time.sleep(0.05)
    return sub.poll(handle)


def _no_gpu() -> list[GpuInfo]:
    return []


def _fake_gpu(free: int = 11000, total: int = 12282) -> list[GpuInfo]:
    return [GpuInfo(0, "Fake 4070", total, total - free, 200.0)]


# --------------------------------------------------------------------------- #
# Detachment and durability
# --------------------------------------------------------------------------- #


def test_a_job_runs_and_resolves(tmp_path: Path):
    sub = LocalGpuSubstrate(tmp_path, prober=_no_gpu)
    handle = sub.submit(_spec([PY, "-c", "print('hello')"]))

    assert _wait_for(sub, handle) is JobState.RESOLVED
    done = json.loads(Path(sub.fetch_artifact(handle)).read_text(encoding="utf-8"))
    assert done["exit_code"] == 0
    assert done["wall_seconds"] >= 0


def test_survives_the_submitting_process(tmp_path: Path):
    """The load-bearing test for W-06.

    A *separate* interpreter submits a long job and exits. This one then resolves
    it. If the job were tied to its submitter it would already be dead, and the
    two-substrate split would be a diagram rather than a mechanism.
    """
    root = tmp_path / "jobs"
    submitter = (
        "import sys, json;"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / 'src')!r});"
        "from expfactory.local_substrate import LocalGpuSubstrate;"
        "from expfactory.registry import JobSpec;"
        f"s = LocalGpuSubstrate({str(root)!r}, prober=lambda: []);"
        f"h = s.submit(JobSpec(ticket='T-detach', command=({PY!r}, '-c', "
        "'import time; time.sleep(2); print(\"done\")'), image='local'));"
        "print(h)"
    )
    out = subprocess.run([PY, "-c", submitter], capture_output=True, text=True, check=True)
    handle = out.stdout.strip().splitlines()[-1]

    # The submitter has exited. A new substrate object, in this process, sees it.
    sub = LocalGpuSubstrate(root, prober=_no_gpu)
    assert sub.poll(handle) is JobState.SUBMITTED, "job died with its submitter"
    assert _wait_for(sub, handle) is JobState.RESOLVED
    assert (
        json.loads(Path(sub.fetch_artifact(handle)).read_text(encoding="utf-8"))["exit_code"] == 0
    )


def test_state_is_read_from_disk_not_memory(tmp_path: Path):
    """A fresh object, as after a reboot, resolves a job it never submitted."""
    sub = LocalGpuSubstrate(tmp_path, prober=_no_gpu)
    handle = sub.submit(_spec([PY, "-c", "pass"]))
    _wait_for(sub, handle)

    assert LocalGpuSubstrate(tmp_path, prober=_no_gpu).poll(handle) is JobState.RESOLVED


def test_a_failing_job_still_resolves_with_its_exit_code(tmp_path: Path):
    """The substrate reports *completion*, never success. Judging the result here
    would put a second authoritative opinion next to the ledger, which is the
    ambiguity M2-03 declined Metaflow to avoid."""
    sub = LocalGpuSubstrate(tmp_path, prober=_no_gpu)
    handle = sub.submit(_spec([PY, "-c", "import sys; sys.exit(3)"]))

    assert _wait_for(sub, handle) is JobState.RESOLVED
    assert (
        json.loads(Path(sub.fetch_artifact(handle)).read_text(encoding="utf-8"))["exit_code"] == 3
    )


def test_a_job_killed_without_finishing_reads_as_lost(tmp_path: Path):
    """No completion record and no live process. Reporting SUBMITTED forever
    would leave the registry waiting out a deadline on a job that is already
    gone."""
    sub = LocalGpuSubstrate(tmp_path, prober=_no_gpu)
    handle = sub.submit(_spec([PY, "-c", "import time; time.sleep(120)"]))
    assert sub.poll(handle) is JobState.SUBMITTED

    assert sub.cancel(handle)
    deadline = time.time() + 30
    while time.time() < deadline and sub.poll(handle) is JobState.SUBMITTED:
        time.sleep(0.05)
    assert sub.poll(handle) is JobState.LOST


def test_a_job_that_finishes_during_the_liveness_probe_is_not_declared_lost(tmp_path: Path):
    """Regression. The probe is slow — `tasklist` on Windows costs ~250 ms — so a
    short job can complete while it runs, and judging on the stale answer marked
    a finished job LOST.

    Not cosmetic: any lost job opens the registry's breaker, so a fast job polled
    at the wrong moment would have demanded a human reset. Encoded here as the
    ordering rule it really is — a dead process that left a completion record
    finished, and the record outranks the probe.
    """
    sub = LocalGpuSubstrate(tmp_path, prober=_no_gpu)
    d = tmp_path / "fabricated"
    d.mkdir()
    # pid 2**22 is above every platform's default maximum, so it is reliably dead
    (d / "meta.json").write_text(json.dumps({"pid": 4_194_304}), encoding="utf-8")
    (d / "done.json").write_text(json.dumps({"exit_code": 0}), encoding="utf-8")

    assert sub.poll("fabricated") is JobState.RESOLVED

    (d / "done.json").unlink()
    assert sub.poll("fabricated") is JobState.LOST


def test_an_unknown_handle_is_lost_not_an_exception(tmp_path: Path):
    assert LocalGpuSubstrate(tmp_path, prober=_no_gpu).poll("never-submitted") is JobState.LOST


def test_fetching_an_artifact_before_completion_refuses(tmp_path: Path):
    sub = LocalGpuSubstrate(tmp_path, prober=_no_gpu)
    handle = sub.submit(_spec([PY, "-c", "import time; time.sleep(120)"]))
    try:
        with pytest.raises(SubstrateRefused):
            sub.fetch_artifact(handle)
    finally:
        sub.cancel(handle)


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #


def test_one_card_does_not_take_two_jobs(tmp_path: Path):
    sub = LocalGpuSubstrate(tmp_path, prober=_fake_gpu, max_concurrent=1)
    first = sub.submit(_spec([PY, "-c", "import time; time.sleep(120)"]))
    try:
        with pytest.raises(SubstrateRefused, match="max_concurrent"):
            sub.submit(_spec([PY, "-c", "pass"], ticket="T-2"))
    finally:
        sub.cancel(first)


def test_requesting_a_gpu_that_does_not_exist_is_refused_immediately(tmp_path: Path):
    """Refused in milliseconds rather than discovered after six hours of the job
    quietly running on CPU."""
    sub = LocalGpuSubstrate(tmp_path, prober=_no_gpu)
    with pytest.raises(SubstrateRefused, match="no device"):
        sub.submit(_spec([PY, "-c", "pass"], gpu="cuda:0"))


def test_a_job_larger_than_the_card_is_refused(tmp_path: Path):
    sub = LocalGpuSubstrate(tmp_path, prober=_fake_gpu, reserve_mib=1536)
    with pytest.raises(SubstrateRefused, match="needs 20000 MiB"):
        sub.submit(_spec([PY, "-c", "pass"], gpu="cuda:0", EXPFACTORY_VRAM_MIB="20000"))


def test_headroom_is_reserved_for_the_desktop(tmp_path: Path):
    """This GPU also drives a display. A job admitted using every free megabyte
    dies when something opens a video, at hour three of eight."""
    sub = LocalGpuSubstrate(tmp_path, prober=lambda: _fake_gpu(free=2000), reserve_mib=1536)
    with pytest.raises(SubstrateRefused):
        sub.submit(_spec([PY, "-c", "pass"], gpu="cuda:0", EXPFACTORY_VRAM_MIB="1000"))


def test_a_nonsense_vram_request_is_refused_not_ignored(tmp_path: Path):
    sub = LocalGpuSubstrate(tmp_path, prober=_fake_gpu)
    with pytest.raises(SubstrateRefused, match="not an integer"):
        sub.submit(_spec([PY, "-c", "pass"], EXPFACTORY_VRAM_MIB="lots"))


def test_a_refused_job_leaves_nothing_behind(tmp_path: Path):
    """Refusal must be total. A half-created run directory would read as a job."""
    sub = LocalGpuSubstrate(tmp_path, prober=_no_gpu)
    with pytest.raises(SubstrateRefused):
        sub.submit(_spec([PY, "-c", "pass"], gpu="cuda:0"))
    assert list(Path(tmp_path).iterdir()) == []


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #


def test_cost_is_never_zero():
    """The reason this model exists at all. `per_job_cap_usd` and
    `per_day_cap_usd` are checked against this number; if it can be zero the caps
    are unsatisfiable-by-construction and the breaker never trips on spend."""
    cm = CostModel()
    assert cm.estimate(0.0) > 0.0
    assert cm.estimate(-5.0) > 0.0
    assert cm.usd_per_hour() > 0.0


def test_cost_rises_with_duration():
    cm = CostModel()
    assert cm.estimate(8.0) > cm.estimate(1.0) > cm.estimate(0.1)


def test_an_overnight_run_is_expensive_enough_to_hit_a_cap():
    """A sanity check on magnitude, not precision. If twelve hours of GPU imputes
    to pennies, a daily cap set at any sane figure never binds and the mechanism
    is theatre."""
    assert CostModel().estimate(12.0) > 1.0


def test_the_rate_is_configurable_for_rented_compute():
    """Moving to Modal or an edge box changes the rate, not the mechanism."""
    rented = CostModel(board_watts=0.0, electricity_usd_per_kwh=0.0, amortisation_usd_per_hour=2.50)
    assert rented.estimate(2.0) == pytest.approx(5.0)


# --------------------------------------------------------------------------- #
# Probe
# --------------------------------------------------------------------------- #


def test_a_missing_nvidia_smi_reports_no_gpu_rather_than_raising():
    """CI has no GPU. That is an ordinary condition and the caller decides
    whether it is fatal, so the probe must not make it an error."""

    def missing(_cmd):
        raise FileNotFoundError("nvidia-smi")

    assert probe_gpus(runner=missing) == []


def test_a_malformed_row_is_skipped_not_guessed_at():
    """Guessing at capacity is how a job is admitted that cannot fit."""

    def mixed(_cmd):
        return "0, Good, 12282, 1227, 200.00\nbroken row\n1, Bad, notanumber, 0, 200.00\n"

    gpus = probe_gpus(runner=mixed)
    assert [g.name for g in gpus] == ["Good"]
    assert gpus[0].free_mib == 12282 - 1227


def test_the_probe_parses_the_real_format():
    """Verbatim output from the machine this was written against."""

    def real(_cmd):
        return "0, NVIDIA GeForce RTX 4070, 12282, 1227, 200.00\n"

    (gpu,) = probe_gpus(runner=real)
    assert gpu.name == "NVIDIA GeForce RTX 4070"
    assert gpu.total_mib == 12282
    assert gpu.free_mib == 11055
    assert gpu.power_limit_w == 200.0


def test_describe_is_honest_when_there_is_no_card():
    assert "no CUDA device" in describe_local_compute(prober=_no_gpu)


def test_describe_reports_the_imputed_rate(tmp_path: Path):
    text = describe_local_compute(prober=_fake_gpu)
    assert "GPU-hour" in text and "Fake 4070" in text


def test_the_cli_output_is_ascii_only(tmp_path: Path, capsys):
    """This runs on the Windows console that prompted GH#28, where cp1252 turns
    anything above U+007F into a replacement character. An em-dash here rendered
    as mojibake on its first run, so the rule is enforced rather than remembered."""
    from expfactory.local_substrate import main

    main(["--root", str(tmp_path / "nothing-here")])

    out = capsys.readouterr().out
    offenders = sorted({f"U+{ord(c):04X}" for c in out if ord(c) > 127})
    assert not offenders, f"non-ASCII in CLI output: {offenders}"
