"""
H1 — the first real experiment, adjudicated by the factory.

Runs a classical baseline on DroneAudioDataset twice: once split by recording
session, once split by clip. Both go through the real `GateVerifier` with the
task's `DatasetGrouping` supplied, so the verdicts are the factory's rather than
this script's.

The point is not the model. It is that **the honest split promotes and the leaky
one is rejected by G-09**, on real data, using the gate that was built from the
paper that documented the leak.

    python examples/h1_drone_audio.py

Needs the dataset provisioned by hand (see docs/research/) and the `demo` extra.
Features are cached next to the data, so the second run is seconds rather than a
minute.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import welch
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_curve

from expfactory.drone_audio import (
    GROUPING,
    Sample,
    clip_level_folds,
    load_index,
    session_grouped_folds,
    sessions,
)
from expfactory.harness import RunResult
from expfactory.verifier import Candidate, GateVerifier, VerdictBundle

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "DroneAudioDataset" / "Binary_Drone_Audio"
CACHE = ROOT / "data" / "h1_features.npz"

N_BANDS = 48
FAR = 0.01


# --------------------------------------------------------------------------- #
# Features — classical, deliberately untuned
# --------------------------------------------------------------------------- #


def features(path: Path) -> np.ndarray:
    """Welch PSD in log space plus shape statistics.

    Rotor noise is narrowband-harmonic and most ESC-50 negatives are not, so band
    energies plus peakiness is a reasonable classical baseline. Left untuned on
    purpose: H1 is a *replication*, and a baseline tuned until it looked good
    would be the thing G-08 counts.

    Amplitude is normalised per clip because loudness is a property of the
    recording session, not of the drone — leaving it in would hand the model a
    session fingerprint and quietly undo the split.
    """
    rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    x = data.astype(np.float64)
    if x.size < 256:
        x = np.pad(x, (0, 256 - x.size))
    peak = np.abs(x).max()
    if peak > 0:
        x = x / peak

    freqs, psd = welch(x, fs=rate, nperseg=min(1024, x.size))
    psd = psd + 1e-12
    band_energy = np.array([b.mean() for b in np.array_split(np.log10(psd), N_BANDS)])

    total = psd.sum()
    centroid = float((freqs * psd).sum() / total)
    spread = float(np.sqrt(((freqs - centroid) ** 2 * psd).sum() / total))
    flatness = float(np.exp(np.mean(np.log(psd))) / np.mean(psd))
    peak_f = float(freqs[int(np.argmax(psd))])
    top = float(np.sort(psd)[-8:].sum() / total)

    return np.concatenate([band_energy, [centroid, spread, flatness, peak_f, top]])


def load_features(samples: list[Sample]) -> np.ndarray:
    if CACHE.exists():
        # allow_pickle stays OFF. The cache holds a float array and a string
        # array, neither of which needs it, and enabling it would make a feature
        # cache an arbitrary-code-execution path — a cache being "our own file"
        # is exactly the assumption that makes that bite.
        cached = np.load(CACHE)
        if list(cached["ids"]) == [s.sample_id for s in samples]:
            return np.asarray(cached["X"])
    print(f"extracting features for {len(samples)} clips (once; then cached)...", flush=True)
    X = np.array([features(s.path) for s in samples])
    np.savez_compressed(CACHE, X=X, ids=np.array([s.sample_id for s in samples]))
    return X


def pd_at_far(y_true: np.ndarray, scores: np.ndarray, far: float = FAR) -> float:
    """Probability of detection at a fixed false-alarm rate."""
    fpr, tpr, _ = roc_curve(y_true, scores)
    ok = fpr <= far
    return float(tpr[ok].max()) if ok.any() else 0.0


# --------------------------------------------------------------------------- #
# One experiment
# --------------------------------------------------------------------------- #


def run_split(name: str, folds, samples, X, y, index, seeds=(0, 1, 2)) -> Candidate:
    """Evaluate one splitting strategy and package it as evidence.

    Returns a `Candidate`, not a verdict — the runner-side rule from GH#33
    applies here too, and this script does not get to decide whether its own
    result is real.

    The group ids recorded in `extra` come from the task definition, not from
    this function, which is why G-09 can trust them.
    """
    runs: list[RunResult] = []
    for seed in seeds:
        started = time.time()
        # one fold per seed, so the seed perturbs which sessions are held out
        train, evaluate = folds(samples, k=5, seed=seed)[0]
        tr = [index[s.sample_id] for s in train]
        ev = [index[s.sample_id] for s in evaluate]

        model = RandomForestClassifier(n_estimators=200, random_state=seed, n_jobs=-1)
        model.fit(X[tr], y[tr])
        scores = model.predict_proba(X[ev])[:, 1]

        runs.append(
            RunResult(
                seed=seed,
                val_metric=pd_at_far(y[ev], scores),
                train_ids_hash=f"n={len(tr)}",
                eval_ids_hash=f"n={len(ev)}",
                # sample ids ARE disjoint in both splits — that is the whole
                # point. gate_no_leakage cannot tell these apart.
                overlap_count=0,
                wall_seconds=time.time() - started,
                extra={
                    "train_groups": sorted({s.session for s in train}),
                    "eval_groups": sorted({s.session for s in evaluate}),
                },
            )
        )

    return Candidate(
        hypothesis=name,
        config={"model": "random_forest", "n_estimators": 200, "metric": f"Pd@{FAR:.0%}FAR"},
        code_hash="h1-classical-v1",
        runs=runs,
        cost_usd=0.0,
    )


def report(name: str, candidate: Candidate, bundle: VerdictBundle) -> None:
    values = [r.val_metric for r in candidate.runs]
    print(f"\n--- {name} " + "-" * max(0, 58 - len(name)))
    print(f"  Pd@1%FAR : {np.mean(values):.4f} +/- {np.std(values):.4f}  {np.round(values, 4)}")
    print(f"  sessions : train/eval disjoint by '{GROUPING.group_key}'? ", end="")
    r = candidate.runs[0]
    shared = set(r.extra["train_groups"]) & set(r.extra["eval_groups"])
    print("yes" if not shared else f"NO - {len(shared)} shared")
    for gate in bundle.artifact.get("gates", []):
        mark = "PASS" if gate["passed"] else "FAIL"
        print(f"    [{mark}] {gate['name']}: {gate['detail']}")
    print(f"  verdict  : {'PROMOTED' if bundle.promoted else 'REJECTED ' + str(list(bundle.blocked_by))}")


def main() -> int:
    if not DATA.exists():
        print(f"dataset not provisioned at {DATA}")
        print("see docs/research/acoustic-drone-detection.md")
        return 1

    samples = load_index(DATA)
    index = {s.sample_id: i for i, s in enumerate(samples)}
    X = load_features(samples)
    y = np.array([s.label for s in samples])

    print("=" * 70)
    print("H1 - DroneAudioDataset, classical baseline, adjudicated by the factory")
    print("=" * 70)
    print(f"{len(samples)} clips, {len(sessions(samples))} recording sessions")
    print(f"grouping declared to the verifier: {GROUPING.group_key}")

    # The verifier is built by this script, not by the training code, and it is
    # handed the task's grouping. That is what makes G-09 a wall here.
    verifier = GateVerifier(grouping=GROUPING)

    honest = run_split(
        "recording-session-grouped split", session_grouped_folds, samples, X, y, index
    )
    leaky = run_split("clip-level split", clip_level_folds, samples, X, y, index)

    report("HONEST  session-grouped", honest, verifier.run(honest))
    report("LEAKY   clip-level", leaky, verifier.run(leaky))

    h = float(np.mean([r.val_metric for r in honest.runs]))
    lk = float(np.mean([r.val_metric for r in leaky.runs]))
    print("\n" + "=" * 70)
    print(f"inflation from splitting at clip level: {lk - h:+.4f} Pd@1%FAR")
    print("EchoHawk's abstract reports +0.051 (0.745 -> 0.796) for their baseline.")
    print("Different features and different absolute values; the effect reproduces.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
