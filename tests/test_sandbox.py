"""
The execution environment the runner hands an agent (ticket 07).

Most of these are name-confusion cases, because that is where the bugs are — the
same reason `test_egress.py` is mostly host-confusion. A workspace name is
attacker-influenced input: it comes from a ticket id, and anyone who can file a
ticket can choose it.

Windows gets its own cases and they are not hypothetical: this runs on it.
`CON` is not a usable directory, `BRE-1.` and `BRE-1` are the same path, and so
are `bre-1` and `BRE-1`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from expfactory.sandbox import SecretStore, WorkspaceRefused, WorkspaceRoot

# --------------------------------------------------------------------------- #
# Names are refused, not sanitized
# --------------------------------------------------------------------------- #


def test_a_normal_ticket_id_gets_its_own_directory(tmp_path: Path):
    root = WorkspaceRoot(tmp_path)
    ws = root.prepare("BRE-1")

    assert ws.path.is_dir()
    assert ws.path.parent == tmp_path.resolve()
    assert ws.ticket == "BRE-1"


@pytest.mark.parametrize(
    "hostile",
    [
        "../escape",
        "../../etc/passwd",
        "a/b",
        "a\\b",
        "/absolute",
        "C:\\Windows",
        ".hidden",
        "",
        "   ",
        "..",
        ".",
        "name with spaces",
        "semi;colon",
        "null\x00byte",
        "x" * 65,
    ],
)
def test_a_name_that_is_not_plainly_safe_is_refused(tmp_path: Path, hostile: str):
    """Refused rather than sanitized. Every sanitizer is lossy, and a lossy
    mapping lets two different tickets land in one directory —
    `BRE-1/../BRE-2` and `BRE-2` must not become the same workspace."""
    with pytest.raises(WorkspaceRefused):
        WorkspaceRoot(tmp_path).prepare(hostile)


def test_nothing_is_created_when_a_name_is_refused(tmp_path: Path):
    """A refusal that left a directory behind would be a refusal in name only."""
    with pytest.raises(WorkspaceRefused):
        WorkspaceRoot(tmp_path).prepare("../escape")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("reserved", ["CON", "con", "NUL", "COM1", "LPT9", "con.txt"])
def test_windows_reserved_device_names_are_refused(tmp_path: Path, reserved: str):
    """These cannot be directories on Windows. Allowing them would turn a
    platform quirk into a runner bug nobody could read."""
    with pytest.raises(WorkspaceRefused, match="reserved device name"):
        WorkspaceRoot(tmp_path).prepare(reserved)


@pytest.mark.parametrize("colliding", ["BRE-1.", "BRE-1 ", "BRE-1.."])
def test_trailing_dots_and_spaces_are_refused(tmp_path: Path, colliding: str):
    """Windows strips them, so these would silently become `BRE-1` and two
    tickets would share one directory."""
    with pytest.raises(WorkspaceRefused):
        WorkspaceRoot(tmp_path).prepare(colliding)


def test_the_root_itself_is_never_a_workspace(tmp_path: Path):
    """`prepare` empties what it returns. Returning the root would empty the
    root, taking every other ticket's workspace with it."""
    with pytest.raises(WorkspaceRefused):
        WorkspaceRoot(tmp_path).prepare(".")


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #


def test_two_tickets_do_not_share_a_directory(tmp_path: Path):
    root = WorkspaceRoot(tmp_path)
    assert root.prepare("BRE-1").path != root.prepare("BRE-2").path


def test_preparing_again_starts_empty(tmp_path: Path):
    """A workspace carrying the previous attempt's files is how one run's output
    becomes another run's input, which makes a result depend on what happened to
    be on disk."""
    root = WorkspaceRoot(tmp_path)
    first = root.prepare("BRE-1")
    (first.path / "leftover.txt").write_text("from the last run", encoding="utf-8")

    second = root.prepare("BRE-1")

    assert second.path == first.path
    assert list(second.path.iterdir()) == []


