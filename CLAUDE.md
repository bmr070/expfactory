# CLAUDE.md — expfactory

The project handbook. Overview, architecture, commands, standards, and the rules
that must not be broken. Detail lives in the docs this points at.

**Read [`docs/SPEC.md`](docs/SPEC.md) before changing anything in `src/expfactory/`.**

## What this is

A software factory for **empirical work** — ML experiments where CI cannot be the
verifier, because there are no tests to pass, only numbers that may or may not be
real.

> Adopt infrastructure. Build verification. No existing system has a ledger or
> anti-fooling gates, because they all verify code against tests.

The proving workload (drone detection) is a **vehicle, not a ship target**. The
acceptance bar is "the gates behaved correctly," not "the model beat a benchmark."
**A run where every proposed improvement is correctly rejected is a passing run.**
If you find yourself trying to make something get promoted, stop and re-read that.

A hill-climb may carry a published external target, and beating it is a permitted
*outcome*, never an acceptance criterion (L-01). The live one is
[`docs/research/acoustic-drone-detection.md`](docs/research/acoustic-drone-detection.md);
read the figure there, not from memory.

## Architecture

One Python package, `src/expfactory/`. One concern per module. Every seam that
crosses a trust boundary is a `Protocol`, so the runner never imports the
substrate half.

| Group | Modules |
|---|---|
| **Adjudication** | `verifier.py` (the plugin boundary), `harness.py` (original six gates), `gates_v1.py` (tamper, seed-dominance, G-09 group leakage, G-10 attestation), `prereg.py` (G-07/G-08), `holdout.py` |
| **Checking the checker** | `adversarial_suite.py` (known-answer fixtures), `gate_probe.py` (properties, not points), `llm_probe.py` (local model attacks the gates), `selfcheck.py` (boundary test) |
| **Execution** | `runner.py` (outer loop + trust boundary), `sandbox.py` (workspace + secrets), `registry.py` (JobRegistry, holds the GPU credential), `local_substrate.py`, `pipeline.py` |
| **Adapters** | `linear_tracker.py` (**the queue the runner reads**), `github_tracker.py`, `egress.py`, `scorer.py` |
| **Task + provenance** | `drone_audio.py`, `literature.py`, `docs/literature/corpus.json` (data, not substrate) |
| **Wall** | `substrate_guard.py` — refuses any PR editing the verification layer |

**Two substrates, on purpose (W-06).** An agent session lasts minutes and is
untrusted; an experiment run lasts hours and holds the GPU. The agent submits a
job and detaches. Holding an LLM-metered session open to wait on a six-hour job is
the wrong shape at any timeout value.

**Two test lanes.** Gate lane is deterministic, free, every commit. Eval lane
(`llm_probe`) needs a model server and is deliberately outside CI;
`ProbeUnavailable` is a distinct exit so no job reads "no server" as "no findings."

## Commands

```bash
pip install -e ".[dev]"
pytest                                # EXPFACTORY_REQUIRE_DEMO=1 forces the demo test
ruff check src tests
ruff format --check src tests         # src AND tests; CI checks both
mypy                                  # strict on the verification core
python -m expfactory.selfcheck        # boundary test, visible partition
python -m expfactory.selfcheck --heldout   # spends holdout value; be sure
python -m expfactory.local_substrate  # available compute and its imputed cost
python -m expfactory.llm_probe        # adversarial fuzz, needs local Ollama
```

`python -m expfactory.X` needs `PYTHONPATH=src` when the package is not installed.

## Coding standards

- Python 3.11+, `from __future__ import annotations`, full type hints, frozen
  dataclasses for records, `pathlib` over `os.path`.
- `ruff` lints and formats; `mypy --strict` types. Both pinned.
- Trust-crossing seams are `Protocol`, not base classes.
- **Refuse, do not sanitize.** Every sanitizer is lossy, and lossy means two
  inputs can collide.
- **Raise, do not return a bool.** A check whose result can be ignored by
  forgetting to read it is not a check.
- **Fail closed.** An unreadable ledger means spend is unknown, not zero.
- **Parse, do not grep.** Checks about code read the AST; a grep has matched its
  own docstring here twice.
- Comments explain *why*, especially why the obvious alternative is wrong. The
  density is deliberate.

## Workflow

