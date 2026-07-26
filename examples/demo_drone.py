"""
Worked example: a sensor-fusion drone-vs-bird detector, hill-climbed by an agent.

Three experiments are proposed, exactly as an agent would propose them. One is a
real improvement. Two are failure modes. None of the three is distinguishable
from the others by reading the agent's summary, because in all three cases the
agent honestly reports the number it measured.

Modalities are synthetic stand-ins for the real thing:
  acoustic[0:8]  - mel-band energies (rotor harmonics vs wingbeat)
  rf[8:14]       - 2.4/5.8 GHz band power, FHSS hop-rate features
  vision[14:20]  - bbox aspect/area/motion-jitter statistics

Swap `make_dataset` for a real loader (RFUAV, DroneRF, Anti-UAV, DUT Anti-UAV,
UAV audio sets) and the harness is unchanged. That is the point: the gates are
domain-independent, the loader is not.

## Calibration

Every claim below is measured, not asserted, and `tests/test_demo_drone.py`
fails if any of them stops being true. An earlier version of this file planted
labels by intent and was wrong about three of the four scenarios -- the "noise"
case was in fact the best model in the demo, and the seed-variance gate it was
meant to exercise had a zero-width band and was rubber-stamping everything.

Measured over seeds (0, 1, 2, 3, 4), metric = average precision:

    baseline  logreg, raw features        0.8905 +/- 0.0015
    A         per-modality standardising  0.9177 +/- 0.0022  (+0.0271, band 0.0024)
    B         random forest, depth 3      0.8895 +/- 0.0107  (-0.0010, band 0.0097)
    C         pool-mined augmentation     0.9182 +/- 0.0017  (+0.0277, 127 leaked ids)

## Why the seeds have to move something

`gate_seed_variance` asks whether a delta clears the seed-noise band. That
question is vacuous if nothing in the pipeline is seeded. lbfgs logistic
regression ignores `random_state`, so an earlier version of this demo produced
five identical runs, a band of exactly 0.0000, and a gate that promoted any
positive delta whatsoever -- while appearing to do the opposite.

So each seed trains on a bootstrap resample of the training pool. That is a real
practice (it estimates sensitivity to the training sample), it leaves the eval
set untouched, and it keeps train and eval disjoint, so the leak gate still
measures what it says it measures.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler

from expfactory.harness import (
    Experiment,
    ExperimentLedger,
    RunResult,
    code_fingerprint,
    report,
    run_experiment,
)

N_FEATURES = 20
RNG_DATA = 12345  # dataset generation is fixed; only the training resample varies
SEEDS = (0, 1, 2, 3, 4)


def make_dataset(n: int = 3000) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (X, y, sample_ids). Drones are separable but only weakly, and the
    acoustic channel is deliberately noisy — like the real problem.

    Note the class signal is *additive per modality*: there are no cross-modality
    interaction terms. That is what makes proposal B a genuine dead end rather
    than a rigged one.
    """
    rng = np.random.default_rng(RNG_DATA)
    y = rng.integers(0, 2, n)

    X = rng.normal(0, 1, (n, N_FEATURES))
    # acoustic: rotor harmonics give drones a weak, noisy lift
    X[:, 0:8] += y[:, None] * rng.normal(0.35, 0.55, (n, 8))
    # rf: strong when present, but 40% of samples have no RF capture at all
    rf_present = rng.random(n) > 0.4
    X[:, 8:14] += (y * rf_present)[:, None] * rng.normal(1.1, 0.4, (n, 6))
    X[np.ix_(~rf_present, range(8, 14))] = 0.0
    # vision: moderate, degrades with range (simulated by a scale factor)
    rng_scale = rng.uniform(0.3, 1.0, n)
    X[:, 14:20] += (y * rng_scale)[:, None] * rng.normal(0.8, 0.5, (n, 6))
    # features are on wildly different scales, as raw sensor features are
    X[:, 8:14] *= 40.0
    X[:, 14:20] *= 0.02

    sample_ids = np.arange(n)
    return X, y, sample_ids


