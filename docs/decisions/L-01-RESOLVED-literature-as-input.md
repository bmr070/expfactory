---
id: L-01
parent: wayfinder:map2
labels: [wayfinder:decision]
mode: HITL
status: RESOLVED
raised: 2026-07-26
resolved: 2026-07-26
---

# L-01 — Literature as a first-class input, and the leak it found

## The ask

Drive the workload from recent (2025–2026) papers across adjacent domains —
computer vision, robotics, VLM, LLM, audio, edge, government research — weighting
work selected for presentation, then apply it and hill-climb against real state
of the art, using fusion, fine-tuning, RL, world models and whatever else the
reading turns up.

## What the reading actually turned up

A sweep on 2026-07-26 produced eleven papers, recorded in
[`docs/literature/corpus.json`](../literature/corpus.json). One of them changed
the codebase rather than the workload.

**[EchoHawk](https://arxiv.org/abs/2606.29589) (arXiv, 28 Jun 2026)** is a
counter-UAS acoustic pipeline whose stated central contribution is not the
pipeline. It is a documented case of **session-level data leakage** in a widely
used public drone-audio dataset: the recordings ship pre-segmented into short
clips, so a naive clip-level split puts adjacent slices of one continuous
recording into both train and test. Enforcing recording-session-grouped
cross-validation drops a random-forest baseline's detection probability at 1%
false-alarm rate from **0.796 to 0.745**.

Checked against our own gate set, this was not a workload problem. It was a hole.

`gate_no_leakage` intersects train and eval **sample ids**. In session-level
leakage every sample id is distinct — clip 7 and clip 8 are different samples.
The gate passes. Five points of Pd, invisible to every gate the factory had, in
precisely the domain this repository picked as its proving workload.

## Decision

**Adopt literature as a recorded input, and ratchet the finding into a gate.**

1. **G-09, `gate_no_group_leakage`.** Train and eval must be disjoint at the
   *group* level — recording, session, site, device, subject — not only per
   sample. Three states, and the middle one is the design:
   - no grouping declared → non-blocking warning that states what was *not*
     checked, because most data has no group structure and blocking there would
     make the gate something everyone routes around;
   - grouping declared, run recorded no group ids → **blocks**, fail-closed, or
     omitting the field becomes the technique;
   - groups intersect → **blocks**, naming them.

   The declaration lives on the verifier constructor next to `require_prereg`,
   **never on the candidate**. Same rule as baselines and guardrail thresholds: a
   constraint the agent may decline to declare is not a constraint.

2. **Provenance.** `literature.py` gives a hypothesis a resolvable citation:
   `Paper`, `Mechanism` (the transferable idea, which is the real unit — the same
   idea appears in several papers), and `ResearchHypothesis`, content-hashed so it
   can be bound into a preregistration and fixed before the run. `provenance_of`
   raises rather than returning empty, because a silent empty tuple is how an
   unattributed hypothesis comes to look attributed.

3. **Venue selection is recorded, ranked, and firewalled.** Programme committees
   run an expensive filter and publish the result; the brief asked to weight it.
   It ranks a reading list and nothing else. A test asserts `VenueTier` and
   `triage_score` appear nowhere in the gate set or the verifier. A factory that
   promoted results because their inspiration was an oral would be the purest
   form of the fooling this repository exists to prevent.

4. **The corpus is data, the reader is substrate.** `literature.py` is in
   `_HARNESS_PATHS` and CODEOWNERS — an agent that can edit `provenance_of` can
   cite a paper that does not exist. `docs/literature/corpus.json` is not, so the
   reading list grows without an override on the gate layer.

## Amendment to the "vehicle, not a ship target" invariant

`AGENTS.md` says the proving workload is *"a vehicle, not a ship target. The
acceptance bar is 'the gates behaved correctly,' not 'the model beat a
benchmark.'"* It then warns: *"If you find yourself trying to make something get
promoted, stop and re-read this paragraph."*

Chasing state of the art inverts that, and the invariant was right, so it is
amended rather than deleted:

> The acceptance bar for **the factory** remains "the gates behaved correctly."
> A hill-climb may now carry a **published external target**, and beating it is a
> permitted *outcome*. It is still never an acceptance criterion, and `promoted`
> is still derived only from the gates. A run in which every proposal is
> correctly rejected remains a passing run.

The safeguard that makes this survivable is in
[the target document](../research/acoustic-drone-detection.md): the bar chosen is
**0.745, the session-grouped number — not 0.796**. Competing against the leaked
figure would be trivially winnable and worth nothing. The first act of setting an
external target was to reject the flattering version of it.

## A hazard the corpus caught, recorded because it will recur

Test-time adaptation ([GRPO-TTA](https://arxiv.org/abs/2605.03403), May 2026) is
among the most attractive mechanisms in the corpus and would, applied naively
here, reintroduce session leakage **at inference**: the model specialises to the
exact recording it is scored on. G-09 does not catch it — the training split
stays disjoint and the contamination happens afterwards.

Two 2026 papers, weeks apart: one warning the field's numbers are inflated by
session leakage, the other proposing a method that reintroduces it. Neither cites
the other. The mechanism record carries the warning in its `preconditions` so the
next session inherits it instead of rediscovering it.

## Consequences

- 197 tests (was 170). G-09 has visible and held-out fixtures; both partitions
  were run once after the gate landed, per the standing rule, and both passed
  (3/3 and 2/2). Measured, not tuned.
- `python -m expfactory.selfcheck` now reports three suites.
- No existing verdict changes: with no grouping declared, G-09 is a non-blocking
  warning, so every prior candidate is adjudicated exactly as before.

## Not decided here

- **Whether G-09 should eventually fail closed by default.** Today an
  undeclared grouping warns. The stricter rule — every task must state its
  grouping or explicitly state it has none — is better and needs a task registry
  that does not exist yet.
- **Inference-time contamination in general.** H5 above is one instance. There is
  no gate for it, and it is not obvious there can be a purely deterministic one.
