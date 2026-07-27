---
labels: [wayfinder:map]
tracker: local-markdown
parent-effort: MAP.md (exhausted, 12/12)
---

# Factory Proving Map II — the empirical execution layer

## Destination

The long-running experiment path is decided and buildable: how a hours-long GPU experiment is
dispatched, tracked, adjudicated and recorded without bending a coding-agent orchestrator into a job
queue — and how a hill-climb preregisters its claims so an autonomous agent cannot metric-shop its
way to a promotion. Done when nothing is left to decide before someone builds it.

## Notes

- **Why a second map.** Map I closed with 12/12 and a working verifier core (34 tests). Three things
  then surfaced that Map I could not have seen: a duration mismatch every ticket-driven orchestrator
  shares, the loop-split that follows from it, and a documented fooling mode (metric-shopping) that
  passes every gate currently built.
- **The duration mismatch.** Symphony, OpenSymphony, Kata and Baton all assume one agent session is
  roughly one unit of work. The empirical lane breaks that: the agent *writes* an experiment in
  minutes, then the experiment *runs* for hours. Waiting trips the stall timeout; exiting leaves the
  ticket without a result.
- **Standing decision (Map I, unchanged).** Verification is layered L0/L1/L2; L0 deterministic gates
  run first and block; a reviewer may never override a blocking gate.
- **Evidence carried in.** Autonomous AI research systems are documented to p-hack (arXiv 2606.27687);
  preregistration is the proposed mitigation and the paper recommends integrating it into autonomous
  frameworks. Named failure modes: HARKing (hypothesising after results known) and S-hacking
  (trialling many metrics, reporting the favourable one).
- **Known tension.** Preregistration assumes a confirmatory study. A hill-climb is exploratory by
  construction. Literature suggests preregistering the *selection methodology* rather than the
  specific hypothesis — but that is a sketch, not a design.
- **Category note.** Metaflow is ML-experiment-first with built-in artifact versioning; Prefect is a
  general-purpose Python orchestrator expecting you to bring your own tracking. Since the ledger
  already IS the tracking layer, the usual recommendation may invert.
- **Baton is eliminated as a foundation.** Recorded explicitly because it was recommended, withdrawn,
  then leaked back into artifacts. The Python-seam argument did not survive scrutiny. Retained only as
  a short reading exercise.
- **The LangGraph rejection needs a boundary.** "LangGraph as the outer loop orchestrating coding
  agents" stays rejected. "A coding agent implemented in LangGraph" (Open SWE) is a different claim and
  is a live candidate. Do not dismiss the second by association with the first.
- **Stickiness warning.** Orchestrator choice is among the stickiest infrastructure decisions; once
  20-30 pipelines sit on one, migration costs quarters. Decide deliberately, not by default.

## Decisions so far

<!-- one line per closed ticket -->

- [M2-08 — Where does the runner live, and is it polled or pushed?](decisions/M2-08-RESOLVED-runner-location.md) —
  **a daemon on the owner's machine, polling, for as long as compute is local.** The option table was
  answering the wrong question: `LocalGpuSubstrate` did not exist when this was raised, and a
  cloud-hosted runner cannot reach a GPU under the desk without exposing it. So runner location is
  *downstream of compute location*, recorded as an explicit coupling so that moving to Modal reopens
  the hosted options rather than leaving a daemon in place from habit. Webhooks deferred for the same
  reason a hosted runner was: a receiver on a home network needs an inbound path, a poll needs only
  outbound. **BRE-21 turns out to be already mitigated** — `substrate_guard` asks what changed, never
  who, so Open SWE authoring PRs as the triggering human degrades CODEOWNERS and does not touch the
  wall. That inverts BRE-18: the *free* Linear identity is the load-bearing one (it is what
  `label_actor` reads), and the GitHub App is a later nicety rather than a blocker.

