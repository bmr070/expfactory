"""
The contract every ComputeSubstrate must satisfy, run against more than one.

"Local now, edge or cloud later" is only true if the seam actually holds a second
implementation. W-02 made the same argument for the verifier and settled it the
same way: `ExitCodeVerifier` exists mainly to prove `Verifier` admits two
implementations. A protocol exercised by exactly one class is a description of
that class.

So these tests are parameterised. `LocalGpuSubstrate` runs real detached
processes; `FakeRemoteSubstrate` models a provider that hands back an opaque
handle and resolves out of band — the shape Modal's `spawn` has. When a real
remote substrate is written, adding it to `SUBSTRATES` is the acceptance test.

The properties here are the ones `JobRegistry` actually depends on. Anything a
substrate may reasonably differ on — how fast, where artifacts live, whether a
GPU exists — is deliberately absent.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from expfactory.local_substrate import (
    CostModel,
    LocalGpuSubstrate,
    SubstrateRefused,
)
from expfactory.registry import (
    ComputeSubstrate,
    CostCapExceeded,
    JobRegistry,
    JobSpec,
    JobState,
)

PY_NOOP = ("python", "-c", "pass")


class FakeRemoteSubstrate:
    """A stand-in for a provider that resolves out of band.

    Deliberately shares no code with the local one. If the contract below only
    passed for implementations built the same way, it would not be a contract.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._ids = itertools.count()
        self._done: set[str] = set()

    def submit(self, spec: JobSpec) -> str:
        return f"remote-{spec.ticket}-{next(self._ids)}"

    def poll(self, handle: str) -> JobState:
        if handle in self._done:
            return JobState.RESOLVED
        if not handle.startswith("remote-"):
            return JobState.LOST
        return JobState.SUBMITTED

    def fetch_artifact(self, handle: str) -> str:
        if handle not in self._done:
            raise SubstrateRefused(f"{handle} not finished")
        return f"s3://bucket/{handle}/result.json"

    # test-only hook standing in for the provider finishing the work
    def finish(self, handle: str) -> None:
        self._done.add(handle)


Factory = Callable[[Path], tuple[ComputeSubstrate, Callable[[str], None]]]


def _local(tmp: Path) -> tuple[ComputeSubstrate, Callable[[str], None]]:
    sub = LocalGpuSubstrate(tmp, prober=lambda: [])

    def wait(handle: str) -> None:
        deadline = time.time() + 60
        while time.time() < deadline and sub.poll(handle) is JobState.SUBMITTED:
            time.sleep(0.05)

    return sub, wait


def _remote(tmp: Path) -> tuple[ComputeSubstrate, Callable[[str], None]]:
    sub = FakeRemoteSubstrate(tmp)
    return sub, sub.finish


SUBSTRATES: dict[str, Factory] = {"local": _local, "remote-fake": _remote}


@pytest.fixture(params=sorted(SUBSTRATES), ids=sorted(SUBSTRATES))
def substrate(request, tmp_path: Path) -> Iterator[tuple[ComputeSubstrate, Callable[[str], None]]]:
    yield SUBSTRATES[request.param](tmp_path / request.param)


def _spec(ticket: str = "T-1") -> JobSpec:
    import sys

    return JobSpec(ticket=ticket, command=(sys.executable, "-c", "pass"), image="local")


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


def test_it_satisfies_the_protocol(substrate):
    sub, _ = substrate
    assert isinstance(sub, ComputeSubstrate)


def test_submit_returns_a_handle_that_polls(substrate):
    sub, _ = substrate
    handle = sub.submit(_spec())
    assert isinstance(handle, str) and handle
    assert sub.poll(handle) in (JobState.SUBMITTED, JobState.RESOLVED)


def test_handles_are_unique_over_the_substrate_lifetime(substrate):
    """The registry keys its event log on the handle, and replays that log from
    the beginning. A handle reused after an earlier job resolved would make the
    old job's events apply to the new one.

    Submitted sequentially, each finished before the next, because *concurrency
    limits are provider policy and not part of this contract* — the local
    substrate deliberately refuses a second simultaneous job on a one-card box.
    """
    sub, finish = substrate
    handles = []
    for i in range(3):
        h = sub.submit(_spec(f"T-{i}"))
        finish(h)
        handles.append(h)
    assert len(set(handles)) == 3


def test_a_finished_job_resolves(substrate):
    sub, finish = substrate
    handle = sub.submit(_spec())
    finish(handle)
    assert sub.poll(handle) is JobState.RESOLVED


def test_polling_is_idempotent_after_resolution(substrate):
    """The registry polls repeatedly during a sweep. A state that changes on
    read would make reconciliation depend on how often it ran."""
    sub, finish = substrate
    handle = sub.submit(_spec())
    finish(handle)
    assert {sub.poll(handle) for _ in range(3)} == {JobState.RESOLVED}


def test_an_unknown_handle_is_lost_rather_than_an_exception(substrate):
    """`reconcile` polls handles read back from the log, including from a
    previous process. A raise there would jam the sweep on one bad row."""
    sub, _ = substrate
    assert sub.poll("definitely-not-a-real-handle") is JobState.LOST


def test_the_artifact_is_a_reference_available_only_after_completion(substrate):
    sub, finish = substrate
    handle = sub.submit(_spec())
    with pytest.raises(Exception):  # noqa: B017 - the type is the provider's business
        sub.fetch_artifact(handle)

    finish(handle)
    ref = sub.fetch_artifact(handle)
    assert isinstance(ref, str) and ref


# --------------------------------------------------------------------------- #
# Integration: the caps actually bind
# --------------------------------------------------------------------------- #


def test_local_cost_makes_the_daily_cap_bind(tmp_path: Path):
    """The central claim of the local cost model, tested end to end.

    A local GPU has no invoice, so the obvious implementation reports zero and
    every cap passes forever while still reading as enforced. Here a real
    registry with a real cap refuses a real submission — which it could not do if
    the estimate were zero.
    """
    sub = LocalGpuSubstrate(tmp_path / "jobs", prober=lambda: [])
    registry = JobRegistry(
        tmp_path / "jobs.jsonl",
        sub,
        per_job_cap_usd=1.00,
        per_day_cap_usd=10.00,
    )

    overnight = sub.estimate_usd(12.0)
    assert overnight > 1.00, "twelve GPU-hours must cost more than the per-job cap"

    with pytest.raises(CostCapExceeded):
        registry.submit(_spec("T-overnight"), cost_estimate_usd=overnight)

    # and nothing was started
    assert registry.outstanding() == []


def test_a_zero_cost_model_would_disable_the_caps(tmp_path: Path):
    """States the failure this design avoids, so that reintroducing it fails here
    rather than silently in production.

    If someone 'simplifies' the cost model to zero because local compute is free,
    this test documents exactly what breaks: an eighteen-hour job sails through a
    cap set at one dollar.
    """
    free = CostModel(board_watts=0.0, electricity_usd_per_kwh=0.0, amortisation_usd_per_hour=0.0)
    sub = LocalGpuSubstrate(tmp_path / "jobs", cost_model=free, prober=lambda: [])
    registry = JobRegistry(
        tmp_path / "jobs.jsonl", sub, per_job_cap_usd=1.00, per_day_cap_usd=10.00
    )

    # 18 hours of GPU, and the cap does not notice
    record = registry.submit(_spec("T-free"), cost_estimate_usd=sub.estimate_usd(18.0))
    assert record.cost_estimate_usd == 0.0

    # whereas the shipped default refuses it
    real = LocalGpuSubstrate(tmp_path / "jobs2", prober=lambda: [])
    assert real.estimate_usd(18.0) > 1.00
