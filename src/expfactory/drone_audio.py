"""
drone_audio — the task definition for DroneAudioDataset.

The first real target (see `docs/research/acoustic-drone-detection.md`). This
module is the **task**, not the workload: it owns the things a training function
must not be trusted to get right, and nothing else.

## Why this is verification substrate

G-09 checks that train and eval groups are disjoint. It reads those groups from
`RunResult.extra`, which the *training function* writes — so the gate catches an
honest mistake and not a dishonest one. Exactly the shape GH#33 fixed for
verdicts and GH#39 fixed for metrics: a check is only as good as the provenance
of what it checks.

So the mapping from filename to recording session lives here, in the protected
set, and a workload imports it rather than deriving its own. An agent that could
supply its own session ids could report every clip as its own session and pass
G-09 while leaking wholesale.

## The leakage, visible in the filenames

DroneAudioDataset ships 1-second clips cut from longer continuous recordings, and
the provenance survives in the names:

    B_S2_D1_067-bebop_000_.wav ... B_S2_D1_067-bebop_004_.wav   one recording
    1-100032-A-00.wav          ... 1-100032-A-04.wav            one ESC-50 clip
    extra_membo_D2_2000.wav    ... extra_membo_D2_2055.wav      one long take

So a clip-level random split puts, for most clips, **four siblings of every test
clip into training**. The model can memorise the recording — its background, its
microphone, that airframe — and the reported number measures that.

EchoHawk (arXiv:2606.29589) documents this and reports 257 continuous recording
sessions behind the 1,332 drone files. This parse recovers the same count, which
is the check that it reads the naming scheme the way the paper did.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from expfactory.gates_v1 import DatasetGrouping

# Declared to the verifier so G-09 blocks rather than warns. Supplied by the
# task, never by a candidate — see the module docstring.
GROUPING = DatasetGrouping(
    group_key="recording_session",
    rationale=(
        "1-second clips cut from longer continuous recordings; most clips have "
        "four siblings from the same take"
    ),
    source="EchoHawk, arXiv:2606.29589 (documents the leak in this dataset)",
)

# --------------------------------------------------------------------------- #
# Provenance — GH#46
# --------------------------------------------------------------------------- #
#
# EchoHawk names the dataset and links nothing, which is what #46 was blocked on.
# It is Sara Al-Emadi's, published on GitHub alongside the 2019 IWCMC paper.
#
# **github.com stays off the egress allowlist.** Adding it would not open a
# dataset mirror, it would open the entire internet's code, and this is a
# 1.1 GB one-time fetch — the exact case hand-provisioning exists for.
#
# The commit SHA does the work a pinned digest would, and does it better. Git is
# content-addressed: this commit fixes the whole tree, it is the publisher's own
# identifier rather than one we recorded off our own first download, and it is
# checkable offline with `git rev-parse`. In `egress.py` terms that is a
# `publisher` digest, not a `first-fetch` one.
#
# HuggingFace mirrors of this data exist and are already allowlisted, which makes
# them the tempting route. Declined: a third-party re-upload is a stranger's copy,
# and pinning it would certify "the same bytes as that mirror" while reading like
# provenance. The origin is reachable by hand; use the origin.
DATASET_REPO = "https://github.com/saraalemadi/DroneAudioDataset"
DATASET_COMMIT = "1f1ffb214c63215c95176dcb70dda246f8ad96c1"
DATASET_TREE = "94301f74f18db840021b83d47a9c9601dc953511"

DRONE_LABEL = 1
UNKNOWN_LABEL = 0

# "B_S2_D1_067-bebop_000_" -> session "B_S2_D1_067"
_HYPHENATED = re.compile(r"^(?P<session>.+?)-[A-Za-z]+_\d+_$")
# "extra_membo_D2_2007" -> session "extra_membo_D2". 56 consecutive slices of one
# long take, and the only drone naming that does not use a hyphen.
_SEQUENTIAL = re.compile(r"^(?P<session>.+_[A-Za-z]\d+)_\d+$")
# ESC-50 "1-100032-A-00" -> source clip "1-100032-A"
_ESC50 = re.compile(r"^(?P<session>\d+-\d+-[A-Z])-\d+$")


class WrongDataset(RuntimeError):
    """The provisioned tree is not the commit the recorded numbers came from."""


def provisioned_commit(root: str | Path) -> str | None:
    """The commit a hand-provisioned clone sits at, or None if it is not a clone.

    Reads `.git` directly rather than shelling out to `git`. Three reasons, all
    learned here: a subprocess opens a console window on Windows unless every
    call site remembers `CREATE_NO_WINDOW` (it did not, seven times), it assumes
    git is on PATH inside whatever sandbox this runs in, and a plain file read is
    testable without a repository.
    """
    git = Path(root).resolve()
    # The dataset root is a subdirectory of the clone; walk up to find `.git`.
    for candidate in (git, *git.parents):
        if (candidate / ".git").exists():
            git = candidate / ".git"
            break
    else:
        return None

    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not head.startswith("ref:"):
        return head or None  # detached HEAD is the SHA itself

    ref = head.removeprefix("ref:").strip()
    loose = git / Path(ref)
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip() or None

    # Freshly-cloned repos often have refs packed rather than loose.
    try:
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            sha, _, name = line.partition(" ")
            if name.strip() == ref:
                return sha.strip()
    except OSError:
        pass
    return None


def verify_provisioned(root: str | Path, expected: str = DATASET_COMMIT) -> None:
    """Refuse a tree that is not the commit our recorded numbers came from.

    H1 reported Pd@1%FAR of 0.8762 on the session-grouped split. That number is
    only reproducible against specific bytes, and until now nothing checked which
    bytes were on disk — the tests read whatever `data/` happened to contain. A
    dataset silently updated upstream, or a partial clone, would move the number
    with no indication anything had changed.

    Raises rather than warning: a comparison against the wrong data is worse than
    no comparison, because it still produces a plausible figure.
    """
    actual = provisioned_commit(root)
    if actual is None:
        raise WrongDataset(
            f"{root} is not a git clone, so its provenance cannot be established. "
            f"Provision by hand: git clone {DATASET_REPO}"
        )
    if actual != expected:
        raise WrongDataset(
            f"provisioned tree is at {actual[:12]}, expected {expected[:12]}.\n"
            "The recorded numbers came from the expected commit. Either check out "
            "that commit, or re-measure and update DATASET_COMMIT in the same diff "
            "as the new figures — never one without the other."
        )


def session_of(filename: str) -> str:
    """The continuous recording a clip was cut from.

    Falls back to the whole stem — treating an unrecognised name as its own
    session — which is the *permissive* direction and therefore the one to watch:
    a clip wrongly given a unique session cannot collide with anything, so it
    silently weakens G-09 rather than tripping it.

    `unparsed_names` exists so a caller can refuse rather than discover that
    quietly, and a test asserts the shipped dataset parses completely.
    """
    stem = Path(filename).stem

    for pattern in (_ESC50, _HYPHENATED, _SEQUENTIAL):
        match = pattern.match(stem)
        if match:
            return match.group("session")
    return stem


@dataclass(frozen=True)
class Sample:
    """One clip. `session` is what G-09 will be handed."""

    sample_id: str
    path: Path
    label: int
    session: str


def load_index(root: str | Path) -> list[Sample]:
    """Index the binary task without reading any audio.

    Deliberately does not decode the wavs: the split has to be decided, and G-09
    satisfied, before anything expensive happens. Sorted, so a run is
    reproducible.
    """
    base = Path(root)
    if not base.exists():
        raise FileNotFoundError(
            f"{base} not found. DroneAudioDataset is provisioned by hand rather than "
            "fetched: github.com is deliberately absent from the egress allowlist, "
            "because allowing it would open far more than a dataset mirror."
        )

    out: list[Sample] = []
    for folder, label in (("yes_drone", DRONE_LABEL), ("unknown", UNKNOWN_LABEL)):
        directory = base / folder
        if not directory.is_dir():
            raise FileNotFoundError(f"expected {directory} to exist")
        for path in sorted(directory.glob("*.wav")):
            out.append(
                Sample(
                    sample_id=f"{folder}/{path.name}",
                    path=path,
                    label=label,
                    session=session_of(path.name),
                )
            )
    return out


def unparsed_names(samples: Iterable[Sample]) -> list[str]:
    """Clips whose session fell back to the whole stem.

    A non-empty result means the naming scheme changed and the grouping is now
    weaker than it looks — the failure mode worth failing loudly on.
    """
    return sorted(s.sample_id for s in samples if s.session == Path(s.path).stem)


def sessions(samples: Iterable[Sample]) -> dict[str, list[Sample]]:
    grouped: dict[str, list[Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.session, []).append(sample)
    return grouped


def session_grouped_folds(
    samples: Sequence[Sample], k: int = 5, seed: int = 0
) -> list[tuple[list[Sample], list[Sample]]]:
    """K folds split by *session*, never by clip.

    Sessions are assigned to folds and clips follow their session. That is the
    correction EchoHawk argues for, and it is why this returns folds rather than
    exposing a shuffle for a caller to misuse.

    Deterministic given `seed`, and the seed perturbs something real — which
    sessions land in which fold — rather than nothing.
    """
    if k < 2:
        raise ValueError(f"k must be at least 2, got {k}")

    names = sorted(sessions(samples))
    if len(names) < k:
        raise ValueError(f"{len(names)} sessions cannot make {k} folds")

    shuffled = list(names)
    random.Random(seed).shuffle(shuffled)
    assignment = {name: i % k for i, name in enumerate(shuffled)}

    return [
        (
            [s for s in samples if assignment[s.session] != fold],
            [s for s in samples if assignment[s.session] == fold],
        )
        for fold in range(k)
    ]


def clip_level_folds(
    samples: Sequence[Sample], k: int = 5, seed: int = 0
) -> list[tuple[list[Sample], list[Sample]]]:
    """The **wrong** split, kept deliberately.

    Shuffles clips with no regard for the recording they came from, which is what
    the paper reports as inflating the number. Present so the inflation can be
    *measured* on this dataset rather than cited, and so G-09 has something real
    to reject.

    Never use this to produce a result. It exists to be compared against.
    """
    shuffled = list(samples)
    random.Random(seed).shuffle(shuffled)
    return [
        (
            [s for i, s in enumerate(shuffled) if i % k != fold],
            [s for i, s in enumerate(shuffled) if i % k == fold],
        )
        for fold in range(k)
    ]


__all__ = [
    "DRONE_LABEL",
    "GROUPING",
    "UNKNOWN_LABEL",
    "Sample",
    "clip_level_folds",
    "load_index",
    "session_grouped_folds",
    "session_of",
    "sessions",
    "unparsed_names",
]