X_ALL, Y_ALL, IDS_ALL = make_dataset()
SPLIT = int(0.7 * len(IDS_ALL))
TRAIN_IDX, EVAL_IDX = IDS_ALL[:SPLIT], IDS_ALL[SPLIT:]


def _hash_ids(ids: np.ndarray) -> str:
    return hashlib.sha256(np.sort(ids).tobytes()).hexdigest()[:16]


def _bootstrap_train(seed: int) -> np.ndarray:
    """A per-seed resample of the training pool, with replacement.

    Drawn from TRAIN_IDX only, so train and eval stay disjoint and the honest
    scenarios record overlap_count == 0. This is the only thing the seed
    perturbs for the linear models, and without it the seed-variance gate has
    nothing to measure.
    """
    rng = np.random.default_rng(1000 + seed)
    return rng.choice(TRAIN_IDX, size=len(TRAIN_IDX), replace=True)


def _score(model: Any, tr: np.ndarray, ev: np.ndarray, scale: bool) -> float:
    Xtr, ytr, Xev, yev = X_ALL[tr], Y_ALL[tr], X_ALL[ev], Y_ALL[ev]
    if scale:
        # fit on train only; proposal C is the version that gets this wrong
        scaler = StandardScaler().fit(Xtr)
        Xtr, Xev = scaler.transform(Xtr), scaler.transform(Xev)
    model.fit(Xtr, ytr)
    return float(average_precision_score(yev, model.predict_proba(Xev)[:, 1]))


def _result(seed: int, metric: float, tr: np.ndarray, ev: np.ndarray) -> RunResult:
    return RunResult(seed, metric, _hash_ids(tr), _hash_ids(ev), len(np.intersect1d(tr, ev)), 0.0)


# --------------------------------------------------------------------------- #
# The candidate training functions an agent might write
# --------------------------------------------------------------------------- #


def train_baseline(config: dict, seed: int) -> RunResult:
    """Logistic regression on raw features. No scaling — so the RF channel (x40)
    dominates and the vision channel (x0.02) is effectively ignored."""
    tr, ev = _bootstrap_train(seed), EVAL_IDX
    model = LogisticRegression(max_iter=2000, random_state=seed)
    return _result(seed, _score(model, tr, ev, scale=False), tr, ev)


def train_scaled(config: dict, seed: int) -> RunResult:
    """HYPOTHESIS (real): per-modality standardisation lets the vision and acoustic
    channels contribute instead of being swamped by RF magnitude.

    Measured +0.0271 against a noise band of 0.0024 — an order of magnitude clear
    of it. This is what a real finding looks like, and it promotes.
    """
    tr, ev = _bootstrap_train(seed), EVAL_IDX
    model = LogisticRegression(max_iter=2000, random_state=seed)
    return _result(seed, _score(model, tr, ev, scale=True), tr, ev)


def train_forest(config: dict, seed: int) -> RunResult:
    """HYPOTHESIS (noise): a random forest will capture cross-modality interactions
    that a linear model cannot.

    It does not, because `make_dataset` puts no interaction terms in the data —
    the class signal is additive per modality. So the forest has nothing to find
    that the linear model was missing, and its number wanders around the baseline
    instead of beating it: -0.0010 on average against a 0.0097 noise band.

    The instructive part is the per-seed spread. Against the baseline's own seeds
    the deltas are -0.0095, +0.0018, +0.0039, +0.0128, -0.0139. Seed 3 alone reads
    as a +0.0128 win, and reporting that seed is how a hill-climb drifts for
    weeks. Five seeds and a noise band is what turns it back into a rejection.
    """
    tr, ev = _bootstrap_train(seed), EVAL_IDX
    model = RandomForestClassifier(
        n_estimators=config.get("n_estimators", 300),
        max_depth=config.get("max_depth", 3),
        random_state=seed,
        n_jobs=-1,
    )
    return _result(seed, _score(model, tr, ev, scale=True), tr, ev)


