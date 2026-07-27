---
id: T-01
parent: wayfinder:map2
labels: [wayfinder:decision]
mode: HITL
status: RESOLVED
raised: 2026-07-27
resolved: 2026-07-27
---

# T-01 — Who computes the number

## The problem

G-10 established that a run *happened*. It cannot establish that the number the
run reported is **correct**, because the agent writes both the training code and
the code that computes the metric. A real job, a real registry record, a matching
artifact digest — and a metric computed by agent-authored code.

> Attestation proves the execution. It cannot prove the semantics.

## Decision

**Split who trains from who scores. The agent submits predictions; trusted code
holds the labels and computes the metric.**

The agent's job receives holdout *inputs* and emits a prediction per sample id.
It never receives labels, and no agent-authored code runs in the scoring process.

### Why predictions, not a model artifact

The obvious alternative is for the agent to submit a trained model that the
scorer loads and runs. It was rejected on security grounds, and the reasoning
generalises:

**Loading an agent-authored model executes agent-authored code inside the
process that holds the labels.** A pickle is arbitrary code by construction; a
framework checkpoint usually permits custom layers. The scorer would be handing
the untrusted party a shell next to the one secret it exists to protect.

Restricting the format (safetensors or ONNX, plus an architecture from a fixed
registry) closes that, and costs the ability to try anything not in the registry
— which is most of what makes a hill-climb worth running.

Predictions have neither problem. The interface is a mapping from sample id to a
value; it admits any model in any framework, and nothing the agent produced is
ever executed by trusted code.

This is also what competition infrastructure converged on
([AI Competitions as the gold standard for empirical rigor](https://arxiv.org/abs/2505.00612),
May 2025), alongside the same supporting controls this project already has or
needs: no network from the submission, compute limits, and a cap on submissions
per interval.

### What predictions-only does not fix

The agent still sees holdout **inputs**, and can therefore fingerprint them
across runs and overfit by repeated submission. Label-only exposure is a
documented leak channel in its own right
([Li & Zhang, CCS 2021](https://yangzhangalmo.github.io/papers/CCS21-Label.pdf)).

That is not a reason to reject the design — it is the reason the holdout carries
a budget, and the next section is a better budget.

## Amendment to the holdout discipline: adopt the Ladder

The existing `HoldoutBudget` **counts queries and cuts off**. That is the crude
form of the right idea, and the literature has a strictly better one.

[Blum & Hardt, *The Ladder* (ICML 2015)](https://arxiv.org/abs/1502.04585) show
that the leak is driven by *feedback*, not by queries: an analyst who is told
their exact score every time overfits the holdout quickly, and one who is told
only "you improved" learns far less per query. Reporting a score only when it
beats the best so far by more than a threshold bounds leaderboard error at
`log(k)^(2/3) / n^(1/3)` in a fully adaptive model —
[later improved to `O(1/n^0.4)`](https://arxiv.org/abs/1706.02733) by
randomising the comparison.

So the scorer reports through a Ladder: a submission that does not clearly beat
the incumbent is told *"no improvement"* and nothing else. The counting budget
stays as a backstop, because the Ladder bounds the error and does not bound the
spend.

This is the second time a 2015-era result has turned out to be load-bearing here
after the 2026 literature pointed at the problem. Recorded so the next reader
does not re-derive it.

## Consequences

- The agent cannot report a metric at all. There is no field for it to fill.
- A run's number is reproducible by a third party who has the labels — the
  predictions are recorded, so scoring can be repeated.
- Tasks must define a prediction schema. That is easy for a fixed benchmark like
  the acoustic target and hard in general, so it is a per-target cost rather
  than a universal one.

## Not decided here

- **Wiring.** The runner does not yet assemble a `Candidate` from scorer output.
  Until it does, this is a component with no caller.
- **Ladder thresholds per task.** The step size is a task property and there is
  no task registry to hold it.
- **Whether inputs should be withheld too.** Prospective ground truth — scoring
  against data that did not exist at training time — closes the fingerprinting
  channel entirely and is not always available.
