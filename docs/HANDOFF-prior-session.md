# Handoff — AI software factory (empirical lane)

**Next session:** Claude Code, with filesystem, git and test execution.
**Prior session:** design + TDD in a sandboxed container. No git history exists — the code was written
without version control and needs to be committed as a first act.

---

## 1. What this is

A software factory for **empirical work** — ML experiments where CI cannot be the verifier because
there are no tests to pass, only numbers that may or may not be real. The thesis in one line:

> Adopt infrastructure. Build verification. No existing system has a ledger or anti-fooling gates,
> because they all verify code against tests.

The proving workload is multimodal drone-vs-bird detection, used as a **vehicle, not a ship target**.
The acceptance bar is "the gates behaved correctly," not "the model beat a benchmark." A run in which
every proposed improvement is correctly rejected is a **passing** run.

---

## 2. Artifacts — read these, don't reconstruct them

All currently sitting in the export directory; they need to be placed into a real repo (§6).

| Path | What it is | Read when |
|---|---|---|
| `factory-spec.html` | Full architecture reference, rev 2. Schematic, layer stack, agents, skills, gate set, revision log, worked end-to-end flow. | **First.** Single best orientation. |
| `MAP.md` | Map I — 12 closed decisions with rationale, plus post-map decisions and an explicit rejections list. | Before proposing any architecture change. |
| `MAP2.md` | Map II — open territory: the loop split, experiment queue, preregistration. | Before starting new design work. |
| `tickets/W-01..W-12` | Map I decision tickets. Seven carry `## AMENDMENT` or `## SUPERSEDED` sections — **read those, the headline verdicts are stale in places**. | When a decision seems wrong. |
| `tickets2/M2-01..M2-07` | Map II open questions. Frontier is M2-01, M2-04, M2-07. | When picking up new work. |
| `issues/01..11` | Build tickets as vertical slices, dependency-ordered. 02/03/04/05 are done. | When implementing. |
| `expfactory/` | The working code. 34 tests, all green. | Always. |
| `expfactory/provision/` | CODEOWNERS, labels.json, provisioning README. Needs the user's accounts to apply. | Ticket 01. |

---

## 3. Code state

```
expfactory/
  verifier.py            Verifier protocol · frozen VerdictBundle · GateVerifier ·
                         ExitCodeVerifier (CI adapter) · Ledger              [7 tests]
  gates_v1.py            DiffEvidence · tamper gate · baseline-free dominance [7 tests]
  holdout.py             HoldoutBudget, atomic + durable across restart       [6 tests]
  adversarial_suite.py   labeled fixtures, visible + held-out partitions      [6 tests]
  pipeline.py            run_and_record — train → verify → append             [4 tests]
  harness.py             original six gates, retained behind the boundary     [4 tests]
  demo_drone.py          synthetic sensor-fusion demo (sklearn)
```

`pytest` → **34 passed**. Requires `pytest`, `numpy`, `scikit-learn`, `scipy`.

Build tickets **02, 03 (2 of 3), 04, 05 are complete**. Tickets 06–11 need infrastructure that did not
exist in the prior environment (GPU substrate, live tracker, durable store, subagent runtime).

---

## 4. Invariants — do not break these

These are load-bearing. Each was arrived at the hard way; several were discovered by a test catching a
mistake, not by reasoning.

1. **`promoted` is derived, never settable.** `VerdictBundle` is frozen. If a caller can set it, the
   whole verification layer is theatre.
2. **L0 gates run before L1 review, always.** Reversing the order lets a fake result reach a human
   wearing a persuasive LLM endorsement — worse than no review, because the narrative launders it.
   **A reviewer may never override a blocking gate.**
3. **The reviewer runs in fresh context, read-only.** A reviewer sharing the implementer's context
   rubber-stamps its own reasoning.
4. **Every gate traces to a fixture** in the adversarial suite. No speculative gates; the set must not
   bloat.
5. **Never consult the held-out fixture partition while tuning gates.** The factory is held to the same
   holdout discipline it enforces on experiments. This already caught a real error once — see §5.
6. **The agent never holds GPU or tracker credentials.** It submits a job and receives an artifact.
7. **Only a human-applied `agent-ready` label is dispatch-eligible.** The tracker is untrusted input;
   anyone who can file a ticket can prompt-inject the factory.
8. **Prose does not ratchet.** A recurring failure becomes a lint rule, hook, CI check, boundary test,
   or gate. Adding a line to `AGENTS.md` is the last resort, not the first.

---

## 5. Known defects and open threads

**The refactor is queued and not started.** Findings from a code review of the current code, in the
order they should be tackled:

- **Candidate 3 — delete `VerdictBundle.with_exp_id`.** It exists only to serve a test helper. Pure
  deletion; let the test control the id through the seam instead.
- **Candidate 1 — `Candidate.runs` is `Sequence[dict[str, Any]]`.** Primitive obsession. Should take
  `Sequence[RunResult]` so malformed data fails at the boundary with a clear message, not deep inside
  gate evaluation.
- **Candidate 2 — `VerdictBundle` construction is duplicated** across `GateVerifier.run` and
  `ExitCodeVerifier.run`. Named constructors (`from_experiment`, `from_exit_code`). **Priority raised:**
  the runner is no longer assumed to be Python, so the seam is likely a subprocess/artifact-file
  boundary — these constructors should produce a clean *serialized* form, not merely deduplicate.
