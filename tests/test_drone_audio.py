"""
The DroneAudioDataset task definition.

The load-bearing property is the session parse. G-09 reads groups out of
`RunResult.extra`, which a training function writes — so the gate catches an
honest mistake and not a dishonest one, and the mapping from filename to
recording session has to come from trusted code.

Filename parsing is tested without the dataset; the shipped-data assertions skip
when it has not been provisioned, because it is 1.1 GB fetched by hand (github.com
is deliberately not on the egress allowlist).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from expfactory.drone_audio import (
    GROUPING,
    Sample,
    WrongDataset,
    clip_level_folds,
    load_index,
    provisioned_commit,
    session_grouped_folds,
    session_of,
    sessions,
    unparsed_names,
    verify_provisioned,
)
from expfactory.gates_v1 import gate_no_group_leakage
from expfactory.harness import Experiment, RunResult

DATA = Path(__file__).resolve().parent.parent / "data" / "DroneAudioDataset" / "Binary_Drone_Audio"
needs_data = pytest.mark.skipif(
    not DATA.exists(), reason="DroneAudioDataset not provisioned; see docs/research/"
)


# --------------------------------------------------------------------------- #
# The session parse — the whole point
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # five 1-second slices of one drone recording
        ("B_S2_D1_067-bebop_000_.wav", "B_S2_D1_067"),
        ("B_S2_D1_067-bebop_004_.wav", "B_S2_D1_067"),
        ("mixed_membo_9-membo_003_.wav", "mixed_membo_9"),
        # ESC-50 negatives: FOLD-CLIP-TAKE-TARGET, five slices per source clip
        ("1-100032-A-00.wav", "1-100032-A"),
        ("1-100032-A-04.wav", "1-100032-A"),
        # 56 consecutive slices of one long take, the only unhyphenated drone name
        ("extra_membo_D2_2000.wav", "extra_membo_D2"),
        ("extra_membo_D2_2055.wav", "extra_membo_D2"),
    ],
)
def test_siblings_resolve_to_one_session(filename: str, expected: str):
    assert session_of(filename) == expected


def test_clips_from_one_recording_share_a_session():
    """Stated directly, because this is what makes a clip-level split leak."""
    siblings = [f"B_S2_D1_067-bebop_00{i}_.wav" for i in range(5)]
    assert len({session_of(name) for name in siblings}) == 1


def test_different_recordings_do_not_collide():
    """The other direction: over-merging sessions would shrink the eval set and
    look like a stricter split than it is."""
    assert session_of("B_S2_D1_067-bebop_000_.wav") != session_of("B_S2_D1_068-bebop_000_.wav")
    assert session_of("1-100032-A-00.wav") != session_of("1-100038-A-00.wav")


def test_an_unrecognised_name_falls_back_to_itself():
    """Permissive, and therefore the dangerous direction: a clip given a unique
    session cannot collide, so it weakens G-09 silently rather than tripping it.
    `unparsed_names` is how a caller notices."""
    assert session_of("something_unexpected.wav") == "something_unexpected"


# --------------------------------------------------------------------------- #
# Folds
# --------------------------------------------------------------------------- #


def _samples(n_sessions: int = 10, per_session: int = 5) -> list[Sample]:
    return [
        Sample(
            sample_id=f"s{s}-c{c}",
            path=Path(f"s{s}-c{c}.wav"),
            label=s % 2,
            session=f"sess-{s}",
        )
        for s in range(n_sessions)
        for c in range(per_session)
    ]


def test_session_grouped_folds_never_split_a_session():
    """The correction the whole task exists for."""
    for train, evaluate in session_grouped_folds(_samples(), k=5, seed=0):
        assert not ({s.session for s in train} & {s.session for s in evaluate})


def test_every_sample_appears_in_exactly_one_eval_fold():
    samples = _samples()
    seen = [s.sample_id for _, evaluate in session_grouped_folds(samples, k=5) for s in evaluate]
    assert sorted(seen) == sorted(s.sample_id for s in samples)


def test_folds_are_deterministic_for_a_seed():
    a = session_grouped_folds(_samples(), k=5, seed=7)
    b = session_grouped_folds(_samples(), k=5, seed=7)
    assert [[s.sample_id for s in ev] for _, ev in a] == [[s.sample_id for s in ev] for _, ev in b]


def test_the_seed_actually_moves_the_assignment():
    """A seed that changes nothing gives `gate_seed_variance` a zero-width band —
    the demo's exact failure. Here the seed decides which sessions land where."""
    a = [{s.sample_id for s in ev} for _, ev in session_grouped_folds(_samples(20), k=5, seed=0)]
    b = [{s.sample_id for s in ev} for _, ev in session_grouped_folds(_samples(20), k=5, seed=1)]
    assert a != b


