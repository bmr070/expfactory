# AGENTS.md — expfactory

Table of contents for agents working in this repo. Progressive disclosure: this
file orients you and points at the real documents. It is not the specification.

**Read `docs/SPEC.md` before changing anything in `src/expfactory/`.**

---

## What this is

A software factory for **empirical work** — ML experiments where CI cannot be the
verifier, because there are no tests to pass, only numbers that may or may not be
real. The thesis:

> Adopt infrastructure. Build verification. No existing system has a ledger or
> anti-fooling gates, because they all verify code against tests.

The proving workload (drone detection) is a **vehicle, not a ship target**. The
acceptance bar for the factory is "the gates behaved correctly," not "the model
beat a benchmark." **A run in which every proposed improvement is correctly
rejected is a passing run.** If you find yourself trying to make something get
promoted, stop and re-read this paragraph.

**Amended 2026-07-26 (L-01).** A hill-climb may now carry a *published external
target*, and beating it is a permitted **outcome** — never an acceptance
criterion. `promoted` is still derived only from the gates. The live target is
[`docs/research/acoustic-drone-detection.md`](docs/research/acoustic-drone-detection.md),
and note which number it competes against: the honest session-grouped 0.745, not
the leaked 0.796.

## Layout

| Path | What |
|---|---|
| `src/expfactory/verifier.py` | The plugin boundary. `Verifier.run(candidate) -> VerdictBundle`. |
| `src/expfactory/harness.py` | The original six gates, behind the boundary. |
| `src/expfactory/gates_v1.py` | Tamper gate, seed-dominance gate, and G-09 group leakage. |
| `src/expfactory/literature.py` | Paper/mechanism/hypothesis provenance. Substrate; the corpus it reads is not. |
| `docs/literature/corpus.json` | The reading list and the mechanisms extracted from it. Data, not substrate. |
| `docs/research/` | Live hill-climb targets, their protocols and their published bars. |
| `src/expfactory/holdout.py` | Durable holdout query budget, atomic across restarts. |
| `src/expfactory/adversarial_suite.py` | Known-answer fixtures, visible + held-out. |
| `src/expfactory/pipeline.py` | `run_and_record`: train → verify → append. |
| `src/expfactory/prereg.py` | Preregistration record + the G-07 gate. |
| `src/expfactory/selfcheck.py` | The boundary test. |
| `src/expfactory/registry.py` | JobRegistry + ComputeSubstrate seam. Holds the GPU credential. |
| `src/expfactory/local_substrate.py` | The local GPU behind that seam. Detached jobs, imputed cost. |
| `src/expfactory/runner.py` | The outer loop and the trust boundary: what gets worked on, and who adjudicates it. |
| `src/expfactory/github_tracker.py` | GitHub Issues adapter for `Tracker`. |
| `src/expfactory/substrate_guard.py` | PR-level wall: refuses any PR editing the verification layer. |
| `examples/demo_drone.py` | Worked example. End-to-end check on the gate set; pinned by `tests/test_demo_drone.py`. |
| `docs/SPEC.md` | The specification. Start here. |
| `docs/DISPATCH-READINESS.md` | What must be true before a real agent runs here. |
| `docs/MAP.md`, `docs/MAP2.md` | Closed decisions and open territory. |
| `docs/decisions/` | One file per decision, with rationale. |
| `provision/` | CODEOWNERS, labels — needs the owner's accounts, applied by hand. |
| `docs/TRACKING.md` | Linear vs GitHub Issues, and the `agent-ready` dispatch rule. |

## Commands

```bash
pip install -e ".[dev]"
pytest                              # 269 tests (set EXPFACTORY_REQUIRE_DEMO=1 to force the demo test)
ruff check src tests
ruff format --check src tests
mypy                                # strict on the whole verification core
python -m expfactory.selfcheck      # boundary test, visible partition
python -m expfactory.local_substrate  # what compute is available, and its imputed cost
```

## Invariants — do not break these

Each was arrived at the hard way; several were found by a test catching a mistake,
not by reasoning. Breaking one silently guts the verification layer.

