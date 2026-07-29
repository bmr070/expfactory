---
name: run-experiment
description: Submit a compute job to the GPU substrate, detach, and collect the artifact later without blocking an agent session. Use when running training, launching a GPU job, a long run, or anything measured in hours. Trigger words - run the experiment, train, submit a job, GPU, long run, detach, unattended.
---

# Run an experiment

An agent session lasts minutes. A training run lasts hours. Holding the first
open to wait for the second is the wrong shape at any timeout value (W-06).

## Steps

1. **Preflight before spending.** `python -m expfactory.local_substrate` reports
   what is available and its imputed cost. A GPU requested when none exists, or a
   VRAM request larger than free memory minus `reserve_mib`, is refused in
   milliseconds rather than discovered after six hours on CPU.

2. **Submit through the registry, never directly.** `JobRegistry.submit` checks
   the per-job cap, the per-day cap and the breaker *before* anything starts, and
   writes the handle to the append-only log before the job runs. The agent never
   holds the GPU credential (invariant 6).

3. **Pick the deadline, not the price.** `submit(spec, deadline_s=...)` takes no
   cost: the substrate quotes it from its own rate card over the deadline you
   chose, because a price the submitting side names is a request rather than a
   cap (W-12, BRE-29). A shorter deadline is a cheaper job, and it is the only
   lever there is. A non-finite, zero or negative deadline is refused outright.

4. **Detach.** Return `Submitted(handle)` from the agent session and end it. Not
   a `Candidate`, not a partial verdict, not a prediction of how it will go. The
   ticket parks in `Running Unattended` and the registry owns the run.

5. **Collect on a later tick.** `collect_finished()` returns finished jobs with
   their waiting ticket; a `ResultCollector` turns the artifact reference into a
   `Candidate`, which reaches the same adjudication a synchronous one does.

6. **Cite the handle in the candidate.** G-10 requires the handle be one the
   registry issued. A candidate quoting a handle with no record describes a run
   this factory never started.

## Traps

- **A pid is not proof a job is alive.** Pids are reused, and the liveness probe
  costs ~250 ms on Windows, long enough for a short job to finish inside it.
  `done.json` is the authority; the probe is a hint.
- **Never auto-retry a lost job.** Its state is unknown, so it may still be
  running and still spending. Resubmitting can double-spend. It goes to a human.
- **The local card drives the display.** ~1.2 GB is gone before any job starts,
  and it moves when a browser opens. `reserve_mib` headroom is not optional.
  12 GB total is a real ceiling on which hypotheses are runnable here.
- **Cost is imputed, never zero.** Hardware you own has no invoice, and a zero
  cost model silently disables every cap while leaving them looking enforced.
- **`estimate > cap` was never a cap check.** `NaN` is under every cap and a
  negative estimate is too, and the negative one also lowers the trailing-day
  total. Numbers reaching a cap are refused unless finite and non-negative, and
  refused means raised — do not clamp one, a cost you cannot read means spend is
  unknown.
- **The prober and a training run cannot both hold the card.**

## Failure modes and where they land

| What happened | Where the ticket goes |
|---|---|
| Job finished, artifact readable | adjudicated, then `In Review` |
| Job finished, artifact unreadable | `Needs Human`, not retried |
| Job passed its deadline silently | `Needs Human`, breaker opens |
| Breaker already open | not dispatched at all |

## Related

`src/expfactory/registry.py`, `src/expfactory/local_substrate.py`,
`docs/decisions/M2-03-RESOLVED-experiment-queue.md`, `docs/decisions/C-01-RESOLVED-local-gpu-first.md`.
