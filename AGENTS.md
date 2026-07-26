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

The proving workload (multimodal drone-vs-bird detection) is a **vehicle, not a
ship target**. The acceptance bar is "the gates behaved correctly," not "the model
beat a benchmark." **A run in which every proposed improvement is correctly
rejected is a passing run.** If you find yourself trying to make something get
promoted, stop and re-read this paragraph.

## Layout

| Path | What |
|---|---|
| `src/expfactory/verifier.py` | The plugin boundary. `Verifier.run(candidate) -> VerdictBundle`. |
| `src/expfactory/harness.py` | The original six gates, behind the boundary. |
| `src/expfactory/gates_v1.py` | Tamper gate + baseline-free seed-dominance gate. |
| `src/expfactory/holdout.py` | Durable holdout query budget, atomic across restarts. |
| `src/expfactory/adversarial_suite.py` | Known-answer fixtures, visible + held-out. |
| `src/expfactory/pipeline.py` | `run_and_record`: train → verify → append. |
| `src/expfactory/prereg.py` | Preregistration record + the G-07 gate. |
| `src/expfactory/selfcheck.py` | The boundary test. |
| `src/expfactory/registry.py` | JobRegistry + ComputeSubstrate seam. Holds the GPU credential. |
| `src/expfactory/runner.py` | The outer loop and the trust boundary: what gets worked on. |
| `src/expfactory/github_tracker.py` | GitHub Issues adapter for `Tracker`. |
| `examples/demo_drone.py` | Demo only. **Miscalibrated — see Gotchas.** |
| `docs/SPEC.md` | The specification. Start here. |
| `docs/DISPATCH-READINESS.md` | What must be true before a real agent runs here. |
| `docs/MAP.md`, `docs/MAP2.md` | Closed decisions and open territory. |
| `docs/decisions/` | One file per decision, with rationale. |
| `provision/` | CODEOWNERS, labels — needs the owner's accounts, applied by hand. |
| `docs/TRACKING.md` | Linear vs GitHub Issues, and the `agent-ready` dispatch rule. |

## Commands

```bash
pip install -e ".[dev]"
pytest                              # 154 tests
ruff check src tests
ruff format --check src tests
mypy                                # strict on the whole verification core
python -m expfactory.selfcheck      # boundary test, visible partition
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

## Gotchas

- **`examples/demo_drone.py` is miscalibrated.** A scenario planted as "seed
  noise" turned out to be a genuine improvement, so the demo validates less than
  it appears to. It lives outside the package for this reason. **Trust the
  adversarial suite, not the demo.**
- **The dominance gate in `gates_v1.py` was wrong on first implementation**
  (inverted ratio) and passed nothing. A fixture caught it. If you modify it,
  verify against *both* suite partitions.
- **`mean_metric` is NaN in the deterministic lane.** It serialises as `null`,
  not the bare `NaN` token Python emits by default, because the ledger must be
  readable by whatever language the runner ends up in.
- **The tamper gate matches path basenames**, so moving harness files around does
  not weaken it — but renaming one silently would.
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