1. Branch first. A hook blocks edits on `main`.
2. Open a PR. Never push to `main`.
3. Green the gate lane before pushing.
4. Expect `substrate-guard` to fail if the change touches the verification layer.
   That is the wall working; it needs a deliberate human override, recorded.
5. If a ticket is half-built, name the unmet boxes in
   [`docs/tickets/NEXT.md`](docs/tickets/NEXT.md). A green summary hides them.

## Invariants — do not break these

Cited by number throughout the source. Each was arrived at the hard way, several
by a test catching a mistake rather than by reasoning.

1. **`promoted` is derived, never settable.** `VerdictBundle` is frozen. If a
   caller can set it, the layer is theatre.
2. **L0 gates run before L1 review, always.** Reversed, a fake result reaches a
   human wearing a persuasive endorsement, which is worse than no review because
   the narrative launders it. **A reviewer may never override a blocking gate.**
3. **The reviewer runs in fresh context, read-only.** Shared context
   rubber-stamps its own reasoning.
4. **Every gate traces to a fixture.** No speculative gates.
5. **Never consult the held-out fixture partition while tuning gates.** The
   factory is held to the discipline it enforces. This caught a real error once.
6. **The agent never holds GPU or tracker credentials.** It submits a job and
   receives an artifact.
7. **Only a human-applied `agent-ready` label is dispatch-eligible.** The tracker
   is untrusted input; anyone who can file a ticket can prompt-inject the factory.
8. **Prose does not ratchet.** A recurring failure becomes a lint rule, hook, CI
   check, boundary test, or gate. Adding a line here is the last resort.
9. **The untrusted party returns evidence, never a verdict.** `AgentSession.run`
   yields a `Candidate`; the runner adjudicates on a verifier the agent cannot
   reach. **You cannot verify a result by asking the thing that produced it what
   the result was.**

## Where to look

| For | Read |
|---|---|
| The specification | [`docs/SPEC.md`](docs/SPEC.md) |
| Traps that cost real time | [`docs/GOTCHAS.md`](docs/GOTCHAS.md) |
| Who is trusted, who adjudicates | [`docs/ROLES.md`](docs/ROLES.md) |
| A word that means two things | [`CONTEXT.md`](CONTEXT.md) |
| Why a decision went that way | [`docs/decisions/`](docs/decisions/) |
| What is left to build | [`docs/tickets/NEXT.md`](docs/tickets/NEXT.md) |
| Ideas already declined on evidence | [`docs/MAP.md`](docs/MAP.md) |
| Linear vs GitHub, and dispatch | [`docs/TRACKING.md`](docs/TRACKING.md) |
| Before dispatching a real agent | [`docs/DISPATCH-READINESS.md`](docs/DISPATCH-READINESS.md) |
| What upstream systems actually do | [`docs/research/`](docs/research/) — read the addenda, they correct the body |
| Reviews, the map, the rendered spec | [`docs/reference/`](docs/reference/) |

## Workflow skills

`.claude/skills/` holds the repeated flows, each encoding rules this repo already
enforces rather than inventing new ones. Invoke via the Skill tool.

| Skill | For |
|---|---|
| `pull-ticket` | Claiming work, and proving it is dispatch-eligible first |
| `triage` | A finding becomes a ticket, in the right place with a lane |
| `hill-climb` | A preregistered attempt against a recorded baseline |
| `run-experiment` | Submit to the GPU substrate, detach, collect later |
| `eval-analysis` | Reading a verdict or a lineage without fooling yourself |
| `add-gate` | A new check, with the fixtures that justify it |
| `ratchet` | A recurring failure becomes the cheapest sufficient check |

## Do not re-propose

Declined on evidence. Full list with reasoning in [`docs/MAP.md`](docs/MAP.md).

- Forking Baton as the runner. Recommended then withdrawn twice.
- Adopting an orchestrator codebase (OpenSymphony, Kata). M2-02: the orchestrator
  is not load-bearing.
- Persona agent org-charts. Only planner, reviewer and triage earn isolation.
- An LLM "Evaluation Agent" judging significance. That is arithmetic on recorded
  seeds and belongs in L0.
- LangGraph / CrewAI / AutoGen as the outer loop. **Boundary:** a coding agent
  *implemented in* LangGraph (Open SWE) is a different claim and is adopted.
- A five-database knowledge stack.
- Deferring cost caps or security to a later phase. Both are day-one, fail-closed.
- Adding `github.com` to the egress allowlist. Datasets are hand-provisioned and
  pinned by commit SHA.
