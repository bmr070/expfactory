---
labels: [wayfinder:map]
tracker: local-markdown
---

# Factory Proving Map

## Destination

A software factory designed for multiple workload lanes and **demonstrably working** — proven by
driving one real workload end to end, from ticket through agent execution to a merged, verified
result. The map is done when the lanes, the outer loop, the gate set, and the acceptance criteria
are all locked, and nothing remains to decide before someone builds it.

This map produces **decisions, not the build**. The build is the next effort.

## Notes

- **Domain.** Agentic software factory / harness engineering. Two verifier modes exist and they
  are not interchangeable: a *deterministic lane* where CI is ground truth (web, iOS, infra,
  sim harness code) and an *empirical lane* where no CI exists and results must be defended
  against seed noise, leakage, and holdout burn (ML research, hill-climbing).
- **Prior art already surveyed in-thread** — do not re-research from scratch: OpenAI Symphony
  `SPEC.md` (tracker-as-control-plane, per-issue workspace, WORKFLOW.md), Factory droid
  architecture (7 components, risk taxonomy, config precedence), GitHub Agentic Workflows
  (compiled `.lock.yml`, sandboxing, safe outputs), Tessl SDD, Agent Skills `SKILL.md` spec.
- **Existing asset.** `expfactory/` — working prototype of the empirical-lane verifier: append-only
  ledger plus six anti-fooling gates (leakage, seed variance, too-good, reproducibility, holdout
  budget, cost). Known flaw: its own demo scenarios are miscalibrated and it has no eval of itself.
- **Skills each session should consult:** `/grilling`, `/domain-modeling`, `/research`,
  `/prototype`.
- **Standing preference.** The workload is the acceptance test, not a demo. A workload the factory
  handles comfortably has proven nothing. Prefer the lane where the factory has to earn its
  existence over the lane where gh-aw plus CI already solves the problem.
- **Standing preference.** Throughput ceiling is human review bandwidth. Any design that raises
  agent concurrency without raising review capacity is rejected by default.

## Decisions so far

<!-- one line per closed ticket: gist plus link -->

- [W-04 — What does gh-aw actually enforce, and does it fit non-GitHub workloads?](tickets/) —
  five default-on security layers incl. read-only token, zero secrets, kernel-level egress firewall,
  gated safe outputs, threat-detection scan. Adopt for the deterministic lane. Disqualified for the
  empirical lane not by its 360-min cap but by its output model: safe outputs are GitHub objects,
  and an adjudicated experiment result is not one.
- [W-05 — What is the minimum viable Symphony port, and where does it break?](tickets/) —
  don't port first. OpenAI's multi-language ports were ambiguity-hunting exercises, not production
  code; a real port is weeks. Run the reference on one high-volume ticket type, measure time-to-PR
  and reviewer hours, port later. Spec breaks for long jobs on three counts: in-memory scheduler
  state with no restart recovery, per-issue backoff with no circuit breaker, and a 5-min stall
  timeout that kills compute-bound runs.
- [W-06 — Where do long-running and GPU experiments actually execute?](tickets/) —
  two substrates, not one. Agent session (untrusted, model-bound, minutes) and experiment run
  (trusted, compute-bound, hours, GPU) have opposite requirements on every axis. GPU sandboxes exist
  only on Modal / Northflank / Beam. Agent submits a job and receives an artifact; it never holds
  GPU credentials.
- [W-01 — Which workload proves the factory?](tickets/) —
  multimodal drone-vs-bird detection, empirical lane, as a **proving vehicle only**. Chosen over web
  (deterministic, already solved by gh-aw+CI) and swarm sim2real (better second workload — reuses
  this lane and tests generalisation). Reuses the existing `expfactory/` prototype. Acceptance bar
  is "stressed the factory, gates behaved correctly" — beating a benchmark is explicitly not
  required, and gates correctly rejecting every proposed gain is a passing run.
- [W-02 — Which verifier lanes must v1 support?](tickets/) —
  empirical lane only has a live workload in v1; the verifier is a **plugin boundary** returning
  (verdict, artifact-bundle) so the dispatcher stays lane-agnostic. A one-hour CI-shelling adapter
  fills the deterministic slot to prove the interface admits two implementations, even though no v1
  workload drives it.
