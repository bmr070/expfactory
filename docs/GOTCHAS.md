# Gotchas

Each of these cost real time. They are separated from [`CLAUDE.md`](../CLAUDE.md)
so the handbook stays readable, but they are not optional reading before touching
the thing they describe.

- **`examples/demo_drone.py` is calibrated by measurement, and by a test.** It
  was not. It planted labels by intent and was wrong about three of four
  scenarios — the "noise" case was the best model in the demo, the "leak" case
  gained +0.0005 over the honest one, and every seed returned an identical
  number, so `gate_seed_variance` had a zero-width band and promoted anything
  positive while appearing to scrutinise it. It had also stopped importing
  entirely at the `Ledger` -> `ExperimentLedger` rename, unnoticed, because
  nothing ran it. `tests/test_demo_drone.py` now asserts every verdict and CI
  sets `EXPFACTORY_REQUIRE_DEMO=1` so it cannot silently skip. **If you retune a
  scenario, measure it — do not relabel it.**
- **`gate_no_leakage` cannot see session-level leakage.** It intersects train and
  eval *sample ids*; clips cut from one continuous recording have distinct ids and
  it passes. That is what G-09 (`gate_no_group_leakage`) is for, and G-09 only
  bites when the task supplies a `DatasetGrouping` to the verifier. Declaring the
  grouping is therefore part of defining a task, not an optional extra. Found in
  the literature, not by a bug: EchoHawk (arXiv:2606.29589) documents it in
  DroneAudioDataset, where 1,332 drone files come from only 257 continuous
  recording sessions. **Do not quote a single inflation figure from memory** —
  the abstract and Table 2 disagree, and that is recorded unresolved in the
  corpus.
- **Test-time adaptation reintroduces that leak after the split**, where G-09
  cannot reach it. See the H5 hazard in `docs/research/acoustic-drone-detection.md`
  before proposing any per-session adaptation.
- **Local GPU cost is imputed, and must never be zero.** The registry's caps and
  breaker are in dollars; hardware you own has no invoice. `CostModel` charges
  ~$0.09/GPU-hour so the caps still bind. Setting it to zero does not "simplify
  free compute" — it silently disables every cap while leaving them looking
  enforced. Same shape as the zero-width noise band.
- **`x > cap` is not a cap check.** `NaN > anything` is False, so a non-finite
  estimate is under every cap, and so is `-100.0` — which then *subtracts* from
  the trailing-day total and buys the next job over the limit. Both were live in
  `JobRegistry.submit` and both were reproduced by an external review (BRE-29).
  Anything compared to a cap is now refused first unless it is finite and
  non-negative, and refused means raised, never clamped: a cost that cannot be
  read means spend is unknown, not zero.
- **Nobody submitting a job may name its price.** The substrate quotes it
  (`ComputeSubstrate.rate_card()`), over the job's deadline. If you find yourself
  adding a cost argument back to `submit` because a caller "knows better", that
  is W-12's finding — a self-reported cost cap is not a cap — arriving again.
  The lever the caller does have is the deadline, and it is priced.
- **The rate card is not keyed on the GPU.** `submit`/`poll`/`fetch_artifact`
  name no hardware anywhere and neither does `RateCard`; it prices a window in
  seconds. A GPU SKU in that signature would make the registry hardware-aware for
  the first time, and the next substrate may have no GPU to key on.
- **A pid is not proof a job is alive.** Pids are reused, and the liveness probe
  costs ~250 ms on Windows, long enough for a short job to finish inside it.
  `done.json` is the authority; the probe is a hint. Getting this backwards
  marked finished jobs LOST, which opens the breaker and needs a human to reset.
- **The local card drives the display.** ~1.2 GB is gone before any job starts and
  it moves when a browser opens, so `reserve_mib` headroom is not optional. 12 GB
  total is also a real ceiling on what hypotheses are runnable here.
- **The dominance gate in `gates_v1.py` was wrong on first implementation**
  (inverted ratio) and passed nothing. A fixture caught it. If you modify it,
  verify against *both* suite partitions.
- **`mean_metric` is NaN in the deterministic lane.** It serialises as `null`,
  not the bare `NaN` token Python emits by default, because the ledger must be
  readable by whatever language the runner ends up in.
