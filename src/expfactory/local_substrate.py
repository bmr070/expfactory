"""
local_substrate — the GPU under the desk, behind the same seam as the cloud one.

M2-03 chose "adopt the compute substrate's own job primitive and keep a thin
`JobRegistry`", with Modal named as the eventual provider. This is the first real
implementation of `ComputeSubstrate`, against local hardware, written so that
swapping in Modal or an edge box later is a constructor change and nothing else.

## Detachment is the whole point

W-06 splits the lane in two: the agent writes an experiment in minutes, the
experiment runs for hours. If the job dies when the agent session ends, the
split does not exist and the registry has nothing to be a registry *of*.

So a job is a detached OS process — a new session on POSIX, a new process group
on Windows — and **all of its state lives on disk**. `poll()` reads files, never
in-memory handles. A fresh interpreter, started after a reboot, resolves the same
jobs the same way. That is the property that makes the two-substrate split real
rather than aspirational, and it is what `test_survives_the_submitting_process`
exercises.

## Cost, and why it is not zero

The registry's caps and breaker are denominated in dollars, because the design
assumed rented compute. A local GPU produces no invoice, so the obvious
implementation reports `cost_estimate_usd=0.0`.

That would be a mistake of a familiar shape. A zero cost makes `per_job_cap_usd`
and `per_day_cap_usd` unsatisfiable-by-construction: every check passes, the
breaker never trips on spend, and the cap *looks* enforced while enforcing
nothing. It is the same failure as the demo's zero-width noise band — a
mechanism that appears to scrutinise and does not.

So local runs carry an **imputed** cost: marginal electricity plus hardware
amortisation, per GPU-hour. The numbers are estimates and are documented as
such, but they are not zero, so the caps keep working and "do not cook the GPU
for eighteen hours" stays enforceable. Moving to Modal later changes the *rate*,
not the mechanism.

`CostModel` is also the tree's one concrete `RateCard` (BRE-29): the registry no
longer takes a cost from whoever is submitting, it asks the substrate what the
substrate's own time costs. Note what the rate card is *not* keyed on — there is
no GPU SKU in it, and no device class. It prices a billable window in seconds,
which is a quantity an edge box or a rented instance answers just as well.

## What this deliberately does not do

No judgement about whether a run succeeded. A process that exits non-zero is
still `RESOLVED` here, with the exit code recorded in the artifact. The registry
records what is outstanding; the gates decide what is real. If this module ever
grows a notion of "the job failed, so the experiment failed", the ambiguity
M2-03 declined Metaflow to avoid has arrived by the back door.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from expfactory.registry import (
    SECONDS_PER_HOUR,
    Clock,
    CompletionRecord,
    JobSpec,
    JobState,
    RateCard,
    SubstrateDeclined,
)

# --------------------------------------------------------------------------- #
# Hardware probe
# --------------------------------------------------------------------------- #

_SMI_QUERY = "index,name,memory.total,memory.used,power.limit"

Runner = Callable[[Sequence[str]], str]

# Windows opens a console window for every child of a windowless parent, and this
# module spawns them in loops. `CREATE_NO_WINDOW` suppresses that; it is 0 on
# every other platform so the flag can be passed unconditionally.
#
# Not cosmetic. A test run put hundreds of console windows on the owner's screen,
# and a tool that disrupts the machine it runs on stops being used.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Same treatment for the detach flags. `getattr` rather than a direct reference
# because mypy runs on Linux in CI, where these attributes do not exist, and it
# only narrows `sys.platform` inside an `if` — not inside the ternary this used
# to be written as. Caught by the CI matrix; local mypy on Windows was happy.
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)


def _run(cmd: Sequence[str]) -> str:
    out = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        creationflags=_NO_WINDOW,
    )
    return out.stdout


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    total_mib: int
    used_mib: int
    power_limit_w: float

    @property
    def free_mib(self) -> int:
        return max(self.total_mib - self.used_mib, 0)


def probe_gpus(runner: Runner = _run) -> list[GpuInfo]:
    """Ask nvidia-smi what is actually present. Empty list if there is no GPU.

    Returns empty rather than raising when the tool is missing: "no GPU here" is
    an ordinary condition (CI runs on a CPU box), and the caller decides whether
    that is fatal. A malformed *row* is a different matter and is skipped, since
    guessing at capacity is how a job gets admitted that cannot fit.
    """
    try:
        raw = runner(["nvidia-smi", f"--query-gpu={_SMI_QUERY}", "--format=csv,noheader,nounits"])
    except (OSError, subprocess.SubprocessError):
        return []

    gpus: list[GpuInfo] = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            gpus.append(
                GpuInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    total_mib=int(float(parts[2])),
                    used_mib=int(float(parts[3])),
                    power_limit_w=float(parts[4]),
                )
            )
        except ValueError:
            continue
    return gpus


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CostModel:
    """Imputed dollars per GPU-hour for hardware you already own, and the one
    concrete `RateCard` in the tree.

    Defaults are derived for the machine this was written against (RTX 4070,
    200 W board limit) and are estimates, not measurements:

      electricity   0.200 kW x $0.15/kWh   = $0.030/h   at full board power
      amortisation  ~$600 card / ~10,000 h = $0.060/h

    Round to $0.09/h. The precision is fictional and the magnitude is not: it is
    the difference between a cap that binds and a cap that cannot.

    Override all three when moving to rented compute, at which point the numbers
    stop being imputed and start being the provider's price.
    """

    board_watts: float = 200.0
    electricity_usd_per_kwh: float = 0.15
    amortisation_usd_per_hour: float = 0.06
    # Fraction of board power actually drawn under a typical training load.
    utilisation: float = 1.0

    def usd_per_hour(self) -> float:
        kw = (self.board_watts / 1000.0) * self.utilisation
        return kw * self.electricity_usd_per_kwh + self.amortisation_usd_per_hour

    def estimate(self, expected_hours: float) -> float:
        """Imputed cost of `expected_hours` on this machine. Never zero for a
        real job.

        A zero estimate would silently disable the caps, so a non-positive
        duration is floored at one minute rather than trusted.
        """
        hours = max(float(expected_hours), 1.0 / 60.0)
        return round(self.usd_per_hour() * hours, 4)

    def price_usd(self, spec: JobSpec, billable_seconds: float) -> float:
        """`RateCard` — what this machine charges for a job's billable window.

        Time, not hardware. `spec` is accepted because the seam hands it over
        and a rented substrate will need it to tell one instance class from
        another; it is deliberately unused here, because one owned box has one
        rate. Keying this on a GPU SKU would push hardware into a seam that has
        stayed hardware-free on purpose — the registry above it names no device
        anywhere, and the next substrate may not have a GPU at all.

        Non-decreasing in `billable_seconds`, which is the contract `RateCard`
        states: the registry prices the *deadline*, so a longer window has to
        cost at least as much or the deadline stops being an upper bound.
        """
        return self.estimate(billable_seconds / SECONDS_PER_HOUR)


# --------------------------------------------------------------------------- #
# The substrate
# --------------------------------------------------------------------------- #


class SubstrateRefused(SubstrateDeclined):
    """Preflight refused the job. Raised before anything is started, so a refused
    job has definitely not consumed the GPU.

    A `SubstrateDeclined` since BRE-30, which is the registry's name for exactly
    that claim. The registry writes a reservation *before* calling `submit`, so
    it has to be told the difference between "I started nothing" and "I do not
    know what happened": the first releases the reservation, the second leaves it
    for a human. Without this the everyday case — one card, `max_concurrent=1`,
    a second job asking for it — would open the circuit breaker every time.
    """


# Written by the wrapper when the payload finishes, whatever its exit code. Its
# presence is what distinguishes "done" from "died", which a pid check cannot:
# pids are reused, and a reused pid reads as a live job.
_DONE = "done.json"
_META = "meta.json"


class LocalGpuSubstrate:
    """`ComputeSubstrate` over detached local processes.

    One GPU, so `max_concurrent` defaults to 1. Two training jobs on a 12 GB card
    that also drives a display do not share it gracefully; they OOM at hour three,
    which is the most expensive time to find out.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        cost_model: CostModel | None = None,
        max_concurrent: int = 1,
        reserve_mib: int = 1536,
        clock: Clock = time.time,
        prober: Callable[[], list[GpuInfo]] = probe_gpus,
    ) -> None:
        """
        `reserve_mib` is headroom left for whatever else uses the card. On a
        desktop GPU that is the compositor, the browser, and everything else the
        machine is also doing — on the box this was written for, about 1.2 GB
        before any job starts. Admitting a job that needs every free megabyte is
        how an eight-hour run dies at hour three because something opened a video.
        """
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cost_model = cost_model or CostModel()
        self._max_concurrent = max_concurrent
        self._reserve_mib = reserve_mib
        self._clock = clock
        self._prober = prober

    # -- paths -------------------------------------------------------------

    def _dir(self, handle: str) -> Path:
        return self.root / handle

    def _key_marker(self, key: str) -> Path:
        """Where this substrate remembers that it has seen an idempotency key.

        A file, not a directory, so `running()` — which scans for job
        directories — steps over it without needing to know it exists.

        The name is a hash of the key rather than the key itself. Not
        sanitisation: a key is caller-supplied text and may contain a path
        separator, and *rewriting* it into something safe is exactly the lossy
        mapping that put two tickets in one workspace once already. A digest
        collides only if SHA-256 does.
        """
        return self.root / f"key-{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"

    # -- preflight ---------------------------------------------------------

    def capacity(self) -> list[GpuInfo]:
        return self._prober()

    def running(self) -> list[str]:
        """Handles whose process is still alive, by reading the run directories."""
        return [
            d.name
            for d in self.root.iterdir()
            if d.is_dir() and self._state_of(d.name) is JobState.SUBMITTED
        ]

    def preflight(self, spec: JobSpec, required_mib: int = 0) -> None:
        """Refuse now, or admit and start. Raises `SubstrateRefused`.

        Everything here is checkable in milliseconds and every failure it catches
        would otherwise surface hours later, which is the entire argument for
        doing it.
        """
        live = self.running()
        if len(live) >= self._max_concurrent:
            raise SubstrateRefused(
                f"{len(live)} job(s) already running and max_concurrent={self._max_concurrent}: "
                f"{live}. One card does not timeshare a training run gracefully."
            )

        gpus = self.capacity()
        if spec.gpu is not None and not gpus:
            raise SubstrateRefused(
                f"job requests gpu={spec.gpu!r} but nvidia-smi reports no device. "
                "Refusing rather than silently running on CPU for six hours."
            )
        if required_mib and gpus:
            usable = max(g.free_mib - self._reserve_mib for g in gpus)
            if usable < required_mib:
                raise SubstrateRefused(
                    f"job needs {required_mib} MiB, largest device has {usable} MiB usable "
                    f"(after {self._reserve_mib} MiB reserved for the desktop)"
                )

    # -- ComputeSubstrate --------------------------------------------------

    def submit(self, spec: JobSpec) -> str:
        """Start a detached process and return a handle that outlives this one.

        Required VRAM is read from `spec.env['EXPFACTORY_VRAM_MIB']` when present;
        the spec type is shared with the cloud substrate and is deliberately not
        widened for one provider's preflight.

        **Idempotent on `spec.idempotency_key` (BRE-30).** The registry writes its
        reservation before calling this and binds the handle after, so a process
        death in between leaves an intent whose fate only this side knows. A key
        already on disk here returns the handle it produced rather than starting
        a second run: one intent, one job, whatever the caller believes. The
        marker is written *before* the process is spawned, because a marker
        written afterwards would not exist in precisely the case it is for.
        """
        if spec.idempotency_key is not None:
            marker = self._key_marker(spec.idempotency_key)
            if marker.exists():
                try:
                    seen = json.loads(marker.read_text(encoding="utf-8"))["handle"]
                except (OSError, json.JSONDecodeError, KeyError) as exc:
                    # Refuse rather than start a second job. An unreadable marker
                    # means this key may already own a run, and guessing "it
                    # probably does not" is how one intent buys two GPUs.
                    raise SubstrateRefused(
                        f"idempotency key {spec.idempotency_key!r} has an unreadable "
                        f"marker at {marker} ({exc}); refusing to start a second job "
                        "for a key that may already own one"
                    ) from exc
                return str(seen)

        required = 0
        env_map: Mapping[str, str] = spec.env or {}
        if "EXPFACTORY_VRAM_MIB" in env_map:
            try:
                required = int(env_map["EXPFACTORY_VRAM_MIB"])
            except ValueError as exc:
                raise SubstrateRefused(
                    f"EXPFACTORY_VRAM_MIB={env_map['EXPFACTORY_VRAM_MIB']!r} is not an integer"
                ) from exc

        self.preflight(spec, required_mib=required)

        handle = f"{spec.ticket}-{uuid.uuid4().hex[:8]}"
        d = self._dir(handle)
        d.mkdir(parents=True, exist_ok=False)

        env = dict(os.environ)
        env.update(env_map)
        if spec.gpu is not None:
            # The substrate pins the device. A job that could choose its own
            # would be able to ignore the preflight that just admitted it.
            env["CUDA_VISIBLE_DEVICES"] = str(spec.gpu).removeprefix("cuda:")

        (d / _META).write_text(
            json.dumps(
                {
                    "handle": handle,
                    "ticket": spec.ticket,
                    "command": list(spec.command),
                    "image": spec.image,
                    "gpu": spec.gpu,
                    "idempotency_key": spec.idempotency_key,
                    "submitted_at": self._clock(),
                    "required_mib": required,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        if spec.idempotency_key is not None:
            # Before the spawn, not after. A marker written after `Popen` would
            # be missing in exactly the window it exists to cover.
            self._key_marker(spec.idempotency_key).write_text(
                json.dumps({"handle": handle, "key": spec.idempotency_key}, sort_keys=True),
                encoding="utf-8",
            )

        wrapper = d / "run.py"
        wrapper.write_text(_WRAPPER, encoding="utf-8")

        stdout = (d / "stdout.log").open("wb")
        stderr = (d / "stderr.log").open("wb")
        try:
            # Detach so closing the agent's shell does not signal the job, and on
            # Windows give it no console of its own — a job that runs for hours
            # must not put a window on the owner's desktop.
            #
            # `creationflags` is passed explicitly rather than folded into the
            # kwargs dict so that the AST check in tests/test_no_console_windows
            # can actually see it. A guard that cannot read the call it guards is
            # not a guard.
            detached = _NEW_PROCESS_GROUP | _DETACHED | _NO_WINDOW if sys.platform == "win32" else 0
            popen_kwargs: dict[str, Any] = {
                "cwd": str(d),
                "env": env,
                "stdout": stdout,
                "stderr": stderr,
                "stdin": subprocess.DEVNULL,
            }
            if sys.platform != "win32":
                popen_kwargs["start_new_session"] = True

            proc = subprocess.Popen(
                [sys.executable, str(wrapper), json.dumps(list(spec.command))],
                creationflags=detached,
                **popen_kwargs,
            )
        finally:
            stdout.close()
            stderr.close()

        meta = json.loads((d / _META).read_text(encoding="utf-8"))
        meta["pid"] = proc.pid
        (d / _META).write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
        return handle

    def poll(self, handle: str) -> JobState:
        return self._state_of(handle)

    def _state_of(self, handle: str) -> JobState:
        d = self._dir(handle)
        if not d.exists():
            return JobState.LOST
        if (d / _DONE).exists():
            return JobState.RESOLVED
        meta_path = d / _META
        if not meta_path.exists():
            return JobState.LOST
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return JobState.LOST
        pid = meta.get("pid")
        if pid is None:
            # Recorded before Popen returned: the process may or may not exist.
            # Reported as still open so `reconcile` decides on the deadline
            # rather than this guessing.
            return JobState.SUBMITTED
        if _alive(int(pid)):
            return JobState.SUBMITTED
        # The liveness probe is not instantaneous — on Windows it shells out to
        # `tasklist`, which takes a quarter of a second — so a job that finished
        # *during* the probe would be judged on a stale answer and declared LOST.
        # That is not cosmetic: any lost job opens the registry's breaker, so a
        # short job polled at the wrong moment would demand a human reset.
        #
        # Re-read the completion record after the probe. It is cheap, and it is
        # the authority: a dead process that left one finished.
        return JobState.RESOLVED if (d / _DONE).exists() else JobState.LOST

    def fetch_artifact(self, handle: str) -> str:
        """Path to the completion record. Always exists once the job is resolved.

        Returns a *reference*, per the registry's contract — the caller reads it,
        the gates adjudicate it, and neither this module nor the registry forms
        an opinion about what it says.
        """
        done = self._dir(handle) / _DONE
        if not done.exists():
            raise SubstrateRefused(f"job {handle} has not produced {_DONE} yet")
        return str(done)

    def completion(self, handle: str) -> CompletionRecord | None:
        """What this substrate actually ran, read back off disk (BRE-31).

        The wrapper writes `done.json` in the job's own directory as its last act,
        and the command recorded there is the list it invoked -- not the list
        somebody says it was asked to invoke. That difference is the point: G-10
        compares this against the command the registry recorded at submission, so
        a job that ran something other than the evaluation is caught even though
        its handle, its exit code and its artifact are all genuine.

        `artifact_sha256` is computed here, at resolution, over the exact bytes
        `fetch_artifact` points at. The registry writes it to its append-only log,
        so editing the artifact afterwards no longer matches what was recorded.

        Returns `None` when the job has not finished or the record is unreadable.
        Honest silence: G-10 names an unchecked field rather than reading absence
        as agreement.
        """
        done = self._dir(handle) / _DONE
        if not done.exists():
            return None
        raw = done.read_bytes()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # A truncated or corrupt record says nothing trustworthy. Refusing to
            # guess is the same fail-closed direction as an unreadable ledger
            # meaning spend is unknown rather than zero.
            return None
        command = payload.get("command")
        if not isinstance(command, list) or payload.get("exit_code") is None:
            return None
        return CompletionRecord(
            handle=handle,
            command=tuple(str(part) for part in command),
            exit_code=int(payload["exit_code"]),
            wall_seconds=float(payload.get("wall_seconds", 0.0)),
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
            # This substrate runs whatever is on disk and has no build step, so
            # it cannot honestly claim a revision. Left `None` rather than filled
            # with the runner's own HEAD, which would attest to the wrong thing.
            source_revision=None,
        )

    def rate_card(self) -> RateCard:
        """`ComputeSubstrate` — this box quotes its own price (BRE-29).

        The registry asks for this at submission time rather than being handed a
        number by the caller. Returning the model itself keeps one rate card and
        one place to override when the compute is rented instead of owned.
        """
        return self.cost_model

    # -- convenience --------------------------------------------------------

    def estimate_usd(self, expected_hours: float) -> float:
        """What a run of roughly this length would cost. Reporting only — the
        caps are checked against `rate_card()`, which the caller cannot supply."""
        return self.cost_model.estimate(expected_hours)

    def cancel(self, handle: str) -> bool:
        """Best-effort stop. True if a signal was delivered."""
        meta_path = self._dir(handle) / _META
        if not meta_path.exists():
            return False
        try:
            pid = int(json.loads(meta_path.read_text(encoding="utf-8"))["pid"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return False
        if not _alive(pid):
            return False
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return True


def _alive(pid: int) -> bool:
    """Whether a pid is currently a live process.

    Cannot distinguish a reused pid from the original, which is exactly why
    `done.json` — not this — is what declares a job finished.

    ## Zombies

    On POSIX a dead child stays in the process table until someone reaps it, and
    `os.kill(pid, 0)` succeeds the whole time. This module deliberately holds no
    `Popen` objects — all state is on disk, so a fresh process can poll — which
    means nothing ever reaps. The naive check therefore reported a killed job as
    alive *forever*, and the registry would sit out its full deadline (six hours
    by default) before calling it lost. On a one-card box that is a slot burned
    per crash.

    Caught by CI on Linux; Windows does not have the failure mode, which is a
    good argument for the matrix.

    Two steps, because there are two cases. If the job is our own child,
    `waitpid` reaps it and we learn it exited. If it is not — the submitter
    exited and init inherited it — we ask the OS for its state.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _alive_windows(pid)

    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            # Our child, and it has now exited. (0, 0) means still running.
            return False
    except (ChildProcessError, OSError):
        # Not our child, which is the normal case after the submitter exits.
        pass

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process. It exists, so treat it as live rather than
        # declaring a job lost on the basis of a permission error.
        return True
    return not _is_zombie(pid)


def _alive_windows(pid: int) -> bool:
    """Liveness via the Win32 API rather than by shelling out to `tasklist`.

    Two reasons, and the first is not about correctness.

    **It opened a console window.** Every `subprocess.run` from a windowless
    parent flashes one, `poll()` calls this, and the polling loops call `poll()`
    every 50 ms — so a test run put hundreds of console windows on the owner's
    screen. A tool that disrupts the machine it runs on does not get used.

    **It was slow enough to be wrong.** `tasklist` costs roughly 250 ms, long
    enough for a short job to finish *inside* the probe, which is what made a
    finished job read as LOST (see `_state_of`). `OpenProcess` answers in
    microseconds, so the window in which the answer goes stale effectively
    closes. The completion record stays authoritative regardless; this just
    stops the probe from being wrong so often.

    Exit code 259 is `STILL_ACTIVE`, and a process that genuinely exits with 259
    is indistinguishable from a running one. That ambiguity is why `done.json`
    decides whether a job finished and this only ever hints.
    """
    # The early return is what lets mypy narrow `sys.platform`. Without it the
    # Windows-only names below are errors when mypy runs on Linux in CI, and a
    # `type: ignore` would then be flagged as unused when it runs on Windows.
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # No such process, or it is gone. Access-denied would also land here; a
        # pid we cannot open is not one this substrate started.
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return bool(code.value == STILL_ACTIVE)
    finally:
        kernel32.CloseHandle(handle)


def _is_zombie(pid: int) -> bool:
    """Whether a pid is an unreaped corpse rather than a running process."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("State:"):
                return "Z" in line.partition(":")[2]
    except OSError:
        # No /proc — macOS, or the process vanished between calls.
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip().startswith("Z")


# The payload runs under this. It exists so that `done.json` is written on every
# exit path, including a crash — a job that vanishes without a completion record
# is indistinguishable from one still running, and the registry would sit on it
# until the deadline.
_WRAPPER = '''\
"""Written by expfactory.local_substrate. Do not edit; it is regenerated per job."""
import json, subprocess, sys, time

started = time.time()
command = json.loads(sys.argv[1])
exit_code, error = 1, None
try:
    exit_code = subprocess.call(
        command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
except Exception as exc:  # noqa: BLE001 - recorded, not handled
    error = f"{type(exc).__name__}: {exc}"
finally:
    finished = time.time()
    with open("done.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "command": command,
                "exit_code": exit_code,
                "error": error,
                "started_at": started,
                "finished_at": finished,
                "wall_seconds": finished - started,
                "stdout": "stdout.log",
                "stderr": "stderr.log",
            },
            f,
            indent=2,
            sort_keys=True,
        )
'''


def describe_local_compute(prober: Callable[[], list[GpuInfo]] = probe_gpus) -> str:
    """Human-facing summary, for a PR body or a readiness check."""
    gpus = prober()
    if not gpus:
        return "no CUDA device visible to nvidia-smi"
    cm = CostModel()
    lines = [f"imputed cost: ${cm.usd_per_hour():.3f}/GPU-hour (electricity + amortisation)"]
    for g in gpus:
        lines.append(
            f"  [{g.index}] {g.name}: {g.free_mib} MiB free of {g.total_mib} MiB, "
            f"{g.power_limit_w:.0f} W limit"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m expfactory.local_substrate` — what compute is available here.

    Exists because "the GPU is wired up" is the kind of claim that should be
    checkable in one command rather than inferred from a training run failing
    six hours in.
    """
    import argparse

    ap = argparse.ArgumentParser(description="Report local compute capacity and imputed cost.")
    ap.add_argument("--root", default="runs/jobs", help="where job directories live")
    args = ap.parse_args(argv)

    print(describe_local_compute())

    root = Path(args.root)
    if root.exists():
        sub = LocalGpuSubstrate(root)
        live = sub.running()
        print(f"\njob root  : {root}")
        print(f"running   : {len(live)} {live if live else ''}")
    else:
        print(f"\njob root  : {root} (not created yet)")

    cm = CostModel()
    print("\nimputed cost of a run, for the registry's caps:")
    for hours in (1, 4, 12, 24):
        print(f"  {hours:>2}h  ${cm.estimate(float(hours)):.2f}")
    print(
        "\nEstimates, not measurements: electricity plus amortisation. They are\n"
        "not zero on purpose: a zero cost makes the registry's caps pass forever\n"
        "while still reading as enforced."
    )
    return 0


__all__ = [
    "CostModel",
    "GpuInfo",
    "LocalGpuSubstrate",
    "SubstrateRefused",
    "describe_local_compute",
    "main",
    "probe_gpus",
]


if __name__ == "__main__":
    sys.exit(main())