def test_discard_removes_it_and_is_silent_when_absent(tmp_path: Path):
    root = WorkspaceRoot(tmp_path)
    root.prepare("BRE-1")
    root.discard("BRE-1")
    root.discard("BRE-1")  # again, no error

    assert root.existing() == ()


def test_existing_lists_what_survived_a_restart(tmp_path: Path):
    root = WorkspaceRoot(tmp_path)
    root.prepare("BRE-2")
    root.prepare("BRE-1")

    assert root.existing() == ("BRE-1", "BRE-2")


def test_existing_is_empty_rather_than_raising_when_the_root_is_absent(tmp_path: Path):
    assert WorkspaceRoot(tmp_path / "not-created-yet").existing() == ()


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #


def test_declared_names_are_public_and_values_are_not():
    store = SecretStore({"LINEAR_API_KEY": "lin_abc", "GH_TOKEN": "ghp_xyz"})

    assert store.names() == ("GH_TOKEN", "LINEAR_API_KEY")
    assert "lin_abc" not in repr(store)
    assert "ghp_xyz" not in str(store)
    assert "GH_TOKEN" in repr(store)


def test_there_is_no_bulk_accessor():
    """Same rule as `LabelStore`: a store that can be dumped is one that will be
    dumped, into a log or a traceback, and the reader is whoever gets the crash
    report. Asserted so adding a convenience accessor fails loudly."""
    store = SecretStore({"GH_TOKEN": "ghp_xyz"})

    for name in dir(store):
        if name.startswith("_"):
            continue
        attr = getattr(store, name)
        value = attr() if callable(attr) and name == "names" else None
        assert not isinstance(value, dict), f"{name}() returns a mapping of secrets"


def test_one_named_secret_can_be_read():
    assert SecretStore({"GH_TOKEN": "ghp_xyz"}).use("GH_TOKEN") == "ghp_xyz"


def test_an_unknown_secret_raises_rather_than_returning_none():
    """A silently-absent credential produces an authentication error somewhere
    far away from the missing configuration."""
    with pytest.raises(KeyError, match="no secret named"):
        SecretStore({"GH_TOKEN": "x"}).use("LINEAR_API_KEY")


def test_an_empty_value_is_refused_at_construction():
    with pytest.raises(ValueError, match="empty"):
        SecretStore({"GH_TOKEN": ""})


def test_an_unnamed_secret_is_refused_because_it_cannot_be_scrubbed():
    with pytest.raises(ValueError, match="cannot be scrubbed"):
        SecretStore({"": "value"})


def test_a_child_environment_has_every_declared_secret_removed():
    """SPEC.md §15.3: adapters MUST declare secret environment names so local and
    remote launchers can remove them from child environments."""
    store = SecretStore({"GH_TOKEN": "ghp_xyz", "LINEAR_API_KEY": "lin_abc"})
    base = {"PATH": "/usr/bin", "GH_TOKEN": "ghp_xyz", "LINEAR_API_KEY": "lin_abc", "HOME": "/h"}

    child = store.child_env(base)

    assert child == {"PATH": "/usr/bin", "HOME": "/h"}


def test_every_declared_secret_is_removed_not_only_the_ones_in_use():
    """A child that never asked for a credential should not be able to read one.
    Deciding per-run which to strip puts that decision in the hands of whoever
    wrote the run."""
    store = SecretStore({"GH_TOKEN": "a", "LINEAR_API_KEY": "b", "HF_TOKEN": "c"})
    store.use("GH_TOKEN")

    child = store.child_env({"HF_TOKEN": "c", "LINEAR_API_KEY": "b", "SAFE": "1"})

    assert child == {"SAFE": "1"}


def test_child_env_does_not_mutate_what_it_was_given():
    base = {"GH_TOKEN": "x", "PATH": "/bin"}
    SecretStore({"GH_TOKEN": "x"}).child_env(base)

    assert base == {"GH_TOKEN": "x", "PATH": "/bin"}