- [M2-07 — Does Open SWE subsume the orchestrator, the runtime, or both?](decisions/M2-07-RESOLVED-open-swe.md) —
  **adopt at L3 + the dispatch half of L5.** Current release is 17 Mar 2026, Python, on Deep Agents +
  LangGraph (the Aug-2025 TS version is superseded). It does *not* supply an experiment queue, a
  durable compute-job store, a cross-job circuit breaker, or GPU cost caps. `execute` defaults to a
  300 s timeout and accepts an arbitrary one — so a long call is *expressible*, but the shape is a
  blocking shell call inside a live agent session, which is exactly the duration mismatch. **W-06's
  two-substrate split survives on design grounds.** OpenSymphony and Kata drop out as orchestrator
  candidates. LangSmith is a swappable default (`SANDBOX_TYPE`), so no collision with MLflow — set it
  explicitly. Steal the dummy-token proxy pattern regardless.
- [M2-03 — Experiment queue: build, adopt, or does it not exist?](decisions/M2-03-RESOLVED-experiment-queue.md) —
  **no general-purpose orchestrator.** Adopt the compute substrate's own job primitive (Modal
  `spawn` → durable handle → poll) and build a thin `JobRegistry` holding outstanding submissions.
  Metaflow declined on *safety*, not fit: its versioned-artifact store is what the ledger already is,
  and installing a second store that also looks authoritative creates ambiguity about which record is
  the truth — the exact thing this project removes. Prefect declined as redundant (Modal already
  supplies the durable state) and retained as the named fallback behind the same interface if
  concurrency outgrows a file. Celery/RQ declined on operational weight; "nothing" was already dead
  via M2-07. The blocking edge to M2-01 was stale for the same reason. Revisit at >~10 concurrent
  experiments or a second lane needing cross-job scheduling.
- [M2-04 — How do you preregister an inherently exploratory hill-climb?](decisions/M2-04-RESOLVED-preregistration.md) —
  **preregister the decision rule, not the hypothesis**, and split runs into exploratory (free,
  unlimited, *structurally* unpromotable) versus confirmatory (prereg filed and hashed first, fixed
  seed set, promotable). The mechanism is an asymmetry: a metric may promote **or** block, never
  both — so "primary flat but latency improved" cannot promote under any reading. Preregistration
  does not make metric-shopping impossible, it makes it *countable*; **G-08** counts it. Secondary
  metrics are recorded and never sufficient. Unblocks M2-05.

## Not yet specified

- **Whether L1 review sees the experiment or only the verdict.** Reviewing a training run's *code* is
  a different act from reviewing its *result*.

<!-- resolved by M2-03: failure semantics (the JobRegistry notices, and only it can —
     an unresolved entry past its deadline trips the breaker, goes needs-human, and is
     never auto-retried) and ticket state during a long run (a `running-unattended`
     state entered on detach and exited only by the registry). -->

<!-- resolved: the observability collision. M2-07 established LangSmith is a swappable
     default, not a forced dependency; tracing, ML tracking and adjudication sit at three
     different layers and the ledger alone promotes. M2-06 no longer has to pick a winner. -->

## Frontier — takeable now

- **N-08** — build the `JobRegistry` + `ComputeSubstrate` protocol (unblocked by M2-03). Now the
  load-bearing *build*.
- ~~M2-05~~ — G-07 and G-08 are built, wired and fixtured. Done.
- **M2-01** — the timeout test. **Downgraded from blocking to confirmatory** by M2-07: the objection
  is not "will a long call be killed" but "should an agent session be the thing that waits," and that
  answer is no at any timeout value. Still cheap, still worth running. Needs the user's machine.
- **M2-02** — orchestrator final pick. Substantially narrowed by M2-07.
- **M2-06** — where MLflow sits. De-risked; now a placement question, not a contention one.

## Out of scope

- Rebuilding anything Map I closed. The verifier core, gate set, ledger and adversarial suite are
  settled and built.
- Model training as a product (TRL, DeepSpeed, fine-tuning pipelines). Out per Map I.
- Multi-user/team concerns. Solo factory until proven.
