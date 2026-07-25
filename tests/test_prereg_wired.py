"""
N-01 + N-02: the ledger holds preregistrations, and G-07 is wired into the
verifier (docs/tickets/NEXT.md).

The G-07 unit fixtures live in test_prereg_gate.py. These tests cover the part
that only exists once the pieces are connected: that filing a preregistration and
citing it actually changes the verdict, and that ordering in the append-only log
is what makes the anti-HARKing rule true rather than merely asserted.
"""

from __future__ import annotations

from pathlib import Path

from expfactory.prereg import Preregistration
from expfactory.verifier import Candidate, GateVerifier, Ledger, VerdictBundle

PARENT = "exp-parent"


def _prereg(**over: object) -> Preregistration:
    base: dict[str, object] = dict(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=0.70,
        minimum_effect=0.02,
        seeds=(0, 1, 2),
        parent_id=PARENT,
    )
    base.update(over)
    return Preregistration(**base)  # type: ignore[arg-type]


def _candidate(metric: float = 0.75, **over: object) -> Candidate:
    base: dict[str, object] = dict(
        hypothesis="wider fusion head",
        config={"width": 256},
        code_hash="abc123",
        runs=[
            dict(
                seed=s,
                val_metric=metric,
                train_ids_hash="t",
                eval_ids_hash="e",
                overlap_count=0,
                wall_seconds=0.0,
            )
            for s in range(3)
        ],
        cost_usd=0.4,
    )
    base.update(over)
    return Candidate(**base)  # type: ignore[arg-type]


def _seed_parent(led: Ledger, metric: float = 0.70) -> None:
    """Record the parent result the preregistration will cite.

    Rule 8 reads the baseline from the ledger rather than from the prereg, so a
    confirmatory run needs a real recorded ancestor. Every promote-case below has
    to establish one — which is the point: before this rule, they were promoting
    against a baseline with no provenance at all.
    """
    led.append(GateVerifier(id_factory=lambda: PARENT).run(_candidate(metric=metric)))


# ---- N-01: one log, two row kinds, ordered ---------------------------------


def test_ledger_keeps_both_kinds_in_one_ordered_log(tmp_path: Path):
    """Positions must be comparable across kinds — that is the whole reason they
    share a file rather than living in two."""
    led = Ledger(tmp_path / "l.jsonl")
    p = _prereg()
    led.append_prereg(p)
    led.append(GateVerifier(id_factory=lambda: "e1").run(_candidate()))
    led.append_prereg(_prereg(minimum_effect=0.05))

    kinds = [r.kind for r in led.rows()]
    assert kinds == ["prereg", "verdict", "verdict"] or kinds == ["prereg", "verdict", "prereg"]
    assert [r.position for r in led.rows()] == [0, 1, 2]