def test_too_few_sessions_for_k_is_refused():
    with pytest.raises(ValueError, match="cannot make"):
        session_grouped_folds(_samples(n_sessions=3), k=5)


def test_the_clip_level_split_really_does_leak():
    """The wrong split is kept so the inflation can be measured rather than
    cited. This asserts it is genuinely wrong — if it stopped leaking, the
    comparison it exists for would be meaningless."""
    shared = 0
    for train, evaluate in clip_level_folds(_samples(), k=5, seed=0):
        shared += len({s.session for s in train} & {s.session for s in evaluate})
    assert shared > 0, "clip-level folds must leak; that is the point of keeping them"


# --------------------------------------------------------------------------- #
# G-09 wiring
# --------------------------------------------------------------------------- #


def _experiment(train, evaluate) -> Experiment:
    exp = Experiment(exp_id="e", parent_id=None, hypothesis="h", config={}, code_hash="c")
    exp.runs = [
        RunResult(
            seed=0,
            val_metric=0.9,
            train_ids_hash="t",
            eval_ids_hash="e",
            overlap_count=0,
            wall_seconds=0.0,
            extra={
                "train_groups": sorted({s.session for s in train}),
                "eval_groups": sorted({s.session for s in evaluate}),
            },
        )
    ]
    return exp


def test_g09_accepts_a_session_grouped_split():
    train, evaluate = session_grouped_folds(_samples(), k=5, seed=0)[0]
    assert gate_no_group_leakage(_experiment(train, evaluate), grouping=GROUPING).passed


def test_g09_rejects_a_clip_level_split():
    """End to end: the gate built from the paper rejects the split the paper
    warns about, on the dataset the paper found it in."""
    train, evaluate = clip_level_folds(_samples(), k=5, seed=0)[0]
    result = gate_no_group_leakage(_experiment(train, evaluate), grouping=GROUPING)

    assert not result.passed and result.blocking
    assert "GROUP LEAK" in result.detail


def test_the_task_declares_its_grouping():
    """G-09 only bites when a task supplies this, so the task must carry it."""
    assert GROUPING.group_key == "recording_session"
    assert "2606.29589" in GROUPING.source


# --------------------------------------------------------------------------- #
# The shipped dataset
# --------------------------------------------------------------------------- #


@needs_data
def test_the_parse_recovers_the_session_count_the_paper_reports():
    """Independent confirmation that this reads the naming scheme the way
    EchoHawk did: they report **257 continuous recording sessions** behind the
    1,332 drone files, and this parse finds exactly that.

    A count that drifted would mean the grouping is not the paper's grouping, and
    any comparison against their numbers would be meaningless.
    """
    samples = load_index(DATA)
    drone = [s for s in samples if s.label == 1]

    assert len(drone) == 1332
    assert len(sessions(drone)) == 257


@needs_data
def test_every_shipped_filename_parses():
    """The fallback is permissive, so an unparsed name weakens G-09 silently.
    Drone clips must all parse; ESC-50 negatives include single-slice sources
    whose stem legitimately *is* the session."""
    drone = [s for s in load_index(DATA) if s.label == 1]
    assert unparsed_names(drone) == []


@needs_data
def test_most_clips_have_siblings_which_is_why_this_matters():
    """255 of 257 drone sessions hold five clips. A clip-level split therefore
    puts four siblings of almost every test clip into training."""
    drone = [s for s in load_index(DATA) if s.label == 1]
    sized = [len(v) for v in sessions(drone).values()]

    assert sized.count(5) == 255
    assert max(sized) == 56, "the extra_membo_D2 take is one continuous recording"


