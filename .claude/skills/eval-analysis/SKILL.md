---
name: eval-analysis
description: Read the ledger and judge what a run or a lineage actually shows, separating a real effect from noise, leakage, or a lucky seed. Use when analysing results, comparing runs, reading a verdict, or asked whether a number is real. Trigger words - analyse results, is this real, compare runs, read the ledger, eval, did it improve, why was this rejected.
---

# Eval analysis

The question is never "did the number go up". It is "does the evidence support
the claim", and the gates already encode most of the ways it does not.

## Steps

1. **Read the verdict before the metric.** `promoted` and `blocked_by` say what
   the gates concluded. A rejected run with a great number is the normal case,
   not an anomaly to explain away.

2. **Check which gates actually ran.** `bundle.gate_names`. A gate that was never
   armed says nothing, and reading its silence as a pass is the exact bug the LLM
   prober shipped with. G-07/G-08 only run under `require_prereg=True`; G-09 only
   blocks when the task supplied a `DatasetGrouping`; G-10 needs an attestation
   source.

3. **Spread before mean.** Report mean and spread across seeds together. A
   zero-width band is not stability, it is usually a seed that changes nothing,
   and it once let `gate_seed_variance` promote anything positive while appearing
   to scrutinise it.

4. **Ask what the split was.** `gate_no_leakage` intersects *sample ids* and
   passes cleanly on session-level leakage, because clips cut from one recording
   have distinct ids. That is G-09's job, and G-09 is silent unless the task
   declared a grouping. On the drone data the difference is +0.043 Pd.

5. **Read the lineage, not just the run.** How many preregistrations, how many
   promoted, and in what order. Four non-promoting attempts is what G-08 counts.

6. **Say what would change your mind.** If no result could have falsified the
   claim, the claim was not being tested.

## Traps

- **A green dashboard line is never a promotion signal.** The ledger adjudicates;
  the tracker observes. No adjudicating module can even import a tracking client
  (M2-06), and that is enforced by a test.
- **Do not compare against a number nobody recorded.** A baseline that is not a
  ledger row cannot be checked.
- **Do not quote a single inflation figure from memory.** The EchoHawk abstract
  and its Table 2 disagree, and that is recorded unresolved in the corpus.
- **The holdout is a budget, not a resource.** Every query spends it, and the
  Ladder reports a score only when it clearly beats the incumbent, because the
  leak is driven by feedback rather than by query count.
- **"Holdout" names two things here.** The experiment holdout and the held-out
  fixture partition protect different parties. See `CONTEXT.md`.

## Useful commands

```bash
python -m expfactory.selfcheck        # do the gates still classify correctly
python -m expfactory.llm_probe        # can a model get a bad candidate past them
```

## Related

`docs/GOTCHAS.md`, `src/expfactory/gate_probe.py`, `src/expfactory/scorer.py`,
`examples/h1_drone_audio.py`.
