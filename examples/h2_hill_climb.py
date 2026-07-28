"""
H2 — an honest hill-climb, preregistered, on the real task.

H1 asked whether the factory rejects a leaky split. This asks the question GH#5
is blocked on: **how many attempts does honest tuning actually take?**

`DEFAULT_MAX_ATTEMPTS = 3` says that a lineage accumulating four non-promoting
preregistrations is better explained as metric-shopping than as revision. That
number was calibrated against synthetic fixtures, and #5 records the objection
in its own title: *a threshold tuned on synthetic data is a starting point, not
a finding.* Generating more synthetic lineages cannot fix that — it is the same
data with more rows.

So this runs a real one. Every attempt below is a modelling idea somebody would
actually try on this task, chosen **before** any of them were run, and each is
preregistered before it executes. Whether each promotes is decided by G-07's
declared minimum effect, not by whether the number went up.

What it produces is one lineage, on one task, with one baseline. That is not a
recalibration and this script does not pretend otherwise — it is the first real
observation against a threshold that has had none, and the honest use of it is
to see whether 3 is in the right neighbourhood or obviously wrong.

    python examples/h2_hill_climb.py

Needs the dataset provisioned (see docs/research/) and the `demo` extra. Reuses
H1's cached features, so this is seconds rather than minutes.
"""

from __future__ import annotations

import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h1_drone_audio import DATA, load_features, pd_at_far  # noqa: E402

from expfactory.drone_audio import (  # noqa: E402
    GROUPING,
    load_index,
    session_grouped_folds,
    verify_provisioned,
)
from expfactory.harness import RunResult  # noqa: E402
from expfactory.prereg import Preregistration  # noqa: E402
from expfactory.verifier import Candidate, GateVerifier, Ledger  # noqa: E402

SEEDS = (0, 1, 2)
# H1's measured session-grouped result. The baseline a preregistration declares
# must match what the parent actually recorded, so this is the number to beat.
BASELINE = 0.8762
# What counts as a real improvement. Declared once, up front, for every attempt —
# a per-attempt effect size chosen after seeing the number is the shopping this
# whole apparatus exists to catch.
MINIMUM_EFFECT = 0.010


@dataclass(frozen=True)
class Attempt:
    """One modelling idea. Fixed before anything runs."""

    name: str
    rationale: str
    build: Any


# Chosen before running any of them, in the order somebody would actually try
# them. Deliberately NOT pruned afterwards to make the lineage look tidier —
# dropping the failures is precisely the behaviour G-08 counts.
ATTEMPTS = (
    Attempt(
        "more_trees",
        "200 -> 600; the cheapest thing to try, and variance-reduction is real",
        lambda seed: RandomForestClassifier(n_estimators=600, random_state=seed, n_jobs=-1),
    ),
    Attempt(
        "balanced_classes",
        "negatives outnumber drone clips; reweight rather than resample",
        lambda seed: RandomForestClassifier(
            n_estimators=200, class_weight="balanced", random_state=seed, n_jobs=-1
        ),
    ),
    Attempt(
        "shallow_forest",
        "53 features on ~1.3k drone clips invites overfitting to session texture",
        lambda seed: RandomForestClassifier(
            n_estimators=200, max_depth=8, random_state=seed, n_jobs=-1
        ),
    ),
    Attempt(
        "more_features_per_split",
        "sqrt(53)~7 may be too few when the signal is spread across bands",
        lambda seed: RandomForestClassifier(
            n_estimators=200, max_features=0.5, random_state=seed, n_jobs=-1
        ),
    ),
    Attempt(
        "extra_trees",
        "randomised thresholds usually help when features are noisy and correlated",
        lambda seed: ExtraTreesClassifier(n_estimators=400, random_state=seed, n_jobs=-1),
    ),
)


# H1's exact configuration, re-run here so the ancestor is a recorded ledger row
# rather than a number in a constant. G-07 rule 8 checks a declared baseline
# against what the parent actually scored.
BASELINE_ATTEMPT = Attempt(
    "baseline_h1",
    "H1's RandomForest(n_estimators=200), the parent this lineage descends from",
    lambda seed: RandomForestClassifier(n_estimators=200, random_state=seed, n_jobs=-1),
)


def evaluate(attempt: Attempt, samples, X, y, index) -> Candidate:
    """Run one attempt on the session-grouped split. Returns evidence, not a
    verdict — this script does not judge its own work."""
    runs: list[RunResult] = []
    for seed in SEEDS:
        started = time.time()
        train, ev = session_grouped_folds(samples, k=5, seed=seed)[0]
        tr_i = [index[s.sample_id] for s in train]
        ev_i = [index[s.sample_id] for s in ev]

        model = attempt.build(seed)
        model.fit(X[tr_i], y[tr_i])
        scores = model.predict_proba(X[ev_i])[:, 1]

        runs.append(
            RunResult(
                seed=seed,
                val_metric=pd_at_far(y[ev_i], scores),
                train_ids_hash=f"n={len(tr_i)}",
                eval_ids_hash=f"n={len(ev_i)}",
                overlap_count=0,
                wall_seconds=time.time() - started,
                extra={
                    "train_groups": sorted({s.session for s in train}),
                    "eval_groups": sorted({s.session for s in ev}),
                },
            )
        )
    return Candidate(
        hypothesis=attempt.name,
        config={"attempt": attempt.name, "rationale": attempt.rationale},
        code_hash="h2-hill-climb-v1",
        runs=runs,
        cost_usd=0.0,
    )


