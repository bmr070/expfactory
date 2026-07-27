# CONTEXT

The project glossary. Terms only — no implementation, no decisions, no status.
Decisions live in `docs/decisions/`; the specification lives in `docs/SPEC.md`.

Written because several terms were doing double duty. Where two things shared a
name, the entry says so explicitly rather than picking a winner silently.

---

## The two holdouts

**This is the most confusable pair in the project.** Both are reference sets,
both carry a budget, both exist so that repeated looks cannot quietly corrupt
them. They are not the same thing and they protect different parties.

### Experiment holdout

The data a *model* is scored against. Guarded by `HoldoutBudget` and
`gate_holdout_budget`. Spending it is an experiment querying the lockbox.

Protects: **the factory, from the agent.**

### Fixture partition (held-out)

The half of the adversarial suite that is *not* used while tuning gates,
consulted only to measure whether tuning generalised (invariant 5).

Protects: **the factory, from its own authors.**

> When either word appears alone and the sentence is about a *model's* score, it
> is the experiment holdout. When it is about whether a *gate* is correct, it is
> the fixture partition. Prefer the qualified form in new writing.

---

## Trust boundary

### Agent session

The untrusted party. Writes hypotheses, training code and configuration. Runs in
a sandbox, holds no credentials, and returns **evidence** — never a verdict, and
never a metric it computed itself.

### Substrate

Where an experiment actually executes. Detached from the agent session, so a job
outlives the session that submitted it.

### Verifier

Adjudicates a `Candidate` against the gate set and returns a `VerdictBundle`.
Decides **whether a result is real**.

### Scorer

Computes **the number itself**, from predictions, in trusted code the agent
cannot reach.

> `Verifier` and `Scorer` are deliberately different words. A verifier asks "is
> this result trustworthy"; a scorer asks "what is this result". Calling the
> second an "evaluator" was rejected — too close to `Verifier` in both meaning
> and spelling for a codebase where the distinction is the whole point.

---

## Evidence and verdicts

### Candidate

Evidence submitted for adjudication: runs, metrics, id hashes, the diff, the
preregistration cited. Authored partly by the agent and partly by trusted
components; which parts come from where is the subject of the trust boundary.

### VerdictBundle

The adjudicated outcome. Frozen. `promoted` is derived from the gates and is
never settable by any caller.

### Attestation

What the substrate vouches for about an execution: the job handle, exit code,
wall time, and a digest of the artifact. Establishes that a run **happened**. It
cannot establish that the number the run reported is **correct**.

### Prediction

A model's output for one sample, submitted **without** the corresponding label.
The unit the scorer consumes.

### Label

Ground truth for one sample. Never leaves trusted storage, and is never visible
to an agent session under any circumstance.

---

## Hill-climb

### Lineage

A chain of experiments linked by `parent_id`. The unit G-08 counts churn over.

### Exploratory run

Free, unlimited, and **structurally unpromotable**. Establishes a baseline.

### Confirmatory run

Cites a preregistration filed beforehand, uses a fixed seed set, and is
promotable.

### Preregistration

A decision rule fixed and content-hashed *before* a run. Preregisters the rule,
not the hypothesis.

### Guardrail

A metric that may only ever **block** a promotion, never earn one. The blocking
half of the promote-or-block asymmetry.

### Primary metric

The single metric that may earn a promotion. A metric may promote **or** block,
never both.

### Secondary metric

Recorded, never sufficient, never adjudicated. Deliberately inert.

---

## Verification

### Gate

A deterministic function of recorded evidence returning a pass or fail. Never an
LLM judgement. Blocking gates decide promotion; non-blocking ones annotate.

### Substrate (verification)

The code that decides whether a result is real — the gates, the ledger, the
verifier, and everything that adjudicates. Distinct from *compute* substrate
above; disambiguate in new writing.

### Ledger

The append-only record of what happened. Holds verdicts and preregistrations.
The one authoritative record; nothing else adjudicates.

### Registry

The record of what is **outstanding** — jobs submitted and not yet resolved.
Deliberately holds no result and no verdict.