# --------------------------------------------------------------------------- #
# Provenance — GH#46
# --------------------------------------------------------------------------- #


def test_the_dataset_is_pinned_to_a_commit_not_just_a_name():
    """EchoHawk names the dataset and links nothing, which is what #46 was
    blocked on. The origin is Sara Al-Emadi's GitHub repo, and the commit SHA
    does a pinned digest's job better than a digest could: git is
    content-addressed, so one SHA fixes the whole tree, and it is the
    publisher's own identifier rather than one recorded off our first download.
    """
    from expfactory.drone_audio import DATASET_COMMIT, DATASET_REPO

    assert DATASET_REPO == "https://github.com/saraalemadi/DroneAudioDataset"
    assert len(DATASET_COMMIT) == 40 and all(c in "0123456789abcdef" for c in DATASET_COMMIT)


def test_github_is_still_not_on_the_egress_allowlist():
    """The dataset lives on github.com and that is *not* a reason to allowlist
    it. Adding github.com would not open a dataset mirror, it would open the
    entire internet's code. A 1.1 GB one-time fetch is the case
    hand-provisioning exists for.

    Asserted here rather than only in egress's own tests, because this is the
    module with the motive to widen it.
    """
    from expfactory.egress import ALLOWED_HOSTS

    assert not any("github" in h for h in ALLOWED_HOSTS)


def test_a_tree_with_no_git_metadata_is_refused(tmp_path: Path):
    """Refuses rather than warns. A comparison against the wrong data still
    produces a plausible number, which is worse than producing none."""
    with pytest.raises(WrongDataset, match="not a git clone"):
        verify_provisioned(tmp_path)


def test_a_tree_at_the_wrong_commit_is_refused(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("a" * 40, encoding="utf-8")

    with pytest.raises(WrongDataset, match="expected"):
        verify_provisioned(tmp_path)


def test_the_refusal_says_to_change_the_pin_and_the_numbers_together(tmp_path: Path):
    """The dangerous repair is bumping DATASET_COMMIT so the check passes while
    the recorded figures still describe the old bytes."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("b" * 40, encoding="utf-8")

    with pytest.raises(WrongDataset) as exc:
        verify_provisioned(tmp_path)
    assert "never one without the other" in str(exc.value)


@pytest.mark.parametrize("layout", ["detached", "loose-ref", "packed-refs"])
def test_the_commit_is_found_however_the_clone_stores_it(tmp_path: Path, layout: str):
    """A fresh clone may keep refs loose or packed, and a checkout by SHA leaves
    HEAD detached. Reading only one of the three would make the check pass or
    fail on how the clone happened to be made."""
    sha = "c" * 40
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)

    if layout == "detached":
        (git / "HEAD").write_text(sha + "\n", encoding="utf-8")
    else:
        (git / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
        if layout == "loose-ref":
            (git / "refs" / "heads" / "master").write_text(sha + "\n", encoding="utf-8")
        else:
            (git / "packed-refs").write_text(
                f"# pack-refs with: peeled\n{sha} refs/heads/master\n", encoding="utf-8"
            )

    assert provisioned_commit(tmp_path) == sha


def test_the_commit_is_found_from_a_subdirectory(tmp_path: Path):
    """The dataset root is a subdirectory of the clone, so the walk upward is
    what makes this work in practice rather than only in a fixture."""
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("d" * 40, encoding="utf-8")
    deep = tmp_path / "Binary_Drone_Audio" / "yes_drone"
    deep.mkdir(parents=True)

    assert provisioned_commit(deep) == "d" * 40


@needs_data
def test_the_shipped_clone_is_the_commit_the_numbers_came_from():
    """The gap this closes: H1 reported Pd@1%FAR 0.8762 against whatever `data/`
    happened to contain, and nothing checked which bytes those were. A dataset
    updated upstream would have moved the number silently."""
    verify_provisioned(DATA)
