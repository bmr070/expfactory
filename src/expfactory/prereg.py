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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from expfactory.harness import Experiment, GateResult, RunResult

Direction = Literal["maximize", "minimize"]

# The metric name meaning "the RunResult.val_metric field itself" rather than a
# key inside RunResult.extra.
PRIMARY_FIELD = "val_metric"

# How far a declared baseline may sit from the parent's recorded metric before it
# is treated as forged. Tight on purpose: this is an equality check with room for
# float round-trips through JSON, not a tolerance band.
BASELINE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Guardrail:
    """A metric that may only ever BLOCK a promotion, never earn one.

    The asymmetry is the mechanism (M2-04): a metric can promote or block, never
    both. Guardrails are the blocking half.

    `direction` names which way is *better*, so "recall must not drop" and
    "latency must not rise" are both expressible. An earlier form took a fixed
    upper bound with lower-is-better assumed, which could not express the first
    case at all and would have fired on every improvement.

    The threshold is NOT declared here — it is the parent's recorded value, read
    from the ledger. A guardrail whose threshold the agent names is decorative
    for the same reason a declared baseline was (rule 8).

    `tolerance` is the regression that is acceptable anyway, stated in advance.
    """

    metric: str
    direction: Direction
    tolerance: float = 0.0

    def __post_init__(self) -> None:
        if self.direction not in ("maximize", "minimize"):
            raise ValueError(
                f"guardrail {self.metric!r}: direction must be maximize|minimize, "
                f"got {self.direction!r}"
            )
        if self.tolerance < 0:
            raise ValueError("tolerance is a magnitude and must be >= 0")

    def regressed(self, observed: float, parent: float) -> bool:
        if self.direction == "maximize":
            return observed < parent - self.tolerance
        return observed > parent + self.tolerance


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
    # Metrics that may only block, never promote. See Guardrail.
    guardrails: tuple[Guardrail, ...] = ()
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
        overlap = set(self.secondary_metrics) & {g.metric for g in self.guardrails}
        if overlap:
            raise ValueError(f"metric cannot be both secondary and guardrail: {sorted(overlap)}")
        if self.primary_metric in {g.metric for g in self.guardrails}:
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
            "guardrails": [[g.metric, g.direction, g.tolerance] for g in self.guardrails],
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
            guardrails=tuple(Guardrail(g[0], g[1], g[2]) for g in d.get("guardrails", ())),
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
    # The parent experiment's RECORDED metric, read from the ledger — never from
    # the preregistration. See the baseline rule in gate_preregistration.
    # Every metric the parent recorded, keyed by name. Rule 8 and rule 6 both
    # read from here. It is deliberately NOT a single number: an earlier version
    # carried only the parent's mean val_metric, so a preregistration naming an
    # `extra` key as primary had its baseline validated against a different
    # metric than the one its effect was measured on — which reopened the
    # forgery hole for every non-default primary metric.
    parent_metrics: Mapping[str, float] = field(default_factory=dict)
    # Ledger positions. `verdict_position` is None on a first verification, which
    # is the normal case; it is set only when a verdict for this experiment was
    # already recorded, which is when ordering has to be checked rather than
    # inferred from append order.
    prereg_position: int | None = None
    verdict_position: int | None = None


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

    # Rule 2 — the preregistration must precede the run. Anti-HARKing.
    #
    # Membership alone only asks "is it filed *now*", which is sound while a
    # verdict is appended straight after verification. It is not sound for a
    # verdict already on the ledger: that can be re-verified against a rule filed
    # afterwards. So when a verdict exists, compare positions.
    if ctx.cited_hash not in ctx.filed_hashes:
        return GateResult(
            name,
            False,
            f"HARKING: prereg {ctx.cited_hash} is not in the ledger — it was not "
            "filed before this run",
            blocking=True,
        )
    if (
        ctx.verdict_position is not None
        and ctx.prereg_position is not None
        and ctx.prereg_position >= ctx.verdict_position
    ):
        return GateResult(
            name,
            False,
            f"HARKING: prereg was filed at ledger position {ctx.prereg_position}, "
            f"after this experiment's verdict at {ctx.verdict_position} — the rule "
            "post-dates the result it claims to have predicted",
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
    for guard in prereg.guardrails:
        values = [metric_value(r, guard.metric) for r in exp.runs]
        if any(v is None for v in values):
            return GateResult(
                name, False, f"guardrail {guard.metric!r} declared but not reported", blocking=True
            )
        parent_value = ctx.parent_metrics.get(guard.metric)
        if parent_value is None:
            # No parent value means no regression can be computed. Fail closed:
            # an unmeasurable guardrail must not read as a satisfied one.
            return GateResult(
                name,
                False,
                f"guardrail {guard.metric!r} has no recorded value on parent "
                f"{prereg.parent_id} — regression cannot be measured",
                blocking=True,
            )
        observed = _mean([v for v in values if v is not None])
        if guard.regressed(observed, parent_value):
            slack = f" (tolerance {guard.tolerance:g})" if guard.tolerance else ""
            return GateResult(
                name,
                False,
                f"GUARDRAIL REGRESSION: {guard.metric} moved {parent_value:.4f} -> "
                f"{observed:.4f}, the wrong way for {guard.direction}{slack}",
                blocking=True,
            )

    # Rule 8 — the declared baseline must match what the parent actually scored.
    #
    # Without this the gate is theatre. The agent writes its own preregistration,
    # so it can declare baseline_value=0.0 and any result clears minimum_effect.
    # `promoted` being underivable by the caller buys nothing if the caller picks
    # the number it is compared against. The baseline is therefore read from the
    # ledger, and the declaration only has to *agree* with it.
    #
    # A confirmatory run must descend from a recorded parent. You cannot claim an
    # improvement over nothing: the first run of a lineage is exploratory by
    # construction, and it is what establishes the baseline the next run cites.
    if prereg.parent_id is None:
        return GateResult(
            name,
            False,
            "confirmatory run has no parent: a baseline nobody recorded cannot be "
            "checked, so the claim is unverifiable. Run it as exploratory first.",
            blocking=True,
        )
    parent_baseline = ctx.parent_metrics.get(prereg.primary_metric)
    if parent_baseline is None:
        return GateResult(
            name,
            False,
            f"parent {prereg.parent_id} recorded no {prereg.primary_metric!r} — "
            "nothing to measure the declared baseline against",
            blocking=True,
        )
    if abs(parent_baseline - prereg.baseline_value) > BASELINE_TOLERANCE:
        return GateResult(
            name,
            False,
            f"FORGED BASELINE: declared {prereg.baseline_value:.4f} for "
            f"{prereg.primary_metric!r} but parent {prereg.parent_id} actually "
            f"scored {parent_baseline:.4f}",
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
