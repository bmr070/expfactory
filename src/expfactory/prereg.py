"""
prereg — the preregistration record and the G-07 gate (M2-04, ticket N-02/N-03).

Closes the fooling mode every other gate is silent on: the agent runs an
experiment, sees the primary metric didn't move but latency improved, and reports
latency as the win. Nothing about that result is *fake* — only the claim is.
Named in the literature as HARKing and S-hacking.

The resolution (see docs/decisions/M2-04-RESOLVED-preregistration.md): preregister
the **decision rule**, not the hypothesis. Exploration stays free and unlimited;
what is fixed in advance is how a result will be judged.

The mechanism is an asymmetry:

    a metric may be allowed to PROMOTE, or allowed to BLOCK, but never both.

`primary_metric` can promote. `guardrails` can only block. `secondary_metrics` are
recorded and are never sufficient for anything. That is what makes "primary flat
but latency improved" unable to promote under any reading of the rule.

## What this does not do

Preregistration does not make metric-shopping impossible. An agent can file eight
preregistrations naming eight primary metrics and promote on the eighth. What it
does is make that **countable** — G-08 counts it.

It also assumes exploratory runs are actually recorded. An agent that runs
privately, sees a result, then files a preregistration and "confirms" it cannot be
caught by ordering alone. The fresh-seed rule below is the partial mitigation:
confirmatory seeds must be declared up front, so a rerun of a known-good seed set
is at least visible in the record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from expfactory.harness import Experiment, GateResult, RunResult

Direction = Literal["maximize", "minimize"]

# The metric name meaning "the RunResult.val_metric field itself" rather than a
# key inside RunResult.extra.
PRIMARY_FIELD = "val_metric"


@dataclass(frozen=True)
class Preregistration:
    """A decision rule, fixed before the confirmatory run executes.

    Content-hashed. The hash is what a Candidate cites and what the ledger
    records, so any edit produces a different record rather than mutating one.
    """

    primary_metric: str
    direction: Direction
    baseline_value: float
    minimum_effect: float
    seeds: tuple[int, ...]
    secondary_metrics: tuple[str, ...] = ()
    # name -> upper bound the metric must not exceed. Guardrails only ever block.
    guardrails: tuple[tuple[str, float], ...] = ()
    parent_id: str | None = None
    supersedes: str | None = None
    decision_rule: str = "mean_effect_meets_minimum"

    def __post_init__(self) -> None:
        if self.direction not in ("maximize", "minimize"):
            raise ValueError(f"direction must be maximize|minimize, got {self.direction!r}")
        if self.minimum_effect < 0:
            raise ValueError("minimum_effect is a magnitude and must be >= 0")
        if not self.seeds:
            raise ValueError("a confirmatory run must declare its seed set up front")
        overlap = set(self.secondary_metrics) & {g[0] for g in self.guardrails}
        if overlap:
            raise ValueError(f"metric cannot be both secondary and guardrail: {sorted(overlap)}")
        if self.primary_metric in {g[0] for g in self.guardrails}:
            # the asymmetry, enforced rather than documented: promote XOR block
            raise ValueError(
                f"{self.primary_metric!r} cannot be both the primary metric and a "
                "guardrail — a metric may promote or block, never both"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_metric": self.primary_metric,
            "direction": self.direction,
            "baseline_value": self.baseline_value,
            "minimum_effect": self.minimum_effect,
            "seeds": list(self.seeds),
            "secondary_metrics": list(self.secondary_metrics),
            "guardrails": [list(g) for g in self.guardrails],
            "parent_id": self.parent_id,
            "supersedes": self.supersedes,
            "decision_rule": self.decision_rule,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Preregistration:
        return cls(
            primary_metric=d["primary_metric"],
            direction=d["direction"],
            baseline_value=d["baseline_value"],
            minimum_effect=d["minimum_effect"],
            seeds=tuple(d["seeds"]),
            secondary_metrics=tuple(d.get("secondary_metrics", ())),
            guardrails=tuple((g[0], g[1]) for g in d.get("guardrails", ())),
            parent_id=d.get("parent_id"),
            supersedes=d.get("supersedes"),
            decision_rule=d.get("decision_rule", "mean_effect_meets_minimum"),
        )

    @property
    def hash(self) -> str:
        """Stable content hash.

        Must be identical across processes — a confirmatory run in one process
        cites a preregistration filed in another. Hence sorted-key JSON of plain
        types only: no `id()`, no object repr, no dict-ordering dependence.
        """
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def metric_value(run: RunResult, name: str) -> float | None:
    """Read a named metric off a run record, or None if it was not reported."""
    if name == PRIMARY_FIELD:
        return run.val_metric
    value = run.extra.get(name)
    return float(value) if isinstance(value, (int, float)) else None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


@dataclass
class PreregContext:
    """What the gate needs from outside the experiment itself."""

    prereg: Preregistration | None = None
    exploratory: bool = False
    # hashes of preregistrations already present in the ledger when this run was
    # verified. Membership is the anti-HARKing proof: the ledger is append-only
    # and the verdict row is written after verification, so anything already in
    # here necessarily precedes this run.
    filed_hashes: frozenset[str] = field(default_factory=frozenset)
    cited_hash: str | None = None
    # How many preregistrations exist in this candidate's lineage that never
    # produced a promotion, including the current attempt. Drives G-08.
    lineage_attempts: int = 0


def gate_preregistration(
    exp: Experiment, prereg_ctx: PreregContext | None = None, **_: Any
) -> GateResult:
    """G-07 — a promotion must satisfy a rule that was fixed before the run.

    Blocking. Seven rules, in the order that fails cheapest first.
    """
    name = "preregistration"
    ctx = prereg_ctx or PreregContext()

    # Rule 7 — exploratory runs are structurally unpromotable. Checked first so an
    # exploratory run needs no preregistration at all: exploration stays free.
    if ctx.exploratory:
        return GateResult(name, False, "EXPLORATORY: recorded, never promotable", blocking=True)

    # Rule 1 — a confirmatory run must cite a preregistration.
    if not ctx.cited_hash or ctx.prereg is None:
        return GateResult(
            name,
            False,
            "no preregistration cited — a confirmatory run must declare its "
            "decision rule before executing",
            blocking=True,
        )

    prereg = ctx.prereg

    # The cited hash must actually match the record supplied, or the citation is
    # decorative.
    if prereg.hash != ctx.cited_hash:
        return GateResult(
            name,
            False,
            f"cited prereg {ctx.cited_hash} does not match supplied record {prereg.hash}",
            blocking=True,
        )

    # Rule 2 — it must already be on the ledger. This is the anti-HARKing proof.
    if ctx.cited_hash not in ctx.filed_hashes:
        return GateResult(
            name,
            False,
            f"HARKING: prereg {ctx.cited_hash} is not in the ledger — it was not "
            "filed before this run",
            blocking=True,
        )

    # Rule 5 — the seed set must be exactly what was declared. Prevents running
    # twenty seeds and reporting the best five.
    observed_seeds = tuple(sorted(r.seed for r in exp.runs))
    declared_seeds = tuple(sorted(prereg.seeds))
    if observed_seeds != declared_seeds:
        return GateResult(
            name,
            False,
            f"SEED SHOPPING: ran {list(observed_seeds)}, declared {list(declared_seeds)}",
            blocking=True,
        )

    # Rule 3 — the primary metric must have actually been reported.
    primary = [metric_value(r, prereg.primary_metric) for r in exp.runs]
    if any(v is None for v in primary):
        return GateResult(
            name,
            False,
            f"declared primary metric {prereg.primary_metric!r} not reported by every run",
            blocking=True,
        )

    # Rule 6 — guardrails may only block. Checked before the primary so a
    # regression cannot be traded away against a headline gain.
    for gname, bound in prereg.guardrails:
        values = [metric_value(r, gname) for r in exp.runs]
        if any(v is None for v in values):
            return GateResult(
                name, False, f"guardrail {gname!r} declared but not reported", blocking=True
            )
        observed = _mean([v for v in values if v is not None])
        if observed > bound:
            return GateResult(
                name,
                False,
                f"GUARDRAIL BREACH: {gname}={observed:.4f} exceeds declared bound {bound:.4f}",
                blocking=True,
            )

    # Rule 4 — the effect must meet the declared minimum, in the declared
    # direction, against the declared baseline. All three were fixed in advance.
    observed = _mean([v for v in primary if v is not None])
    effect = (
        observed - prereg.baseline_value
        if prereg.direction == "maximize"
        else prereg.baseline_value - observed
    )
    if effect < prereg.minimum_effect:
        return GateResult(
            name,
            False,
            f"effect {effect:+.4f} on {prereg.primary_metric} does not meet the "
            f"declared minimum {prereg.minimum_effect:.4f} ({prereg.direction})",
            blocking=True,
        )

    detail = (
        f"prereg {ctx.cited_hash}: {prereg.primary_metric} effect {effect:+.4f} "
        f">= {prereg.minimum_effect:.4f} ({prereg.direction}), "
        f"{len(prereg.guardrails)} guardrail(s) held"
    )
    return GateResult(name, True, detail, blocking=True)


# --------------------------------------------------------------------------- #
# G-08 — preregistration churn (ticket N-04)
# --------------------------------------------------------------------------- #

# How many preregistrations may exist in one lineage, INCLUDING the current
# attempt, before the pattern is better explained as metric-shopping than as
# honest revision.
#
# Calibrated against the fixtures in tests/test_prereg_churn.py rather than
# picked: one prior revision is routine, two is plausible, a fourth attempt after
# three that never promoted is the S-hacking signature the literature describes.
# Re-calibrate against real lineages once there are any — a threshold tuned on
# synthetic fixtures is a starting point, not a finding.
DEFAULT_MAX_ATTEMPTS = 3


def gate_prereg_churn(
    exp: Experiment,
    prereg_ctx: PreregContext | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    **_: Any,
) -> GateResult:
    """G-08 — block serial re-filing until something lands.

    G-07 makes each individual preregistration honest: the rule was fixed before
    the run. It cannot see the pattern *across* preregistrations. An agent may
    file eight rules naming eight primary metrics and promote on the eighth, and
    every one of those eight passes G-07.

    This gate counts. A lineage accumulating preregistrations that never promote
    is metric-shopping regardless of how each one looked in isolation.

    Exploratory runs are exempt: exploration is supposed to be unlimited, and it
    cannot promote anyway.
    """
    name = "prereg_churn"
    ctx = prereg_ctx or PreregContext()

    if ctx.exploratory:
        return GateResult(name, True, "exploratory: churn not counted", blocking=True)

    attempts = ctx.lineage_attempts
    if attempts > max_attempts:
        return GateResult(
            name,
            False,
            f"S-HACKING: {attempts} preregistrations filed in this lineage with no "
            f"promotion (limit {max_attempts}) — escalate; the search is shopping "
            "for a metric, not testing one",
            blocking=True,
        )
    return GateResult(
        name, True, f"{attempts}/{max_attempts} preregistration attempts in lineage", blocking=True
    )