- [W-03 — What does "put through its paces" mean concretely?](tickets/) —
  acceptance = a labeled adversarial suite (shared with W-09) the factory classifies correctly, plus
  bounded-review and cost-ceiling invariants. Load-bearing negative criterion: must refuse to promote
  a single-lucky-seed result; rejecting every proposed gain is a passing run. Held-out fixtures
  prevent overfitting the gates.
- [W-08 — Buy or build the outer loop?](tickets/) —
  **hybrid, split by lane.** gh-aw drives the deterministic lane (its safe-outputs + 5 security
  layers are hardening already done). A minimal custom runner — Symphony's *loop* reimplemented, not
  its code — drives the empirical lane, adding the three things Symphony lacks for long jobs: durable
  restart state, a global circuit breaker, and a compute-tuned stall timeout. Shared tracker above
  both. LangGraph/CrewAI/AutoGen rejected as a category error.
- [W-07 — Provision the issue tracker](tickets/) —
  **[AMENDED — see W-07-AMENDMENT: Linear is the work queue and there is no sync.]**
  Linear = human board, GitHub Issues = machine control plane, one-way Linear→Issues sync (runners
  read GitHub only; avoids two-way races). Labels + states + CODEOWNERS trust lanes defined up front;
  only human-applied `agent-ready` is dispatch-eligible (untrusted-tracker defense).
- [W-10 — What is the per-unit-of-work loop?](tickets/) —
  six-stage chain: setup → wayfinder(once) → to-tickets → **verify-lane** (one interface, impls 3a
  implement/TDD and 3b experiment→gates→ledger) → review(fresh context, parallel Standards∥Spec) →
  architecture → **ratchet**. Stage 6 feeds back into stage 3 as constraints, never into wayfinder.
  None of 1–5 is the dispatcher.
