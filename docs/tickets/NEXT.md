# NEXT — build slices for the clean repo

Dependency-ordered. Sliced from `docs/SPEC.md` after Map II closed M2-04 and
M2-07. **Only closed decisions are sliced here**, because slicing fog produces
sliced fog. M2-03 was open when this file was written and is now closed by N-05;
the header said otherwise for a while and that contradiction is fixed here.

Tickets `01`–`11` in this directory are the prior session's slices. `02`, `03`
(2 of 3), `04`, `05` are complete and now live in `src/expfactory/`. `06`–`11`
still need infrastructure that does not exist yet.

| ID | Slice | Depends on | Needs | Status |
|---|---|---|---|---|
| N-01 | Ledger ordering becomes an explicit, tested guarantee | — | — | **DONE** |
| N-02 | `Preregistration` record + `prereg_hash` / `exploratory` on `Candidate` | N-01 | — | **DONE** |
| N-03 | G-07 preregistration gate + fixtures, wired into the verifier | N-02 | — | **DONE** |
| N-04 | G-08 preregistration-churn gate + fixtures | N-03 | — | **DONE** |
| N-05 | Resolve M2-03 — the experiment queue | — | grilling session | **DONE** |
| N-08 | JobRegistry + ComputeSubstrate protocol | M2-03 | — | **DONE** |
| 07 | Runner: poll, claim, dispatch, reconcile | N-08 | see below | **PARTIAL** (1 box: secret store wiring) |
| N-06 | Ticket 01 provisioning | — | **owner's accounts** | open |
| N-07 | M2-01 timeout / handoff test | N-06 | **owner's machine** | open |

## Blocking defects from the 2026-07-28 review

Filed in Linear, not sliced here, because each is a defect in shipped code rather
than a slice of unbuilt spec. Full statement of each in
[`../DISPATCH-READINESS.md`](../DISPATCH-READINESS.md).

| ID | Defect | Priority |
|---|---|---|
| BRE-28 | Non-finite metrics promote — validate values at the `Candidate` door | **P0** |
| BRE-29 | `JobRegistry` accepts negative and `NaN` cost estimates | **P0** |
| BRE-30 | Submit-then-record crash window; no multi-writer guard | P1 |
| BRE-31 | G-10 proves a handle exists, not the artifact or ticket | P1 |
| BRE-32 | Detach unreachable through both real trackers; first-page-only reads | P2 |
| BRE-33 | Compute seam says "GPU" where it means "substrate" | low, **deferred on purpose** |

**BRE-28 is the one worth reading twice**, because it is a lesson about this
file's own optimism. `architecture-review-verifier.html` proposed making
`RunResult` the currency of the `Candidate` seam so that input is "validated at
the door", and that refactor shipped. It validated **shape** and never validated
**values**, so `float("inf")` walks straight through a door everyone had recorded
as closed. The door was built; the lock was never fitted, and the summary said
"done" either way.

That is the same failure this repo exists to prevent, committed against itself:
a control recorded as present because the work adjacent to it landed.

**Ticket 07 is PARTIAL, not core-done.** Two of its four acceptance boxes were
unmet and were being hidden by the summary. One is now closed and one is half:

- *"prepares an isolated workspace"* — **DONE.** `Runner(workspaces=...)` gives
  each ticket a directory prepared by the runner and handed to the agent, and
  `AgentSession.run` takes it as a second argument. Names are **refused rather
  than sanitized**: every sanitizer is lossy, and a lossy mapping lets two
  tickets share one directory. Filesystem isolation only — Symphony's caveat
  applies verbatim, it is the minimal primitive and not a security boundary.
- *"Tracker credentials live in the runner's secret store"* — **HALF.**
  `SecretStore` exists and satisfies §15.3's normative MUST: it declares its
  names so a launcher can scrub them from a child environment, and strips the
  whole declared set rather than only the secrets a given run uses.
  **It is not yet the source of tracker credentials**, because the runner does
  not construct trackers — they arrive already built, with a transport the
  caller wired. Closing that box means inverting who builds what, which is a
  real change and not a wiring detail.

**Reconcile is now done.** The runner takes an optional `jobs: JobLedger`, calls
`sweep()` at the top of every tick, and grounds each lost job's ticket to
needs-human with a comment and no retry. It also reads `breaker_reason()` and
halts *dispatch* while the breaker is open — previously only `submit` consulted
it, so a tripped breaker cost one full agent session per ticket to discover, and
cost nothing at all to discover on a lane that submits no jobs.

**The detach model is now done.** `AgentSession.run` may return `Submitted`
instead of a `Candidate`: the agent starts a compute job, its session ends, and
the ticket parks in `Running Unattended` (W-06, M2-03). `JobRegistry.collect_finished`
plus a `ResultCollector` turn the artifact back into a candidate, which reaches
the *same* `_adjudicate` the synchronous path uses.

Three things that had to be right:

- **Collect before sweep.** `sweep` resolves a job that finished after its
  deadline and returns it as not-lost, closing the record without naming the
  waiting ticket — so that ticket would sit in `Running Unattended` forever.
- **Unattended runs count against concurrency.** They hold a GPU, and their agent
  session has ended, so nothing else marks them busy.
- **Detaching with no collector is refused, not parked.** A state nothing can
  leave looks like work in flight, which is worse than never starting. BRE-32
  extended this to the registry as well: a collector alone turns a *finished*
  job into a verdict, but `sweep` is the only thing that notices one that
  stopped answering, so with no registry the ticket sits forever.