1. **`promoted` is derived, never settable.** `VerdictBundle` is frozen. If a
   caller can set it, the whole layer is theatre.
2. **L0 gates run before L1 review, always.** Reversing the order lets a fake
   result reach a human wearing a persuasive LLM endorsement — worse than no
   review, because the narrative launders it. **A reviewer may never override a
   blocking gate.**
3. **The reviewer runs in fresh context, read-only.** A reviewer sharing the
   implementer's context rubber-stamps its own reasoning.
4. **Every gate traces to a fixture.** No speculative gates; the set must not
   bloat.
5. **Never consult the held-out fixture partition while tuning gates.** The
   factory is held to the holdout discipline it enforces on experiments. This
   already caught a real error once. CI runs the visible partition only, by
   design — see `selfcheck.py`.
6. **The agent never holds GPU or tracker credentials.** It submits a job and
   receives an artifact.
7. **Only a human-applied `agent-ready` label is dispatch-eligible.** The tracker
   is untrusted input: anyone who can file a ticket can prompt-inject the factory.
8. **Prose does not ratchet.** A recurring failure becomes a lint rule, hook, CI
   check, boundary test, or gate. Adding a line to this file is the last resort,
   not the first.
9. **The untrusted party returns evidence, never a verdict.** `AgentSession.run`
   yields a `Candidate`; the runner adjudicates it on a verifier the agent cannot
   reach. Checking an agent-supplied verdict cannot work — it can be built with
   `promoted=True` and whatever gate names the check wants. **You cannot verify a
   result by asking the thing that produced it what the result was.**

## Gotchas

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
  the literature, not by a bug: EchoHawk (arXiv:2606.29589) measures the inflation
  at 0.796 -> 0.745 Pd@1%FAR.
- **Test-time adaptation reintroduces that leak after the split**, where G-09
  cannot reach it. See the H5 hazard in `docs/research/acoustic-drone-detection.md`
  before proposing any per-session adaptation.
- **Local GPU cost is imputed, and must never be zero.** The registry's caps and
  breaker are in dollars; hardware you own has no invoice. `CostModel` charges
  ~$0.09/GPU-hour so the caps still bind. Setting it to zero does not "simplify
  free compute" — it silently disables every cap while leaving them looking
  enforced. Same shape as the zero-width noise band.
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
  not weaken it — but renaming one silently would.
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
- **Neither a baseline nor a guardrail threshold is agent-declared.** Both are
  read from the parent's recorded verdict. A threshold the agent names is
  decorative.
- **A preregistration's baseline is read from the ledger, never from the prereg.**
  The agent authors the prereg; if it could also name the number it is measured
  against, G-07 would be theatre. A confirmatory run needs a recorded parent.
- **Two ledgers, two names.** `verifier.Ledger` is *the* ledger (verdicts +
  preregistrations). `harness.ExperimentLedger` is the prototype's, kept behind
  the boundary. `ledger_ctx` is typed `HoldoutSource`, so handing over the wrong
  one is now a type error rather than a runtime crash.

## Do not re-propose

Declined on evidence; re-suggesting wastes a session. Full list in `docs/MAP.md`.

- Forking Baton as the runner. Recommended then withdrawn twice.
- Persona agent org-charts. Only three roles earn isolation: planner, reviewer,
  triage.
- An LLM "Evaluation Agent" deciding whether results are significant. That
  question is arithmetic on recorded seeds and belongs in L0.
- LangGraph / CrewAI / AutoGen as the outer loop. **Boundary:** a coding agent
  *implemented in* LangGraph (Open SWE) is a different claim and is **adopted** —
  see `docs/decisions/M2-07-RESOLVED-open-swe.md`.
- A five-database knowledge stack.
- Deferring cost caps or security to a later phase. Both are day-one, fail-closed.

## Agent skills

### Issue tracker

Work lives in **Linear** (team `Brett`, project `expfactory — empirical software
factory`). GitHub holds code, PRs and CI, not the work queue, and there is no
sync between them. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context. Decision records live in `docs/decisions/`, **not** `docs/adr/`.
See `docs/agents/domain.md`.
