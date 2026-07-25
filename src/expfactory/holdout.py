"""
holdout — a durable holdout query budget (ticket 03).

The prototype counted ledger rows in memory, so a restart reopened the lockbox.
This backs the budget with a file the runner owns. Every look at the true holdout
spends one unit; once spent, spent — even across a fresh process. The runner holds
this, never the agent, so an experiment cannot grant itself more holdout access.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class HoldoutExhausted(RuntimeError):
    """Raised when a holdout query is attempted with no budget remaining.
    Fail loud: silently allowing the query is exactly the leak this prevents."""


class HoldoutBudget:
    def __init__(self, path: str | Path, limit: int):
        self.path = Path(path)
        self.limit = limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._spent = self._load()

    def _load(self) -> int:
        if not self.path.exists():
            return 0
        try:
            return int(json.loads(self.path.read_text())["spent"])
        except (json.JSONDecodeError, KeyError, ValueError):
            # a corrupt budget file is treated as fully spent — fail safe, not open
            return self.limit

    def _persist(self) -> None:
        # atomic write: temp file + rename, so a crash mid-write cannot corrupt
        # the budget into an "open" state
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"spent": self._spent, "limit": self.limit}, f)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self._spent)

    def query(self) -> int:
        """Spend one holdout query. Returns remaining budget after the spend.
        Raises HoldoutExhausted if none is left."""
        if self.remaining <= 0:
            raise HoldoutExhausted(
                f"holdout budget exhausted ({self._spent}/{self.limit}) — "
                "freeze model selection and collect new data"
            )
        self._spent += 1
        self._persist()
        return self.remaining
