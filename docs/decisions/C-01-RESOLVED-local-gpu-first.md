---
id: C-01
parent: wayfinder:map2
labels: [wayfinder:decision]
mode: HITL
status: RESOLVED
raised: 2026-07-26
resolved: 2026-07-26
---

# C-01 — Local GPU now, edge or rented later

## The ask

Use the owner's local GPU for now, and keep the ability to move to edge or
external compute later.

## What the hardware actually is

Measured, not assumed:

```
NVIDIA GeForce RTX 4070   12282 MiB total, ~11000 MiB free at idle
driver 610.74, CUDA UMD 13.3, WDDM, 200 W board limit
```

Three consequences worth writing down before anything is built on it:

- **12 GB is the ceiling.** Fine for the acoustic target and for small vision
  models. Not fine for VLM post-training: GRPO samples 8–64 completions per
  prompt, and H5 in the research plan will not fit as written. That is a
  constraint on the *hypotheses*, discovered before a run rather than during one.
- **WDDM, and the card drives the display.** ~1.2 GB is gone before any job
  starts, and it moves when a browser opens. Hence `reserve_mib`.
- **torch is not installed.** It is a workload dependency, not a substrate one —
  the substrate launches processes and does not import it. Deferred until a
  hypothesis needs it, rather than pulling 2.5 GB now.

## Decision

**Implement `LocalGpuSubstrate` behind the existing `ComputeSubstrate` protocol.**
M2-03 already chose "adopt the substrate's own job primitive, keep a thin
`JobRegistry`". Nothing above the seam changes; the local box is simply the first
provider to sit under it.

### Detachment is the requirement, not an optimisation

W-06 splits the lane: the agent writes in minutes, the experiment runs for hours.
A job tied to the submitting process makes that split decorative.

So a job is a detached OS process — `start_new_session` on POSIX,
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` on Windows — and **all state lives
on disk**. `poll()` reads files. A fresh interpreter after a reboot resolves the
same jobs. `test_survives_the_submitting_process` spawns a separate interpreter,
submits, lets it exit, and resolves the job from this one.

### Completion is a file, not a pid

`done.json` is written by a wrapper on every exit path, including a crash. A pid
check cannot be the authority: pids are reused, so a reused pid reads as a live
job.

This produced the one real bug in the build. `poll` checked for `done.json`,
then checked liveness — and the liveness probe on Windows shells out to
`tasklist`, which takes about 250 ms. A short job finished *during* the probe, so
a stale "not alive" answer marked a finished job LOST. Not cosmetic: **any lost
job opens the registry's breaker**, which requires a human to reset. A fast job
polled at the wrong moment would have demanded manual intervention. Fixed by
re-reading the completion record after the probe; the record outranks the probe.

### Cost is imputed, and never zero

The registry's caps and breaker are denominated in dollars because the design
assumed rented compute. A local GPU has no invoice, and the obvious
implementation returns `0.0`.

That is the same failure as the demo's zero-width noise band. A zero estimate
makes `per_job_cap_usd` and `per_day_cap_usd` unsatisfiable by construction:
every check passes, the breaker never trips on spend, and the cap reads as
enforced while enforcing nothing.

So local runs carry an imputed rate — marginal electricity plus amortisation:

```
electricity   0.200 kW x $0.15/kWh    = $0.030/h
amortisation  ~$600 card / ~10,000 h  = $0.060/h
                                        --------
                                        $0.090/GPU-hour
```

The precision is fictional; the magnitude is not. A twelve-hour run imputes to
$1.08, which is enough for a sanely-set cap to bind. `test_local_cost_makes_the_
daily_cap_bind` submits through a real `JobRegistry` and is refused;
`test_a_zero_cost_model_would_disable_the_caps` documents exactly what breaks if
someone later "simplifies" this because local compute is free.

Moving to rented compute changes the rate, not the mechanism.

### The seam is proven, not asserted

"Swappable later" is only true if the protocol holds a second implementation.
`tests/test_substrate_conformance.py` is parameterised over `LocalGpuSubstrate`
and a `FakeRemoteSubstrate` that shares no code with it and models the
spawn-and-poll shape Modal has. **Adding a real remote substrate to `SUBSTRATES`
is its acceptance test.**

The contract covers only what `JobRegistry` depends on: unique handles over the
substrate's lifetime, idempotent polling, unknown handles reporting LOST rather
than raising, and artifacts available only after completion. Concurrency limits
are explicitly *provider policy* and out of contract — the local one refuses a
second simultaneous job on a one-card box, and a remote one should not.

## Preflight, because the alternative is finding out at hour three

Refusals happen in milliseconds, before anything starts:

- more jobs than `max_concurrent` (default 1 — two training jobs on one 12 GB
  card do not share it gracefully)
- a GPU requested when `nvidia-smi` reports none, rather than silently running on
  CPU for six hours
- a VRAM request larger than free memory minus `reserve_mib`

## Consequences

- 236 tests (was 197). `python -m expfactory.local_substrate` reports capacity
  and imputed cost.
- `local_substrate.py` is in `_HARNESS_PATHS` and CODEOWNERS: it imputes the
  number the caps are checked against, so editing it to zero disables them.
- No behaviour above the seam changed.

## Not decided here

- **Which remote provider, and when.** M2-03 named Modal; nothing here commits to
  it. The conformance suite is what makes that a later, cheap decision.
- **Whether a GPU lease should outlive a single job.** Today concurrency is a
  count. Real queueing on one card wants a lease with a deadline.
- **torch and a CUDA build.** Deferred until a hypothesis needs it; H2 and H3 in
  the research plan do not.
- **Whether 12 GB rules out H5 entirely** or only its full-size form. Worth
  measuring before dropping it.
