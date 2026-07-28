---
target: acoustic-drone-detection
status: PLANNED — nothing here has been run
dataset: DroneAudioDataset (+ ESC-50, Speech Commands as negatives)
bar: 93.8% Pd@1%FAR (EchoHawk CNN, session-grouped) — see the correction below
updated: 2026-07-27
---

# Target: acoustic drone detection under session-grouped evaluation

## Nothing below is a result

No experiment here has been executed, no number in the "predicted" column has
been measured, and the ledger contains no entry for any of it.

## Correction — the bar was wrong in the first version of this document

The first version set the target at **0.745 Pd@1%FAR** and repeated it into
`CLAUDE.md`, a decision record, the corpus, three commit messages and two Linear
tickets.

That number came from [EchoHawk's](https://arxiv.org/abs/2606.29589) **abstract**,
where it appears as an illustration — *"reduces, for example, a random-forest
baseline's detection probability at a 1% false-alarm rate from 0.796 to 0.745"*.

Reading the full text gives a different figure. Table 2 reports the random-forest
baseline at **72.3% ± 4.7%** under proper grouping, and a CNN at **93.8%**.

**I do not currently know how to reconcile 0.745 with 0.723.** They are plausibly
different experiments — the paper evaluates both a synthetic benchmark and real
recorded audio, and the abstract does not say which its example is drawn from.
Recorded as an open discrepancy rather than resolved by picking whichever is
convenient.

The failure was reading a headline number and not checking it against the
results tables. That is the exact thing this repository exists to catch, done by
the person building it, which is worth leaving written down.

**Consequence: the bar is higher than stated and the shape of the work changes.**
Beating a 72.3% random forest is a weekend. Clearing a 93.8% CNN is the actual
target, and it is what makes modern methods necessary rather than optional.

## The dataset, now named

**DroneAudioDataset.** 1,332 drone files drawn from only **257 continuous
recording sessions**, with negatives from **ESC-50** and a **Speech Commands**
corpus.

That ratio is the whole story: ~5 clips per session means a clip-level split
almost guarantees that every test clip has a sibling in training. It is also why
the group metadata is recoverable — session identity is derivable from the file
provenance rather than needing to be reconstructed.

`DatasetGrouping(group_key="recording_session")` is armed from the first run.
G-09 was built for this dataset before we knew its name.

## No code was released, despite the claim

The abstract and conclusion both state that all code, a synthetic data
generator, unit tests and figures are released. **There is no repository URL
anywhere in the paper.**

So "runs without any download" — the property that made this the first target
over [CST Anti-UAV](https://arxiv.org/abs/2507.23473) — does not hold in
practice. Reproduction means reimplementing from the description, and the
dataset has to be fetched after all.

## Protocol, fixed before any run

- **Metric.** Pd at 1% FAR. Single primary metric (M2-04): may promote or block,
  never both.
- **Split.** Recording-session-grouped cross-validation. Declared to the verifier
  so G-09 blocks any run that cannot *show* session disjointness.
- **Scoring.** Through `scorer.py`. The training code never sees labels and
  cannot report a metric; feedback goes through the Ladder.
- **Confusers.** Evaluation includes low-frequency harmonic confusers, not
  silence. Separating drone from truck is the task; separating drone from quiet
  is not.
- **Seeds.** Five, with the seed perturbing session fold assignment.

## The climb

| | hypothesis | mechanism | cost | predicted |
| -- | -- | -- | -- | -- |
| **H1** | Reproduce the session-grouped RF baseline | `session-grouped-cv` | CPU only | Pd near 72%. **Replication, not a finding** |
| **H2** | Rotor-harmonic + BPF features beat a generic spectrogram at rejecting ground-vehicle confusers | `rotor-harmonic-bpf` | cheap | gain concentrated in the confuser slice |
| **H3** | Fine-tuning a pretrained audio encoder (AST / BEATs / PANNs) clears the classical baseline | *new — see below* | GPU hours | this is the one aimed at 93.8% |
| **H4** | An explicit SNR curriculum beats single-operating-point training | `low-snr-curriculum` | moderate | gain at low SNR, flat or worse at high |
| **H5** | Per-session test-time adaptation | `grpo-test-time-adapt` | moderate | **hazard — see below** |

H1 must run first: G-07 rule 8 reads baselines from a *recorded* parent, so
nothing else is promotable until it exists.

**H3 is new to this document** and is the answer to "what about fine-tuning and
other models". A pretrained audio encoder is the standard modern move for this
task and fits in 12 GB. It also needs a corpus entry — currently the mechanism
pool has no pretrained-encoder mechanism, which is a gap in the reading rather
than in the plan.

## H5 remains a trap

Test-time adaptation ([GRPO-TTA](https://arxiv.org/abs/2605.03403)) adapts to the
evaluation session at inference, which is session-level leakage performed *after*
the split — where G-09 cannot reach it. Not a reason to drop it; a reason its
protocol must be pinned before it runs. Adaptation may consume unlabelled
deployment audio only, and the adapted model is scored on sessions it did not
adapt on, or the result is reported as an oracle bound and not as detection
performance.

## What "beating SOTA" would mean here

If H3 lands, the honest claim is: *on DroneAudioDataset, under recording-session-
grouped CV, a fine-tuned pretrained audio encoder exceeds EchoHawk's reported CNN
at 1% FAR.* One dataset, one protocol, one paper's baseline.

Not "state of the art in drone detection". Written before any run, because it is
much harder to overclaim in a PR body when the honest phrasing was fixed in
advance.

## Blocked on

- **Dataset provenance.** Where DroneAudioDataset actually lives, and its
  checksum. The egress allowlist cannot be widened on a guess — see the
  bootstrap problem in the linked issue.
- **torch + CUDA**, for H3 onward.