def main() -> int:
    if not DATA.exists():
        print(f"dataset not provisioned at {DATA}")
        return 1
    # The numbers below are only comparable to H1's if they came from the same
    # bytes. Refuses rather than warns; see GH#46.
    verify_provisioned(DATA)

    samples = load_index(DATA)
    index = {s.sample_id: i for i, s in enumerate(samples)}
    X = load_features(samples)
    y = np.array([s.label for s in samples])

    print("=" * 72)
    print("H2 - preregistered hill-climb on the session-grouped split")
    print("=" * 72)
    print(f"minimum effect {MINIMUM_EFFECT:+.3f}, seeds {SEEDS}")
    print(f"{len(ATTEMPTS)} attempts, all chosen before any were run\n")

    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(Path(tmp) / "h2.jsonl")
        verifier = GateVerifier(grouping=GROUPING, require_prereg=True, prereg_store=ledger)

        # The ancestor has to be RUN and RECORDED, not asserted.
        #
        # The first version of this script typed BASELINE = 0.8762 into the
        # preregistration and every attempt was rejected with "confirmatory run
        # has no parent: a baseline nobody recorded cannot be checked". That is
        # G-07 rule 8 working — a declared baseline is checked against what the
        # parent actually scored, read from the ledger, so a number I typed is
        # exactly as good as a number I made up. It is.
        print("  0. baseline (H1 config)  ", end="", flush=True)
        base_candidate = evaluate(BASELINE_ATTEMPT, samples, X, y, index)
        base_bundle = GateVerifier(grouping=GROUPING, id_factory=lambda: "h2-baseline").run(
            base_candidate
        )
        ledger.append(base_bundle)
        baseline = float(np.mean([r.val_metric for r in base_candidate.runs]))
        print(f"{baseline:.4f}  recorded as ancestor h2-baseline")
        if abs(baseline - BASELINE) > 0.02:
            print(f"     NOTE: H1 reported {BASELINE:.4f}; this run differs by more than noise")

        promoted_at: int | None = None
        previous_hash: str | None = None
        results: list[tuple[str, float, bool, tuple[str, ...]]] = []

        for n, attempt in enumerate(ATTEMPTS, 1):
            # Filed BEFORE the run. G-07 rule 2 checks ledger position, so this
            # ordering is the thing being demonstrated, not a formality.
            # `supersedes` chains each revision to the one before it. Without
            # it these preregs would be byte-identical -- same metric, same
            # baseline, same effect, same seeds -- and therefore hash-identical,
            # because a Preregistration records a *decision rule* and the rule
            # genuinely does not change between attempts. See the note printed
            # at the end: that collision is itself a finding about G-08.
            prereg = Preregistration(
                primary_metric="val_metric",
                direction="maximize",
                baseline_value=baseline,
                minimum_effect=MINIMUM_EFFECT,
                seeds=SEEDS,
                parent_id="h2-baseline",
                supersedes=previous_hash,
            )
            previous_hash = prereg.hash
            ledger.append_prereg(prereg)

            candidate = evaluate(attempt, samples, X, y, index)
            candidate = Candidate(
                hypothesis=candidate.hypothesis,
                config=candidate.config,
                code_hash=candidate.code_hash,
                runs=candidate.runs,
                cost_usd=0.0,
                parent_id="h2-baseline",
                prereg_hash=prereg.hash,
            )
            bundle = verifier.run(candidate)
            ledger.append(bundle)

            mean = float(np.mean([r.val_metric for r in candidate.runs]))
            results.append((attempt.name, mean, bundle.promoted, tuple(bundle.blocked_by)))
            if bundle.promoted and promoted_at is None:
                promoted_at = n

            mark = "PROMOTED" if bundle.promoted else "rejected"
            print(
                f"  {n}. {attempt.name:<24} {mean:.4f}  ({mean - baseline:+.4f})  {mark}"
                + (f"  {list(bundle.blocked_by)}" if not bundle.promoted else "")
            )

    print("\n" + "-" * 72)
    non_promoting = sum(1 for _, _, ok, _ in results if not ok)
    churn_fired = [
        n for n, (_, _, _, blocked) in enumerate(results, 1) if "prereg_churn" in blocked
    ]
    print(f"attempts filed              : {len(results)}")
    print(f"non-promoting               : {non_promoting}")
    print(f"first promotion at attempt  : {promoted_at if promoted_at else 'none'}")
    print("G-08 DEFAULT_MAX_ATTEMPTS   : 3")
    print(f"G-08 fired at attempt       : {churn_fired[0] if churn_fired else 'never'}")

    if promoted_at is not None and churn_fired:
        print(
            "\nFINDING for GH#5. This lineage PROMOTED at attempt "
            f"{promoted_at} and was still flagged as\n"
            f"metric-shopping at attempt {churn_fired[0]}. G-08 counts non-promoting "
            "preregistrations\nin the lineage and does not reset on a success, so "
            "continuing to test ideas\nafter a genuine win is scored the same as "
            "shopping for one.\n\n"
            "Whether that is wrong is a judgement about what the gate is for, and one\n"
            "lineage does not settle it. Recorded, NOT fixed: tuning a threshold to\n"
            "this single observation would repeat the error #5 exists to correct."
        )

    churned = non_promoting > 3
    print(
        "\nG-08 would have flagged this lineage as metric-shopping."
        if churned
        else "\nG-08 would NOT have flagged this lineage."
    )
    print(
        "One lineage, one task, one baseline. This is a data point against a\n"
        "threshold that had none — not a recalibration. See GH#5."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