- [W-09 — What is the gate set, and how is the gate harness evaluated?](tickets/) —
  the six `expfactory/` gates are v1, plus three hardenings: a diff-level test-tamper gate (closes
  stress scenario #1), recalibrated too-good gate, durable holdout budget. The harness is evaluated
  against W-03's labeled suite with held-out fixtures — held to its own holdout discipline. New gates
  added only when a fixture proves the set misses something.
- [W-11 — How do review findings become permanent gates?](tickets/) —
  findings land as deterministic gates at the cheapest sufficient point (lint < hook < CI/gate <
  boundary test < CLAUDE.md prose as last resort). Weekly 30-min ratchet promotes the top intervention
  reason-codes; harness owner decides; promote on recurrence ≥2 to avoid overfitting to noise. Gates
  feed back into stage 3, never stage 1. Every gate traces to a fixture (bloat control).
- [W-12 — What is the cost model and where are caps enforced?](tickets/) —
  two hard caps, runner-enforced: agent inference (per-day AIC, mirrors gh-aw's native guardrail) and
  GPU compute (per-experiment + per-day aggregate — the surface with no native guard). Breach =
  fail-closed via the W-08 circuit breaker, ticket → needs-human, no auto-retry on cost. Per-experiment
  cost recorded to the ledger (W-03 acceptance invariant). Caps set before first run.

## Not yet specified

Four of the original patches were resolved in passing while charting and are recorded under
Decisions so far; three remain, and all three are implementation-level — resolved while building,
not while charting. The map's *decisions* are closed.

- **Trust lanes / risk taxonomy** — direction set by W-07 (CODEOWNERS on migrations/auth/billing from
  day one) and W-08 (Factory's read/low/medium/high/unsafe as the template). Exact per-tool
  boundaries are drawn against the real repo during build.
- **CLAUDE.md / skills / hooks contents** — the *mechanism* is fixed by W-10 (where each lands) and
  W-11 (findings become hooks/gates via the weekly ratchet). The *contents* are populated by the
  ratchet as real failures accumulate — by design, they cannot be written up front.
- **Egress policy vs dataset downloads** — the one genuinely unresolved design tension. Default-deny
  outbound (W-04, W-06) versus pulling weights/datasets from HuggingFace. Resolve at build time with
  an allowlist of specific mirror domains + checksum pinning; flagged as the residual security risk.

## Post-map decisions (evidence-driven, after charting)

- **W-08 SUPERSEDED TWICE — orchestrator choice is now open, in Map II.** First answer: reimplement
  Symphony's loop. Second: fork Baton. **Both withdrawn.** The Python-seam argument failed scrutiny —
  a subprocess boundary costs almost nothing. Baton is eliminated as a foundation, retained as a
  reading exercise. Live candidates are OpenSymphony, Kata, and Open SWE (M2-02, M2-07). Kata's SSH
  worker pools and OpenSymphony's memory buckets remain worth porting as *designs*.
  **Consequence:** the VerdictBundle serialization constraint is live again — the seam is likely a
  subprocess/artifact boundary, not an in-process Python call.

- **Verification is layered L0/L1/L2, with a hard ordering constraint.** L0 deterministic gates run
  FIRST and block. L1 fresh-context subagent review runs only on L0-passing candidates. L2 is human.
  Rationale: an LLM reviewer cannot detect leakage (set arithmetic on IDs), cannot enforce a budget
  across restarts (durable state), and cannot ratchet (every review is a fresh probabilistic
  judgement at full token cost). Worse, if L1 ran first it would *launder* a fake result by attaching
  a persuasive endorsement. **A reviewer may never override a blocking gate.**

- **Skills are instructions; gates are walls.** SKILL.md tells the agent how to work and may be
  skipped under context pressure. A gate returning blocking=True is not skippable. Both are needed;
  they are not substitutes.

- **MLflow for observability, never for adjudication.** Tracing fills a real hole (34 tests, zero
  runtime visibility). The ledger keeps the verdict. A green dashboard line is never a promotion
  signal.

- **LiteLLM as model router.** Cheap; OpenSymphony already assumes an OpenAI-compatible/LiteLLM
  endpoint. Enables cheap-model hill-climb iterations + expensive-model planning.

- **Model tiering yes, vendor-shuffling no.** Factory's spec mode (expensive plans, fast implements)
  is evidenced. "GPT for review, Gemini for security" is not — what makes review work is *context
  isolation*, not vendor diversity.

## Explicitly rejected (proposed repeatedly; declined on evidence)

- **Persona/role agent org charts** (Product Manager / Research Scientist / Architect / QA / Data
  agents). Proposed by three separate stack recommendations. Macaron research confirms this is the
  experimental-not-production paradigm. Keep only the three roles that earn *isolation*: planner
  (expensive, no write), reviewer (fresh context, read-only), triage (cheap, bounces bad tickets).
- **An LLM "Evaluation Agent" as the research verifier.** The single most dangerous proposal seen.
  Would confidently endorse the seed-lottery fixture the gate caught, then hand it to a paper-writing
  agent. Produces publication-shaped artifacts from noise.
- **LangGraph / Mastra / AutoGen / CrewAI as the outer loop.** Category error — the coding agent
  already is the agent loop.
- **Five-database knowledge stack** (Qdrant + S3/MinIO + Postgres + Neo4j + tracking). OpenSymphony
  ships equivalent capability on one embedded DuckDB index plus markdown capsules.
- **Phase-4 training factory** (TRL, DeepSpeed, Ray, vLLM fine-tuning). Enormous scope, irrelevant to
  proving the factory on detection.
- **Deferring cost caps and security to a later phase.** W-12 puts both at day one; the Uber and
  Microsoft precedents were each "retrofitted after a shock."

## Out of scope

<!-- ruled beyond this destination; never graduates -->

- **Building or exercising the web and iOS delivery lanes.** Design must leave seams for them;
  this effort does not build them. gh-aw plus CI is a known-good answer there, so exercising it
  proves little.
- **Drone swarm sim2real as a build target.** Remains eligible as the *proving workload*
  (see W-01); ruled out as a separate lane to construct.
- **Shipping a competitive drone detection model.** If detection is chosen as the proving workload,
  the bar is "stressed the factory and produced a defensible result", not "beat SOTA".
