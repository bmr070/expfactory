"""BRE-38 — the runner builds the session, so the runner decides what it reaches.

Ticket 07's last unmet box. `NEXT.md` stated it exactly:

> **It is not yet the source of tracker credentials**, because the runner does
> not construct trackers — they arrive already built, with a transport the caller
> wired. Closing that box means inverting who builds what, which is a real change
> and not a wiring detail.

Invariant 6 says the agent never holds tracker or GPU credentials. Before this,
that held only because every caller had been careful: the runner received an
`AgentSession` somebody else configured and had no way to enforce anything about
it. A factory closes the gap — the runner holds the factory, the factory holds
the `SecretStore`, and a session comes into existence only after its environment
has been scrubbed.

The shape is TRL's OpenEnv `opencode` example, which constructs its sessions with
the verifier and sandbox backend already bound so the session cannot choose
either. Their reason is ours: the thing being scored must not be able to reach
the thing scoring it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from expfactory.runner import (
    AgentSessionFactory,
    FixedSessionFactory,
    SandboxedSessionFactory,
    Ticket,
)
from expfactory.sandbox import SecretStore


class RecordingSession:
    """Stands in for an agent session. Records the environment it was built with."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self.env = env or {}
        self.ran: list[str] = []

    def run(self, ticket: Ticket, workspace: Path | None = None) -> Any:
        self.ran.append(ticket.id)
        raise RuntimeError("not exercised here; the runner path has its own tests")


def _ticket(tid: str = "BRE-1") -> Ticket:
    return Ticket(id=tid, title="t", body="b", labels=frozenset())


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_both_factories_satisfy_the_protocol_structurally() -> None:
    assert isinstance(FixedSessionFactory(RecordingSession()), AgentSessionFactory)
    assert isinstance(
        SandboxedSessionFactory(
            lambda t, w, env: RecordingSession(dict(env)),
            secrets=SecretStore({}),
            base_env={},
        ),
        AgentSessionFactory,
    )


def test_a_bare_session_is_not_mistaken_for_a_factory() -> None:
    """`isinstance` against the Protocol rather than `hasattr("create")`.

    A session that happens to grow a `create` method for its own reasons must not
    silently change what the runner does with it.
    """
    assert not isinstance(RecordingSession(), AgentSessionFactory)


def test_the_fixed_factory_returns_the_session_it_was_given() -> None:
    """The migration path. Grants no isolation, and is named so the call site
    says so."""
    session = RecordingSession()
    assert FixedSessionFactory(session).create(_ticket()) is session


def test_a_new_session_is_built_for_each_ticket() -> None:
    """One session per ticket, not one reused across them.

    A session carried between tickets carries whatever it accumulated — a
    workspace path, a cached credential, an open handle — into work that did not
    ask for it.
    """
    built: list[str] = []

    def build(ticket: Ticket, workspace: Path | None, env: Any) -> RecordingSession:
        built.append(ticket.id)
        return RecordingSession(dict(env))

    factory = SandboxedSessionFactory(build, secrets=SecretStore({}), base_env={})
    first = factory.create(_ticket("BRE-1"))
    second = factory.create(_ticket("BRE-2"))

    assert built == ["BRE-1", "BRE-2"]
    assert first is not second


# ---------------------------------------------------------------------------
# What the factory actually enforces
# ---------------------------------------------------------------------------


def test_declared_secrets_are_stripped_from_the_environment_the_session_gets() -> None:
    """Invariant 6, enforced by construction rather than by the caller's care."""
    secrets = SecretStore({"LINEAR_API_KEY": "lin_abc", "GH_TOKEN": "ghp_xyz"})
    base = {"PATH": "/usr/bin", "LINEAR_API_KEY": "lin_abc", "GH_TOKEN": "ghp_xyz", "HOME": "/h"}

    factory = SandboxedSessionFactory(
        lambda t, w, env: RecordingSession(dict(env)), secrets=secrets, base_env=base
    )
    session = factory.create(_ticket())

    assert "LINEAR_API_KEY" not in session.env
    assert "GH_TOKEN" not in session.env
    assert session.env["PATH"] == "/usr/bin", "unrelated environment must survive"


def test_every_declared_name_is_stripped_not_only_the_ones_in_use() -> None:
    """SPEC §15.3's normative MUST, and the reason it is worded that way.

    A secret nobody remembered to use is precisely the one that leaks: it is in
    the environment, nothing references it, so nothing prompts anyone to remove
    it. `child_env` strips the whole declared set.
    """
    secrets = SecretStore({"GH_TOKEN": "a", "LINEAR_API_KEY": "b", "HF_TOKEN": "c"})
    base = {"HF_TOKEN": "c", "LINEAR_API_KEY": "b", "SAFE": "1"}

    factory = SandboxedSessionFactory(
        lambda t, w, env: RecordingSession(dict(env)), secrets=secrets, base_env=base
    )
    env = factory.create(_ticket()).env

    assert set(env) == {"SAFE"}


def test_the_session_never_receives_the_secret_store() -> None:
    """The store is the factory's, and `build` is handed an environment rather
    than the thing that produced it.

    Passing the store through would make the scrubbing advisory: anything holding
    it can read `use()` and put a secret straight back.
    """
    seen: dict[str, Any] = {}

    def build(ticket: Ticket, workspace: Path | None, env: Any) -> RecordingSession:
        seen["args"] = (ticket, workspace, env)
        return RecordingSession(dict(env))

    factory = SandboxedSessionFactory(
        build, secrets=SecretStore({"GH_TOKEN": "x"}), base_env={"GH_TOKEN": "x", "A": "1"}
    )
    factory.create(_ticket(), Path("/tmp/ws"))

    assert not any(isinstance(arg, SecretStore) for arg in seen["args"])


def test_the_workspace_reaches_the_builder() -> None:
    """The factory decides the environment; it must not swallow the workspace the
    runner prepared."""
    seen: dict[str, Any] = {}

    def build(ticket: Ticket, workspace: Path | None, env: Any) -> RecordingSession:
        seen["ws"] = workspace
        return RecordingSession(dict(env))

    SandboxedSessionFactory(build, secrets=SecretStore({}), base_env={}).create(
        _ticket(), Path("/tmp/ws-1")
    )
    assert seen["ws"] == Path("/tmp/ws-1")


def test_the_base_environment_is_snapshotted_not_aliased() -> None:
    """A later mutation of the caller's dict must not reintroduce a secret into
    sessions built afterwards."""
    base = {"A": "1"}
    factory = SandboxedSessionFactory(
        lambda t, w, env: RecordingSession(dict(env)), secrets=SecretStore({}), base_env=base
    )
    base["SNEAKED_IN"] = "later"

    assert "SNEAKED_IN" not in factory.create(_ticket()).env


@pytest.mark.parametrize("workspace", [None, Path("/tmp/w")])
def test_create_accepts_the_same_shape_the_runner_calls_it_with(workspace: Path | None) -> None:
    factory = SandboxedSessionFactory(
        lambda t, w, env: RecordingSession(dict(env)), secrets=SecretStore({}), base_env={}
    )
    assert factory.create(_ticket(), workspace) is not None