- **The tamper gate matches path basenames**, so moving harness files around does
  not weaken it — but renaming one silently would. **Adding a module to
  `src/expfactory/` fails the suite** until it is classified in `_HARNESS_PATHS`
  or `_NOT_SUBSTRATE`, plus a line in `.github/CODEOWNERS`. Two PRs that each add
  a module therefore conflict by construction; both entries belong.
- **The agent never computes its own metric.** It submits *predictions*; `scorer.py` holds the labels and scores them (T-01). A model artifact was rejected: loading one executes agent code inside the process holding the labels. Feedback goes through a **Ladder** — a score is reported only when it clearly beats the incumbent, because the holdout leak is driven by feedback, not by query count.
- **"Holdout" names two different things here.** The experiment holdout (a model's lockbox) and the held-out fixture partition (invariant 5). Both have budgets and they protect different parties — see `CONTEXT.md`.
- **G-10: a candidate must cite a job the registry issued.** The agent returning
  evidence instead of a verdict left it one move — describe runs that never
  happened. Fabricated evidence is the same *shape* as real evidence, so no gate
  reading the numbers can separate them; only the append-only job log can, and
  the agent does not write it. Like G-09, the source is a verifier constructor
  argument, and without one the gate warns rather than blocks. **It cannot check
  that the metric is right** — the agent still writes the code that computes it,
  which needs a trusted evaluator (GH#39).
- **The agent returns a `Candidate`, never a `VerdictBundle`.** The runner
  adjudicates, on a verifier the agent cannot reach. It briefly worked the other
  way, with the runner *checking* an agent-supplied verdict — which cannot work,
  because a sandboxed agent can build a bundle with `promoted=True` and exactly
  the gate names the check wants. You cannot verify a result by asking the thing
  that produced it what the result was. The agent still authors the *evidence*;
  closing that needs the numbers to come from the compute substrate, not the
  agent (W-06, and GH#33's remaining half).
- **`require_prereg=True`, the `PreregStore` and G-09's `DatasetGrouping` are the
  runner's to set.** They became enforceable only once the runner owned the
  verifier. A runner built without them still refuses its own verdicts — the
  `required_gates` check now catches a misconfigured *runner* rather than a
  misconfigured agent.
- **The runner runs where the compute does.** A cloud-hosted runner cannot reach
  a GPU under the desk without exposing it, so while compute is local the runner
  is a local daemon (M2-08). An explicit coupling, not a preference: moving to
  rented compute reopens the hosted options.
- **PR authorship is not load-bearing.** `substrate_guard` asks what changed,
  never who, so an agent runtime that opens PRs as the triggering human degrades
  CODEOWNERS without touching the wall. The identity that *is* load-bearing is
  the Linear one, because `label_actor` reads it — and that one is free.
- **The egress allowlist is code, and exact-match only.** No env var, no config
  file, no runtime API — inside a sandbox the agent can set env vars and write
  config, but it cannot merge a PR, and that is the whole control. Matching is
  exact because `endswith` accepts `evil-huggingface.co`, substring accepts
  `huggingface.co.evil.example`, and both accept `huggingface.co@evil.example`.
  Host matching alone is not integrity either: pin the SHA-256, and record where
  that digest came from (`digest_source`).
- **Neither a baseline nor a guardrail threshold is agent-declared.** Both are
  read from the parent's recorded verdict. A threshold the agent names is
  decorative.
- **A preregistration's baseline is read from the ledger, never from the prereg.**
  The agent authors the prereg; if it could also name the number it is measured
  against, G-07 would be theatre. A confirmatory run needs a recorded parent. A
  number typed into a constant is worth exactly what an invented one is worth.
- **Two ledgers, two names.** `verifier.Ledger` is *the* ledger (verdicts +
  preregistrations). `harness.ExperimentLedger` is the prototype's, kept behind
  the boundary. `ledger_ctx` is typed `HoldoutSource`, so handing over the wrong
  one is now a type error rather than a runtime crash.
- **`subprocess` on Windows opens a console window** unless every call site passes
  `CREATE_NO_WINDOW`. Seven sites once did not. Prefer not shelling out at all
  where a file read will do — `drone_audio` reads `.git/HEAD` rather than calling
  `git rev-parse`, and needs no PATH assumption.
