---
target: acoustic-drone-detection
status: PLANNED — nothing here has been run
bar: Pd@1%FAR = 0.745 (EchoHawk, session-grouped)
updated: 2026-07-26
---

# Target: acoustic drone detection against harmonic confusers

## Nothing below is a result

This document specifies a hill-climb. No experiment in it has been executed, no
number in the "predicted" column has been measured, and the ledger contains no
entry for any of it. It is written to be turned into preregistrations, which is
the only way these hypotheses are allowed to reach a run.

## Why this task and not the vision one

The obvious candidate was [CST Anti-UAV](https://arxiv.org/abs/2507.23473)
(ICCV 2025 Workshops): thermal IR, tiny UAVs, and state of the art at **35.92%
state accuracy** against 67.69% on Anti-UAV410 — enormous headroom, which is
attractive. It was rejected as the *first* target on availability: the paper
says the benchmark "is about to be publicly released" and gives no download.
A hill-climb that cannot start is not a hill-climb.

The acoustic task wins on one property no other candidate has:
[EchoHawk](https://arxiv.org/abs/2606.29589) releases code, figures and a
synthetic data generator, and states that every result runs **without any
download**. The factory can therefore execute this target on a laptop, and the
first real run is not gated on dataset acquisition, a GPU, or an NDA.

## The bar is the honest number, not the published-looking one

EchoHawk reports the same baseline twice:

| protocol | Pd @ 1% FAR |
| -- | -- |
| naive clip-level split | 0.796 |
| **recording-session-grouped CV** | **0.745** |

The difference is not an improvement or a regression. It is the same model
measured two ways, and the higher number is an artifact of adjacent slices of one
continuous recording landing on both sides of the split.

**The target is 0.745.** Competing against 0.796 would be competing against a
leak: trivially winnable, and the win would be worth nothing. This is the whole
thesis of the repository applied to its own goalposts — and it is the reason
[G-09](../decisions/L-01-RESOLVED-literature-as-input.md) had to exist before
this target could be set, because until last week the factory could not have
detected the difference between the two rows above.

## Protocol, fixed before any run

- **Metric.** Probability of detection at 1% false-alarm rate. Single primary
  metric, per M2-04: it may promote or block, never both.
- **Split.** Recording-session-grouped cross-validation. Declared to the verifier
  as `DatasetGrouping(group_key="recording_session")`, so G-09 blocks any run
  that cannot show session disjointness rather than merely asserting it.
- **Confusers.** Evaluation includes low-frequency harmonic confusers (ground
  vehicles). A detector that separates drone from silence is not the thing being
  measured.
- **Seeds.** Five, with the seed perturbing the session fold assignment — so the
  noise band measures what it claims to. (The demo's band was once identically
  zero for exactly this reason; see `AGENTS.md#gotchas`.)

## Hypotheses, ranked by cost

Each becomes one preregistration citing the mechanism and paper it came from.
H1 must run first: rules 8 and 2 of G-07 read the baseline from a *recorded*
parent, so nothing else is promotable until the baseline is in the ledger.

| | hypothesis | mechanism | cost | predicted |
| -- | -- | -- | -- | -- |
| **H1** | Reproduce the session-grouped baseline | `session-grouped-cv` | free | Pd ≈ 0.745. **Replication, not a finding** |
| **H2** | Rotor-harmonic + blade-passing-frequency features beat a generic spectrogram classifier at rejecting ground-vehicle confusers | `rotor-harmonic-bpf` | cheap | gain concentrated in the confuser slice, not overall |
| **H3** | An explicit SNR curriculum beats single-operating-point training | `low-snr-curriculum` | cheap | gain at low SNR, flat or slightly worse at high SNR |
| **H4** | A latent forecast head carries the track through masked segments | `latent-forecast-head` | expensive | gain only on sequences with dropout; no effect on isolated clips |
| **H5** | Per-session test-time adaptation specialises the detector to deployment conditions | `grpo-test-time-adapt` | moderate | **see the hazard below** |

Note the shape of every prediction: a *slice* where the gain should appear, and
a slice where it should not. A hypothesis that predicts "goes up" is unfalsifiable
in practice, because something always goes up somewhere.

## H5 is a trap, and the corpus is what caught it

Test-time adaptation is the fashionable 2026 mechanism
([GRPO-TTA](https://arxiv.org/abs/2605.03403)) and it is the one hypothesis here
that could produce a large, clean-looking, entirely fake gain.

Adapting to the evaluation session at test time is *session-level leakage
performed at inference instead of at split time*. The model ends up specialised
to the exact recording it is scored on. G-09 will not catch it: the training
split stays disjoint, and the contamination happens after the split.

This is not a reason to drop H5 — per-session adaptation is a legitimate
deployment technique, and the honest version is real. It is a reason that H5
needs its protocol pinned before it runs:

- adaptation may consume **unlabelled** deployment audio only;
- the adapted model is scored on **held-out sessions it did not adapt on**, or
  the result is reported as an oracle upper bound and explicitly not as detection
  performance;
- the preregistration states which of the two it is, before the run.

Two 2026 papers, three weeks apart, one warning that the field's numbers are
inflated by session leakage and the other proposing a method that reintroduces it
at inference. Neither cites the other. Finding that is the pipeline paying for
itself, and it is recorded here so the next session does not have to rediscover
it.

## What "beating SOTA" would and would not mean

If H2 lands, the honest claim is: *on EchoHawk's synthetic benchmark, under
session-grouped CV, a harmonic-structure feature set exceeds their random-forest
baseline's 0.745 Pd@1%FAR.* That is a real result against a real published number
and a narrow one — one benchmark, largely synthetic, one baseline that the
paper's authors did not present as their strongest system.

It would **not** be "state of the art in drone detection". The corpus contains
several systems on other benchmarks with no comparable published number on this
protocol, and no comparison across benchmarks is meaningful. Writing that
sentence down now, before any run, is deliberate: it is much harder to overclaim
in a PR body when the honest phrasing was fixed in advance.