- **Candidate 4 — `Experiment`/`VerdictBundle` feature envy.** Deferred deliberately; may dissolve once
  candidate 2 lands. Re-scan after, don't pre-decide.

**Two cautionary notes about the existing code:**

- ~~The demo in `demo_drone.py` has a **miscalibrated scenario**~~ — **RESOLVED**, and it was worse than
  this note knew. Measurement found three of four scenarios wrong, a seed-variance band of exactly zero,
  and a file that had not imported since the `Ledger` rename. Recalibrated against measured numbers and
  pinned by `tests/test_demo_drone.py`. See `AGENTS.md#gotchas`.
- The dominance gate in `gates_v1.py` was **wrong on first implementation** (inverted ratio) and passed
  nothing. The fixture caught it. If you modify that gate, verify against both suite partitions.

**Third gate hardening incomplete:** ticket 03 asked for three hardenings; the too-good recalibration is
folded into the dominance gate, tamper and durable holdout are done. G-07 preregistration is designed
but not built — that's M2-04 (design) then M2-05 (build).

---

## 6. Suggested first actions

Ranked. The first three are doable entirely in Claude Code; the rest need the user.

1. **Initialise the repo and commit.** No git history exists. `git init`, place `expfactory/` at the
   root, commit the 34 passing tests as the baseline before touching anything.
2. **Run the refactor** — candidates 3 → 1 → 2 from §5, red-green, keeping all 34 tests green. If a test
   *has* to change, that test was coupled to an implementation detail and is telling you something.
3. **Set up strict CI** — typecheck, lint, tests, and a boundary test. The factory's own repo should be
   agent-ready before it dispatches anything. Write `AGENTS.md` while doing it.
4. **M2-04 (design) then M2-05 (build)** — the preregistration gate. Design is a genuine open problem:
   preregistration assumes a confirmatory study, and a hill-climb is exploratory by construction. Needs
   a decision before code.
5. **M2-01 — the timeout test.** Needs the user's machine and accounts. Install an orchestrator, drive
   one trivial ticket, then run a hook that sleeps 20 minutes and observe whether it is killed. **This
   single observation may collapse two other tickets.** Run it against Open SWE too (M2-07).
6. **Ticket 01 provisioning** — needs the user's Linear and GitHub accounts. Artifacts are ready in
   `expfactory/provision/`; do not attempt to create accounts or apply settings autonomously.

---

## 7. Do not re-propose these

Each was raised (several repeatedly) and declined on evidence. Re-suggesting them wastes a session.

- **Fork Baton as the runner.** Recommended, then withdrawn twice. The "same language" argument fails —
  a subprocess boundary costs almost nothing. Retained only as a ~200-line reading exercise.
- **Persona agent org-charts** (Product Manager / Architect / QA / Data Scientist / Factory Manager /
  Memory Agent). Proposed by four separate stack recommendations. Only three roles earn isolation:
  planner (expensive, no write), reviewer (fresh context, read-only), triage (cheap, bounces bad tickets).
- **An LLM "Evaluation Agent" deciding whether results are significant.** The most dangerous proposal
  seen. That question is arithmetic on recorded seeds and belongs in L0.
- **LangGraph / CrewAI / AutoGen as the outer loop.** Category error — the coding agent already *is* the
  agent loop. **Important boundary:** a coding agent *implemented in* LangGraph (Open SWE) is a
  different claim and is a live candidate. Do not dismiss it by association.
- **Five-database knowledge stack** (Qdrant + Postgres + Neo4j + S3 + tracking). One embedded index plus
  markdown capsules covers it.
- **Deferring cost caps or security to a later phase.** Both are day-one, fail-closed.

---

## 8. Suggested skills

| Skill | Use for |
|---|---|
| `/tdd` | The refactor and the preregistration gate. Confirm seams before writing tests — that gate is in the skill for a reason. |
| `/wayfinder MAP2.md` | Resolving M2 tickets. Charting is done; this is resolving mode, one ticket per session (research excepted). |
| `/grilling` | M2-04 specifically — the preregistration design needs the decision tree walked, not a guess. |
| `/code-review` | After the refactor. Standards ∥ Spec as parallel fresh-context passes. |
| `/codebase-design` | If candidate 4 (Experiment/VerdictBundle) turns out not to dissolve. |
| `/to-tickets` | Only after M2 decisions close. Slicing fog produces sliced fog. |

Do **not** run `/improve-architecture` yet — its output is already captured in §5, and re-running it
before the refactor lands will just re-derive the same four candidates.

---

## 9. Environment notes

- Python 3.13. Install with `pip install pytest numpy scikit-learn scipy`.
- No secrets, tokens or credentials exist in any artifact. The provisioning README describes where
  credentials *should* live (runner secret store, never an agent workspace) but contains none.
- `provision/CODEOWNERS` contains the placeholder `@harness-owner` — replace with a real GitHub handle.
- The prior environment had no network access to most domains and no GPU, which is why tickets 06–11
  stopped where they did. That constraint should not apply in Claude Code.
