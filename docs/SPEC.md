# SPEC — expfactory

Authoritative specification. Consolidates Map I (12/12 closed), Map II
(M2-04 and M2-07 closed), and the post-map decisions.

Supersedes `docs/reference/factory-spec.html` (rev 2), retained for its schematic.

Status key: **BUILT** · **DESIGNED** (decided, not built) · **OPEN** (undecided).

---

## 1. Thesis

CI is ground truth for deterministic work. For empirical work there is no ground
truth in the loop — an agent proposes a change, a number moves, and nothing knows
whether the number is real. Every existing agentic factory verifies code against
tests, so none of them has a ledger or anti-fooling gates.

> **Adopt infrastructure. Build verification.**

Everything below follows from that split: infrastructure is bought wherever a
credible option exists, and effort concentrates on the verification layer that
nobody else supplies.

### Acceptance

The proving workload is multimodal drone-vs-bird detection, used as a **vehicle,
not a ship target**. Acceptance is *"the gates behaved correctly"*, never *"the
model beat a benchmark"*. **A run in which every proposed improvement is correctly
rejected is a passing run** (W-03's load-bearing negative criterion).

Shipping a competitive detection model is explicitly out of scope.

---

## 2. Two lanes

Not interchangeable; they differ in what can serve as ground truth.

| | Deterministic lane | Empirical lane |
|---|---|---|
| Ground truth | CI exit code | none — must be constructed |
| Workloads | web, iOS, infra, harness code | ML research, hill-climbing |
| Outer loop | gh-aw (5 default-on security layers) | custom runner |
| Verifier | `ExitCodeVerifier` | `GateVerifier` |
| v1 live workload | none | drone detection |

Both satisfy one interface, so the dispatcher stays lane-agnostic (W-02). The CI
adapter exists to prove the seam admits two implementations even though no v1
workload drives it.

gh-aw is disqualified for the empirical lane not by its 360-minute cap but by its
**output model**: safe outputs are GitHub objects, and an adjudicated experiment
result is not one (W-04).

---

## 3. Verification layers — L0 / L1 / L2   **BUILT (L0)**

```
L0  deterministic gates   FIRST. Blocking. Cheap. Ratchets.
L1  fresh-context review  Only on L0-passing candidates. Read-only.
L2  human                 Judges the result, not the arithmetic.
```

**The ordering is a hard constraint, not a preference.** An LLM reviewer cannot
detect leakage (set arithmetic over IDs), cannot enforce a budget across restarts
(durable state), and cannot ratchet (every review is a fresh probabilistic
judgement at full token cost). Worse: if L1 ran first, it would **launder** a fake
result by attaching a persuasive endorsement to it — strictly worse than no review.

> **A reviewer may never override a blocking gate.**

**Skills are instructions; gates are walls.** A `SKILL.md` tells an agent how to
work and may be skipped under context pressure. A gate returning `blocking=True`
is not skippable. Both are needed; they are not substitutes.

---

## 4. The verifier boundary   **BUILT**

```python
class Verifier(Protocol):
    def run(self, candidate: Candidate) -> VerdictBundle: ...
```

One contract. Whether a verdict came from the gate harness or a CI exit code is
invisible above this line.

### Invariants

1. **`promoted` is derived, never settable.** `VerdictBundle` is frozen and both
   implementations construct it through `from_experiment` / `from_exit_code`. A
   settable `promoted` makes the whole layer theatre.
2. **`Candidate` is the validating boundary.** Run records normalise to typed
   `RunResult` at construction; a malformed record fails there, naming its index,
   rather than as an `AttributeError` inside a gate.
3. **The seam is assumed to be a process boundary.** The runner may not be Python
   (W-08 superseded twice). `VerdictBundle` round-trips through strict JSON —
   including `mean_metric` NaN encoded as `null`, since bare `NaN` is what Python
   emits and is not valid JSON.
4. **The ledger is append-only** and each row reconstructs the verdict alone,
   with no agent narrative required.

---

## 5. The gate set   **BUILT except G-08**

| Gate | Catches | Blocking | Status |
|---|---|---|---|
| `no_leakage` | train/eval index overlap | yes | BUILT |
| `reproducible` | same seed → different number | yes | BUILT |
| `seed_variance` | gain inside the noise band | yes | BUILT |
| `too_good_to_be_true` | implausible jump — escalates | **no** | BUILT |
| `holdout_budget` | over-querying the lockbox | yes | BUILT |
| `cost` | per-experiment spend | yes | BUILT |
| `no_single_seed_dominance` | one lucky seed carries the mean; needs no baseline | yes | BUILT |
| `no_test_tampering` | diff weakens verification itself | yes | BUILT |
| `G-07` preregistration | HARKing / metric-shopping | yes | **BUILT** |
| `G-08` prereg churn | serial re-filing until one lands | yes | **DESIGNED** |

**Bloat control (W-09/W-11): every gate traces to a fixture.** A gate may be added
only when a fixture proves the set misses something. No speculative gates.

### Self-evaluation and the holdout discipline

The harness is evaluated against a labeled adversarial suite with **visible** and
**held-out** partitions — the factory held to the discipline it enforces on
experiments (invariant 5).

Enforced operationally, not just documented: **CI runs the visible partition
only.** Running held-out on every commit would make each red build a tuning signal
and burn the partition within a week. Held-out is a budgeted, human-initiated
measurement behind `--heldout`.

Baseline, measured once before any gate tuning:

| Suite | Visible | Held-out |
|---|---|---|
| core gates | 5/5 | 3/3 |
| G-07 preregistration | 6/6 | 3/3 |

---

## 6. Preregistration (G-07)   **BUILT** — see `decisions/M2-04-RESOLVED-preregistration.md`

Closes metric-shopping: primary metric flat, latency improved, latency reported as
the win. Every built gate is silent on it, because nothing about the result is
fake — only the **claim** is.

**Preregister the decision rule, not the hypothesis.** Two run classes:

| | Exploratory | Confirmatory |
|---|---|---|
| Preregistration | not required | required, hashed, filed first |
| Seeds | any | must match the declared set |
| Can promote? | **structurally never** | yes, if the rule is met |

**The mechanism is an asymmetry: a metric may promote *or* block, never both.**
Guardrails (latency) block only. Secondaries are recorded and never sufficient.

Preregistration does not make metric-shopping impossible — it makes it
**countable**. G-08 counts it. Ordering in the append-only ledger is the
anti-HARKing proof: the prereg row must precede the run row.

**Wiring.** `GateVerifier(require_prereg=True, prereg_store=ledger)` turns G-07
on; `Ledger` holds verdicts and preregistrations in one ordered log so positions
are comparable. `require_prereg` defaults to False because the same gate set also
adjudicates candidates with no hill-climb lineage — **the hill-climb runner must
set it True**, and no boundary test enforces that yet (it belongs with ticket 07).

**Assumption made explicit:** the ledger is single-writer. Two processes appending
concurrently can interleave partial lines, which would break both the ordering
guarantee and the rows. If that stops holding, G-07's proof needs hash chaining
instead.

---

## 7. Execution — two substrates   **DESIGNED**

Agent session and experiment run have opposite requirements on every axis:

| | Agent session | Experiment run |
|---|---|---|
| Trust | untrusted | trusted |
| Bound by | model latency | compute |
| Duration | minutes | hours |
| Needs GPU | no | yes |

**The agent submits a job and receives an artifact. It never holds GPU
credentials** (invariant 6).

M2-07 confirmed this split on *design* grounds, not just empirical ones: Open SWE's
`execute` accepts an arbitrary timeout, so a six-hour call is expressible — but the
shape is a blocking shell call inside a live agent session, holding an LLM-metered
session open to do nothing but wait. Configurable ≠ correct.

**Credential pattern to adopt:** the dummy-token proxy (`GH_TOKEN=dummy` inside the
sandbox, proxy injects the real token). This is invariant 6 implemented cleanly.

---

## 8. Orchestration   **PARTLY DECIDED**

- **L3 agent runtime + L5 dispatch: Open SWE** (17 Mar 2026, Python, Deep Agents +
  LangGraph). Owns webhook routing, label triggers, sandbox lifecycle, the agent
  loop, PR creation, reviewer-as-separate-graph. OpenSymphony and Kata drop out as
  orchestrator candidates.
- **Not supplied by Open SWE, therefore ours:** the experiment queue, a durable
  compute-job store, a cross-job circuit breaker, GPU cost caps.
- **LangGraph boundary:** "LangGraph as the outer loop orchestrating coding agents"
  stays rejected as a category error. "A coding agent *implemented in* LangGraph"
  is a different claim and is adopted. Do not collapse these.
- **OPEN — M2-03:** the experiment queue. Now the load-bearing open ticket.
- **OPEN — M2-02:** orchestrator final pick, substantially narrowed.

---

## 9. Observability   **DESIGNED**

Three layers, no contention:

| Layer | Tool | May it promote? |
|---|---|---|
| Agent-session tracing | LangSmith (or none) | **never** |
| ML experiment tracking | MLflow | **never** |
| Adjudication | **the ledger** | **only this** |

MLflow fills a real hole — 43 tests and zero runtime visibility — but a green
dashboard line is never a promotion signal. `SANDBOX_TYPE` must be set explicitly
rather than inherited, so the LangSmith dependency is a decision on the record.

**LiteLLM** as model router: enables cheap-model hill-climb iterations with
expensive-model planning. Model tiering yes; vendor-shuffling no — what makes
review work is *context isolation*, not vendor diversity.

---

## 10. Trust and cost   **DESIGNED, day one**

- **Tracker is untrusted input.** Anyone who can file a ticket can prompt-inject
  the factory. Only a **human-applied `agent-ready` label** is dispatch-eligible;
  no ticket self-promotes.
- **Linear = human board; GitHub Issues = machine control plane**, one-way
  Linear→Issues sync. Runners read GitHub only, avoiding two-way races.
- **Two hard caps, runner-enforced:** agent inference per-day, and GPU compute
  per-experiment plus per-day aggregate — the surface with no native guard.
- **Breach = fail-closed** via the circuit breaker; ticket → `needs-human`; **no
  auto-retry on cost**. Caps set before the first run.
- Credentials live in the runner's secret store, never an agent workspace.

---

## 11. The ratchet   **DESIGNED**

Review findings become deterministic gates at the **cheapest sufficient point**:

```
lint  <  hook  <  CI check  <  boundary test  <  gate  <  AGENTS.md prose
```

Prose is the last resort. A weekly 30-minute session promotes the top intervention
reason-codes; the harness owner decides; promote on **recurrence ≥ 2** to avoid
overfitting to noise. Gates feed back into the implement stage, never into
charting.

This already paid out during the scaffold: the first lint pass found dead
quadratic code in `gate_reproducible` that no review comment had caught.

---

## 12. Open questions

| Ref | Question | Notes |
|---|---|---|
| M2-03 | Experiment queue: build, adopt, or does it not exist? | Load-bearing; Open SWE supplies none |
| M2-02 | Orchestrator final pick | Narrowed by M2-07 |
| M2-05 | Build G-07 + fixtures | Unblocked by M2-04 |
| M2-06 | Where MLflow sits | De-risked to a placement question |
| M2-01 | Timeout / handoff test | **Downgraded to confirmatory**; needs the owner's machine |
| — | Failure semantics across the split | If the queue loses a job, who notices? |
| — | Ticket state during a six-hour run | Needs a "running, unattended" state |
| — | Does L1 review the experiment or only the verdict? | Reviewing code ≠ reviewing a result |
| — | Egress policy vs dataset downloads | Default-deny vs pulling weights; resolve with a mirror allowlist + checksum pinning. Residual security risk. |
