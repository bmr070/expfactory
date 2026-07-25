"""
Worked example: a sensor-fusion drone-vs-bird detector, hill-climbed by an agent.

Three experiments are proposed, exactly as an agent would propose them. One is a
real improvement. Two are the failure modes that make autonomous ML research
dangerous — and neither is detectable by reading the agent's summary, because in
both cases the agent honestly reports a higher number.

Modalities are synthetic stand-ins for the real thing:
  acoustic[0:8]  - mel-band energies (rotor harmonics vs wingbeat)
  rf[8:14]       - 2.4/5.8 GHz band power, FHSS hop-rate features
  vision[14:20]  - bbox aspect/area/motion-jitter statistics

Swap `make_dataset` for a real loader (RFUAV, DroneRF, Anti-UAV, DUT Anti-UAV,
UAV audio sets) and the harness is unchanged. That is the point: the gates are
domain-independent, the loader is not.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler

from expfactory.harness import Ledger, RunResult, code_fingerprint, report, run_experiment

N_FEATURES = 20
RNG_DATA = 12345  # dataset generation is fixed; only model seeds vary


def make_dataset(n: int = 3000) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (X, y, sample_ids). Drones are separable but only weakly, and the
    acoustic channel is deliberately noisy — like the real problem."""
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
    import hashlib
    return hashlib.sha256(np.sort(ids).tobytes()).hexdigest()[:16]


def _score(model, Xtr, ytr, Xev, yev) -> float:
    model.fit(Xtr, ytr)
    return float(average_precision_score(yev, model.predict_proba(Xev)[:, 1]))


# --------------------------------------------------------------------------- #
# The three candidate training functions an agent might write
# --------------------------------------------------------------------------- #

def train_baseline(config: dict, seed: int) -> RunResult:
    """Logistic regression on raw features. No scaling — so the RF channel (x40)
    dominates and the vision channel (x0.02) is effectively ignored."""
    tr, ev = TRAIN_IDX, EVAL_IDX
    model = LogisticRegression(max_iter=2000, random_state=seed)
    m = _score(model, X_ALL[tr], Y_ALL[tr], X_ALL[ev], Y_ALL[ev])
    return RunResult(seed, m, _hash_ids(tr), _hash_ids(ev),
                     len(np.intersect1d(tr, ev)), 0.0)


def train_scaled(config: dict, seed: int) -> RunResult:
    """HYPOTHESIS (real): per-modality standardisation lets the vision and acoustic
    channels contribute instead of being swamped by RF magnitude.

    Note the scaler is fit on TRAIN ONLY. This is the correct version.
    """
    tr, ev = TRAIN_IDX, EVAL_IDX
    scaler = StandardScaler().fit(X_ALL[tr])
    model = LogisticRegression(max_iter=2000, random_state=seed)
    m = _score(model, scaler.transform(X_ALL[tr]), Y_ALL[tr],
               scaler.transform(X_ALL[ev]), Y_ALL[ev])
    return RunResult(seed, m, _hash_ids(tr), _hash_ids(ev),
                     len(np.intersect1d(tr, ev)), 0.0)


def train_deeper_forest(config: dict, seed: int) -> RunResult:
    """HYPOTHESIS (noise): a bigger random forest will capture modality interactions.

    It does not. It moves the number around by roughly the seed noise band — which
    on a single lucky seed looks like a win, and is how hill-climbs drift for weeks.
    """
    tr, ev = TRAIN_IDX, EVAL_IDX
    scaler = StandardScaler().fit(X_ALL[tr])
    model = RandomForestClassifier(
        n_estimators=config.get("n_estimators", 300),
        max_depth=config.get("max_depth", 7),
        random_state=seed, n_jobs=-1,
    )
    m = _score(model, scaler.transform(X_ALL[tr]), Y_ALL[tr],
               scaler.transform(X_ALL[ev]), Y_ALL[ev])
    return RunResult(seed, m, _hash_ids(tr), _hash_ids(ev),
                     len(np.intersect1d(tr, ev)), 0.0)


def train_leaky_augment(config: dict, seed: int) -> RunResult:
    """HYPOTHESIS (leak): 'augment the training set with hard negatives mined from
    the full pool to improve bird rejection.'

    Sounds like a reasonable ML idea. Mining from the *full pool* pulls eval samples
    into training. The agent will report a big honest-looking gain.
    """
    tr, ev = TRAIN_IDX, EVAL_IDX
    rng = np.random.default_rng(seed)
    mined = rng.choice(IDS_ALL, size=400, replace=False)   # <-- from the full pool
    tr_aug = np.concatenate([tr, mined])

    scaler = StandardScaler().fit(X_ALL[tr_aug])
    model = LogisticRegression(max_iter=2000, random_state=seed)
    m = _score(model, scaler.transform(X_ALL[tr_aug]), Y_ALL[tr_aug],
               scaler.transform(X_ALL[ev]), Y_ALL[ev])
    return RunResult(seed, m, _hash_ids(tr_aug), _hash_ids(ev),
                     len(np.intersect1d(tr_aug, ev)), 0.0)


# --------------------------------------------------------------------------- #

def main() -> None:
    ledger = Ledger("runs/drone_detect.jsonl")
    seeds = (0, 1, 2, 3, 4)

    print("=" * 74)
    print("HILL-CLIMB: multimodal drone-vs-bird detection (metric = average precision)")
    print("=" * 74)

    base = run_experiment(
        hypothesis="baseline: logistic regression on raw fused features",
        config={"model": "logreg", "scaling": None},
        train_fn=train_baseline, ledger=ledger, seeds=seeds,
        code_hash=code_fingerprint("baseline-v1"), cost_usd=0.40,
    )
    print("\n" + report(base))

    for name, fn, cfg, hyp, cost in [
        ("A", train_scaled, {"model": "logreg", "scaling": "standard-train-only"},
         "per-modality standardisation unswamps vision/acoustic channels", 0.45),
        ("B", train_deeper_forest, {"model": "rf", "n_estimators": 300, "max_depth": 7},
         "deeper random forest captures cross-modality interactions", 2.10),
        ("C", train_leaky_augment, {"model": "logreg", "augment": "hard-negative-mining"},
         "mine hard negatives from the pool to improve bird rejection", 0.60),
    ]:
        exp = run_experiment(
            hypothesis=hyp, config=cfg, train_fn=fn, ledger=ledger, seeds=seeds,
            parent=base, baseline=base,
            code_hash=code_fingerprint(f"exp-{name}"), cost_usd=cost,
        )
        print(f"\n--- proposal {name} " + "-" * 55)
        print(report(exp, baseline=base))

    print("\n" + "=" * 74)
    best = ledger.best_promoted()
    print(f"best-so-far: {best.exp_id} @ {best.mean_metric:.4f} — {best.hypothesis}")
    print(f"ledger: {len(ledger.all())} experiments recorded, append-only")
    print("=" * 74)


if __name__ == "__main__":
    main()
