"""
Ticket 03, remaining criterion: the holdout budget must persist across process
restart and be enforced by the runner, not the agent.

The prototype's gate counted ledger rows in memory — a restart reset the count,
so the lockbox could be reopened indefinitely by bouncing the process. This makes
the budget durable: backed by a file the runner owns, decremented atomically,
and refusing further holdout queries once exhausted even across a fresh process.

Seam under test: HoldoutBudget(path, limit) — .query() / .remaining / reload.
Written RED: HoldoutBudget does not exist.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from expfactory.holdout import HoldoutBudget, HoldoutExhausted


def test_budget_starts_full(tmp_path: Path):
    b = HoldoutBudget(tmp_path / "budget.json", limit=10)
    assert b.remaining == 10


def test_each_query_decrements(tmp_path: Path):
    b = HoldoutBudget(tmp_path / "budget.json", limit=3)
    b.query(); b.query()
    assert b.remaining == 1


def test_exhaustion_raises_not_silently_allows(tmp_path: Path):
    b = HoldoutBudget(tmp_path / "budget.json", limit=2)
    b.query(); b.query()
    with pytest.raises(HoldoutExhausted):
        b.query()


def test_budget_survives_restart(tmp_path: Path):
    """The property the prototype lacked: a fresh object at the same path sees the
    spent budget. Bouncing the process does NOT reopen the lockbox."""
    p = tmp_path / "budget.json"
    b1 = HoldoutBudget(p, limit=5)
    b1.query(); b1.query(); b1.query()
    # simulate a restart: brand new object, same file
    b2 = HoldoutBudget(p, limit=5)
    assert b2.remaining == 2


def test_exhaustion_persists_across_restart(tmp_path: Path):
    p = tmp_path / "budget.json"
    b1 = HoldoutBudget(p, limit=1)
    b1.query()
    b2 = HoldoutBudget(p, limit=1)
    with pytest.raises(HoldoutExhausted):
        b2.query()


def test_limit_mismatch_on_reload_keeps_spent_count(tmp_path: Path):
    """If the runner restarts with a different configured limit, the amount already
    spent is still honored — you cannot regain queries by raising the limit and
    you cannot lose the spent count by lowering it."""
    p = tmp_path / "budget.json"
    b1 = HoldoutBudget(p, limit=10)
    for _ in range(7):
        b1.query()
    b2 = HoldoutBudget(p, limit=10)
    assert b2.remaining == 3
