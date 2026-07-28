"""
sandbox — the execution environment the runner hands an agent (ticket 07).

Two of ticket 07's acceptance boxes were unmet, and `NEXT.md` recorded both:

  - *"prepares an isolated workspace"* — `Runner._dispatch` did none. It handed
    the ticket to an `AgentSession` and trusted it to sort itself out.
  - *"tracker credentials live in the runner's secret store"* — no store existed;
    the token rode on a caller-supplied transport.

They are one concern from the runner's side — *where the agent runs, and what it
can see* — so they live together.

## Names are refused, not sanitized

Symphony `SPEC.md` §15.2 requires sanitized directory names and a path that stays
under the workspace root. Sanitizing is the obvious reading and it is the wrong
one: every sanitizer is lossy, and a lossy mapping means two different tickets
can land in one directory. `BRE-1/../BRE-2` and `BRE-2` must not become the same
workspace, and the way to guarantee that is to refuse the first rather than to
scrub it into the second.

So `prepare` accepts a conservative identifier and rejects everything else, then
checks containment anyway. The second check is redundant if the first is correct,
which is the point — it is the one that still holds if the first has a bug.

Windows gets specific attention because this runs on it: reserved device names
(`CON`, `NUL`, `COM1`…) are not usable as directories, trailing dots and spaces
are silently stripped by the filesystem so `BRE-1.` and `BRE-1` collide, and path
comparison is case-insensitive so `bre-1` and `BRE-1` are one directory.

## Secrets are declared so a child can be stripped of them

§15.3 is normative and we already satisfy the first half — the trackers take an
injected transport and never see a token. The half we did not satisfy:

> Adapters MUST declare secret environment names so local and remote launchers
> can remove them from child environments.

`SecretStore.child_env` is that. It is the deny half of invariant 6: the agent
never *holds* a credential, and now the environment it is launched into cannot
*leak* one either — including secrets it never asked for, since the whole
declared set is removed rather than just the ones this run happens to use.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Conservative on purpose. Real ticket ids are `BRE-1`, `GH-46`, `N-07`; anything
# outside this is likelier to be an attack or a bug than a ticket.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Reserved on Windows with or without an extension. Creating one of these fails
# or does something surprising, and the failure would look like a runner bug.
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class WorkspaceRefused(ValueError):
    """The identifier cannot be turned into a safe directory name."""


@dataclass(frozen=True)
class Workspace:
    """One ticket's private directory."""

    ticket: str
    path: Path


class WorkspaceRoot:
    """Per-ticket directories under one root, and nothing outside it.

    Filesystem isolation only — the same primitive Symphony documents, and its
    caveat applies verbatim: *"that is the minimal isolation primitive, not a
    security boundary."* A process that wants to leave its workspace can. What
    this buys is that two concurrent tickets cannot silently write over each
    other, and that a path built from a ticket id cannot escape.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _safe_dirname(self, ticket_id: str) -> str:
        # Deliberately NOT stripped. The first version of this called
        # `.strip()` here, which is a sanitizer, and it silently mapped
        # "BRE-1 " onto "BRE-1" — two tickets, one directory, which is the exact
        # failure the module docstring argues against. A test caught it. Leading
        # or trailing whitespace is refused like anything else outside the safe
        # set, rather than quietly removed.
        name = ticket_id
        if not _SAFE_NAME.match(name):
            raise WorkspaceRefused(
                f"{ticket_id!r} is not a usable workspace name. Allowed: letters, "
                "digits, dot, dash, underscore, starting alphanumeric, max 64. "
                "Refused rather than sanitized — every sanitizer is lossy, and a "
                "lossy mapping lets two tickets share one directory."
            )
        if name.rstrip(". ") != name:
            # Windows strips these, so `BRE-1.` and `BRE-1` become one directory.
            raise WorkspaceRefused(f"{ticket_id!r} ends in a dot or space, which collides")
        if name.split(".")[0].upper() in _WINDOWS_RESERVED:
            raise WorkspaceRefused(f"{ticket_id!r} is a reserved device name on Windows")
        return name

    def prepare(self, ticket_id: str) -> Workspace:
        """A fresh, empty directory for this ticket.

        Emptied if it already exists. A workspace carrying the previous attempt's
        files is how one run's output becomes another run's input, which would
        make a result depend on what happened to be on disk.
        """
        path = self._root / self._safe_dirname(ticket_id)

        resolved = path.resolve()
        # Redundant if `_safe_dirname` is correct. Kept because it is the check
        # that survives a bug in the other one, and because it costs nothing.
        if resolved != self._root and self._root not in resolved.parents:
            raise WorkspaceRefused(f"{resolved} would escape the workspace root {self._root}")
        if resolved == self._root:
            raise WorkspaceRefused("a workspace cannot be the root itself")

        if resolved.exists():
            shutil.rmtree(resolved)
        resolved.mkdir(parents=True)
        return Workspace(ticket=ticket_id, path=resolved)

    def discard(self, ticket_id: str) -> None:
        """Remove a workspace. Silent if it was never created."""
        path = self._root / self._safe_dirname(ticket_id)
        if path.exists():
            shutil.rmtree(path)

    def existing(self) -> tuple[str, ...]:
        """Workspaces on disk, for a caller reconciling after a restart."""
        if not self._root.exists():
            return ()
        return tuple(sorted(p.name for p in self._root.iterdir() if p.is_dir()))


class SecretStore:
    """Holds credentials, and declares their names so a child can be stripped.

    There is no bulk accessor and no `__repr__` that renders values, for the same
    reason `LabelStore` has neither: a store that can be dumped is one that will
    be dumped, into a log or a traceback, and the party reading it is whoever
    gets the crash report.

    `use()` returns one named value to a caller that already knows which secret
    it needs. That is not a security control — anything in this process can reach
    the dict — but it makes an accidental read obviously deliberate, which is the
    honest limit of what a language can offer here.
    """

    def __init__(self, secrets: Mapping[str, str]) -> None:
        for name, value in secrets.items():
            if not name:
                raise ValueError("a secret with no name cannot be scrubbed from an environment")
            if not value:
                raise ValueError(f"secret {name!r} is empty; an empty credential fails obscurely")
        self.__secrets = dict(secrets)

    def names(self) -> tuple[str, ...]:
        """The declared environment names. §15.3's MUST, and the only thing about
        the secrets that is safe to publish."""
        return tuple(sorted(self.__secrets))

    def use(self, name: str) -> str:
        """One value, for a caller that names it. Raises rather than returning
        None — a silently-absent credential produces an authentication error
        somewhere far away from the missing configuration."""
        try:
            return self.__secrets[name]
        except KeyError:
            raise KeyError(f"no secret named {name!r}; declared: {list(self.names())}") from None

    def child_env(self, base: Mapping[str, str]) -> dict[str, str]:
        """`base` with every declared secret removed.

        Removes the whole declared set, not just the ones this run uses. A child
        that never asked for a credential should not be able to read one, and
        deciding per-run which to strip means the decision is made by whoever
        wrote the run.
        """
        secret = set(self.__secrets)
        return {k: v for k, v in base.items() if k not in secret}

    def __len__(self) -> int:
        return len(self.__secrets)

    def __repr__(self) -> str:
        # Names, never values. A repr lands in logs and tracebacks.
        return f"SecretStore({list(self.names())})"

    __str__ = __repr__


__all__ = [
    "SecretStore",
    "Workspace",
    "WorkspaceRefused",
    "WorkspaceRoot",
]