def test_all_returns_verdicts_only(tmp_path: Path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append_prereg(_prereg())
    led.append(GateVerifier(id_factory=lambda: "e1").run(_candidate()))
    assert [b.exp_id for b in led.all()] == ["e1"]
    assert len(led.preregs()) == 1


def test_prereg_survives_a_round_trip_through_the_ledger(tmp_path: Path):
    led = Ledger(tmp_path / "l.jsonl")
    # note: a metric may not hold two roles, so the secondary and the guardrail
    # have to be different metrics — the record enforces that at construction.
    p = _prereg(secondary_metrics=("recall",), guardrails=(("latency_ms", 20.0),))
    led.append_prereg(p)
    restored = Ledger(tmp_path / "l.jsonl").get_prereg(p.hash)
    assert restored == p
    assert restored is not None and restored.hash == p.hash


def test_ordering_survives_a_fresh_ledger_object(tmp_path: Path):
    """A restart must not reorder history — G-07's proof depends on it."""
    path = tmp_path / "l.jsonl"
    Ledger(path).append_prereg(_prereg())
    Ledger(path).append(GateVerifier(id_factory=lambda: "e1").run(_candidate()))
    Ledger(path).append_prereg(_prereg(minimum_effect=0.09))
    rows = Ledger(path).rows()
    assert [r.kind for r in rows] == ["prereg", "verdict", "prereg"]
    assert [r.position for r in rows] == [0, 1, 2]


def test_unwrapped_legacy_verdict_row_still_loads(tmp_path: Path):
    """Ledgers written before preregistration existed must still read."""
    path = tmp_path / "l.jsonl"
    bundle = GateVerifier(id_factory=lambda: "old").run(_candidate())
    path.write_text(bundle.to_json() + "\n")
    rows = Ledger(path).rows()
    assert len(rows) == 1
    assert rows[0].kind == "verdict"
    assert isinstance(rows[0].payload, VerdictBundle)
    assert rows[0].payload.exp_id == "old"


# ---- N-02 + wiring: filing changes the verdict -----------------------------


def test_confirmatory_run_promotes_when_the_prereg_was_filed_first(tmp_path: Path):
    led = Ledger(tmp_path / "l.jsonl")
    _seed_parent(led)
    p = _prereg()
    led.append_prereg(p)  # filed BEFORE the run

    v = GateVerifier(require_prereg=True, prereg_store=led, id_factory=lambda: "e1")
    bundle = v.run(_candidate(prereg_hash=p.hash, parent_id=PARENT))
    assert bundle.promoted, bundle.blocked_by
    assert "preregistration" in bundle.gate_names


def test_same_run_is_blocked_when_the_prereg_was_never_filed(tmp_path: Path):
    """Identical candidate, identical numbers. The only difference is that nobody
    filed the rule beforehand — and that alone must flip the verdict."""
    led = Ledger(tmp_path / "l.jsonl")
    _seed_parent(led)
    p = _prereg()  # constructed but NOT appended

    v = GateVerifier(require_prereg=True, prereg_store=led, id_factory=lambda: "e1")
    bundle = v.run(_candidate(prereg_hash=p.hash, parent_id=PARENT))
    assert not bundle.promoted
    assert "preregistration" in bundle.blocked_by


def test_exploratory_run_cannot_promote_however_good_the_number(tmp_path: Path):
    led = Ledger(tmp_path / "l.jsonl")
    v = GateVerifier(require_prereg=True, prereg_store=led, id_factory=lambda: "e1")
    bundle = v.run(_candidate(metric=0.99, exploratory=True))
    assert not bundle.promoted
    assert "preregistration" in bundle.blocked_by


def test_metric_swap_is_blocked_end_to_end(tmp_path: Path):
    """The headline fooling mode, through the real verifier: primary flat against
    the declared baseline, so no amount of secondary improvement promotes it."""
    led = Ledger(tmp_path / "l.jsonl")
    _seed_parent(led)
    p = _prereg(secondary_metrics=("latency_ms",))
    led.append_prereg(p)

    v = GateVerifier(require_prereg=True, prereg_store=led, id_factory=lambda: "e1")
    bundle = v.run(_candidate(metric=0.70, prereg_hash=p.hash, parent_id=PARENT))
    assert not bundle.promoted
    assert "preregistration" in bundle.blocked_by


def test_gate_is_absent_unless_required(tmp_path: Path):
    """Default configuration is unchanged: the same gate set still adjudicates
    one-off candidates that have no hill-climb lineage."""
    bundle = GateVerifier(id_factory=lambda: "e1").run(_candidate())
    assert "preregistration" not in bundle.gate_names
    assert bundle.promoted


def test_requiring_prereg_with_no_store_fails_closed(tmp_path: Path):
    """Misconfiguration must not be a free pass."""
    bundle = GateVerifier(require_prereg=True, id_factory=lambda: "e1").run(
        _candidate(prereg_hash="whatever")
    )
    assert not bundle.promoted
    assert "preregistration" in bundle.blocked_by


def test_ledger_satisfies_the_prereg_store_protocol(tmp_path: Path):
    """The verifier depends on the narrow PreregStore contract, not on Ledger
    itself, so a runner backed by something else can substitute."""
    from expfactory.verifier import PreregStore

    assert isinstance(Ledger(tmp_path / "l.jsonl"), PreregStore)


def test_baseline_is_read_from_the_ledger_not_the_preregistration(tmp_path: Path):
    """The forgery, end to end. The parent really scored 0.70; the prereg claims
    0.0 so that a 0.05 result clears minimum_effect. Must not promote."""
    led = Ledger(tmp_path / "l.jsonl")
    _seed_parent(led, metric=0.70)
    forged = _prereg(baseline_value=0.0)
    led.append_prereg(forged)

    v = GateVerifier(require_prereg=True, prereg_store=led, id_factory=lambda: "e1")
    bundle = v.run(_candidate(metric=0.05, prereg_hash=forged.hash, parent_id=PARENT))
    assert not bundle.promoted
    assert "preregistration" in bundle.blocked_by