def train_leaky_augment(config: dict, seed: int) -> RunResult:
    """HYPOTHESIS (leak): 'augment the training set with hard negatives mined from
    the full pool to improve bird rejection.'

    Sounds like a reasonable ML idea. Mining from the *full pool* pulls roughly
    127 eval samples into training.

    And here is the part worth staring at: **the metric barely moves.** This
    scores +0.0277 over baseline, which is +0.0005 against the honest proposal A.
    A twenty-one-parameter linear model cannot memorise the rows it was handed, so
    contaminating it buys almost nothing. Pushing the mining up to 2400 of 3000
    samples — 727 of the 900 eval rows in the training set — still only reaches
    +0.0300.

    So there is no suspicious number to notice. Nothing about the metric, its
    variance, or its plausibility distinguishes this from a clean result. The only
    thing that catches it is `gate_no_leakage` counting shared sample ids, which
    is precisely why the harness records `train_ids_hash`, `eval_ids_hash` and
    `overlap_count` per run rather than trusting the number.

    A reviewer reading a summary would approve this. The ledger refuses it.
    """
    tr_pool, ev = _bootstrap_train(seed), EVAL_IDX
    rng = np.random.default_rng(seed)
    mined = rng.choice(IDS_ALL, size=400, replace=False)  # <-- from the full pool
    tr_aug = np.concatenate([tr_pool, mined])

    model = LogisticRegression(max_iter=2000, random_state=seed)
    return _result(seed, _score(model, tr_aug, ev, scale=True), tr_aug, ev)


# --------------------------------------------------------------------------- #

PROPOSALS = [
    (
        "A",
        train_scaled,
        {"model": "logreg", "scaling": "standard-train-only"},
        "per-modality standardisation unswamps vision/acoustic channels",
        0.45,
    ),
    (
        "B",
        train_forest,
        {"model": "rf", "n_estimators": 300, "max_depth": 3},
        "a random forest captures cross-modality interactions a linear model cannot",
        2.10,
    ),
    (
        "C",
        train_leaky_augment,
        {"model": "logreg", "augment": "hard-negative-mining"},
        "mine hard negatives from the pool to improve bird rejection",
        0.60,
    ),
]


def main(ledger_path: str | Path = "runs/drone_detect.jsonl") -> dict[str, Experiment]:
    """Run the hill-climb and return every experiment, keyed by proposal name.

    Returns rather than only printing so the calibration can be asserted by a
    test. This file used to be checked by nobody, which is how it came to be
    wrong about three scenarios and unable to import at all.
    """
    ledger = ExperimentLedger(ledger_path)
    out: dict[str, Experiment] = {}

    print("=" * 74)
    print("HILL-CLIMB: multimodal drone-vs-bird detection (metric = average precision)")
    print("=" * 74)

    base = run_experiment(
        hypothesis="baseline: logistic regression on raw fused features",
        config={"model": "logreg", "scaling": None},
        train_fn=train_baseline,
        ledger=ledger,
        seeds=SEEDS,
        code_hash=code_fingerprint("baseline-v1"),
        cost_usd=0.40,
    )
    out["baseline"] = base
    print("\n" + report(base))

    for name, fn, cfg, hyp, cost in PROPOSALS:
        exp = run_experiment(
            hypothesis=hyp,
            config=cfg,
            train_fn=fn,
            ledger=ledger,
            seeds=SEEDS,
            parent=base,
            baseline=base,
            code_hash=code_fingerprint(f"exp-{name}"),
            cost_usd=cost,
        )
        out[name] = exp
        print(f"\n--- proposal {name} " + "-" * 55)
        print(report(exp, baseline=base))

    print("\n" + "=" * 74)
    best = ledger.best_promoted()
    if best is None:
        print("best-so-far: nothing promoted")
    else:
        print(f"best-so-far: {best.exp_id} @ {best.mean_metric:.4f} - {best.hypothesis}")
    print(f"ledger: {len(ledger.all())} experiments recorded, append-only")
    print("=" * 74)
    return out


if __name__ == "__main__":
    main()