- **The parked state is reachable through both real trackers** (BRE-32). It was
  not, and a fake tracker made the unit tests pass while both production
  adapters rejected it. The runner now asks `Tracker.writable_states()` at
  construction, so a state an adapter cannot express is a wiring error caught
  before dispatch rather than a refusal raised after a GPU job started.

**N-01/02/03/04 landed.** G-07 and G-08 run inside `GateVerifier` when it is
constructed with `require_prereg=True`, backed by a `PreregStore` (which `Ledger`
satisfies). 29 unit fixtures plus 10 suite fixtures across both partitions.

Baseline, measured once before any tuning:

| Suite | Visible | Held-out |
|---|---|---|
| core gates | 5/5 | 3/3 |
| G-07 + G-08 preregistration | 7/7 | 3/3 |

One judgement call worth revisiting: `require_prereg` defaults to **False**. The
same gate set adjudicates one-off candidates with no hill-climb lineage — the
core adversarial fixtures among them — and requiring a preregistration there
would reject everything and destroy their diagnostic value. It is a workflow
switch, not a security toggle. **The hill-climb runner must set it True**, and
nothing yet enforces that it does; that enforcement belongs with the runner
(ticket 07) and should be a boundary test when the runner exists.

---

## N-01 — Make ledger ordering an explicit guarantee

**Why now.** G-07 rule 2 proves a preregistration preceded its run by comparing
ledger positions. That makes insertion order load-bearing. `Ledger.all()` returns
it today as an accident of reading a JSONL file top to bottom; nothing asserts it.

**Done when.** `Ledger` documents ordering as part of its contract; a test appends
interleaved rows across two `Ledger` instances over the same path and asserts
order survives; `position` is reachable for a given `exp_id`.

**Watch for.** Concurrent appends from two processes. Single-writer is assumed
today — if that assumption is wrong, G-07's proof is wrong too. State it
explicitly rather than discovering it later.

---

## N-02 — `Preregistration` record

Per `decisions/M2-04-RESOLVED-preregistration.md`.

**Fields.** `primary_metric`, `direction`, `minimum_effect`, `seeds`,
`decision_rule`, `secondary_metrics`, `guardrail_metrics`, `parent_id`,
`supersedes`.

**Done when.** The record is frozen and content-hashed; the hash is stable across
processes (no `id()`, no dict ordering dependence); it appends to the ledger as
its own row type; `Candidate` gains `prereg_hash: str | None` and
`exploratory: bool = False`.

**Watch for.** Hash stability is the whole mechanism — if the hash changes between
processes, every confirmatory run fails to match its own prereg. Test it by
hashing in a subprocess.

---

## N-03 — G-07 preregistration gate

**Rules, all blocking.**

1. A confirmatory candidate carries a `prereg_hash`.
2. That preregistration appears in the ledger at a **strictly earlier position**
   than this run (anti-HARKing; N-01 supplies the guarantee).
3. Reported primary metric **name** matches the declared one.
4. Observed effect meets `minimum_effect` in the declared `direction`.
5. Run seeds match the declared seed set exactly (anti seed-shopping).
6. No guardrail metric regressed.
7. `exploratory=True` ⇒ never promoted, unconditionally.

**Fixtures required before implementation** (W-09: every gate traces to a
fixture), split across both partitions:

- clean confirmatory → **promote**
- metric swap: primary flat, secondary up → **reject**
- post-hoc filing: prereg appended *after* the run → **reject** on rule 2
- seed shop: ran 20, reported best 5 → **reject** on rule 5
- guardrail regression: primary up, latency worse → **reject** on rule 6
- exploratory run with a great number → **reject** on rule 7

**Watch for.** The dominance gate was wrong on first implementation and passed
nothing; a fixture caught it. Write the fixtures first and expect G-07 to be
wrong the first time too.

---

## N-04 — G-08 preregistration-churn gate

Counts non-promoting preregistrations sharing a lineage. Past a threshold, block
and escalate: that pattern is S-hacking whatever each individual prereg looked
like.

**Watch for.** **Do not guess the threshold.** Calibrate against fixtures on the
visible partition, then measure once on held-out. A guessed threshold is a
speculative gate and violates the bloat rule.

---

## N-05 — Resolve M2-03: the experiment queue

**Grilling, not a build.** M2-07 established Open SWE supplies no experiment
queue, no durable compute-job store and no cross-job circuit breaker, which makes
this the load-bearing open ticket.

Carry in the Map II note: Metaflow is ML-experiment-first with built-in artifact
versioning; Prefect is general-purpose and expects you to bring tracking. **Since
the ledger already *is* the tracking layer, the usual recommendation may invert.**

Must also answer the two unspecified items: failure semantics across the split
(if the queue loses a job, who notices?) and where ticket state lives during a
six-hour run.

---

## N-06 — Provisioning  ·  needs the owner's accounts

Artifacts are ready in `provision/`. **Apply by hand — do not hand these to an
agent.** Replace `@harness-owner` in `CODEOWNERS` with a real handle; create
labels from `labels.json`; branch protection on `main` requiring CODEOWNERS review
and green CI; one-way Linear→Issues sync; and the rule that **only a human may
apply `agent-ready`**.

---

## N-07 — M2-01 timeout / handoff test  ·  needs the owner's machine

**Downgraded from blocking to confirmatory by M2-07.** The objection to running a
long job inside an agent session is structural, not numerical, so no timeout value
changes the architecture. Still cheap and still worth running, because it fixes
the deterministic lane's practical ceiling. Run against Open SWE too.
