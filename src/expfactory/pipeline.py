"""
pipeline — the empirical lane's run stage (ticket 05).

run_and_record is the single entry the runner (ticket 07) calls per experiment:
train the seeds, verify through the plugin, append one immutable row. Promotion
stays derived inside the verifier; this function never sets it.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Sequence

from expfactory.verifier import Candidate, Ledger, Verifier, VerdictBundle


def run_and_record(
    train_fn: Callable[[dict[str, Any], int], dict[str, Any]],
    hypothesis: str,
    config: dict[str, Any],
    code_hash: str,
    seeds: Sequence[int],
    verifier: Verifier,
    ledger: Ledger,
    cost_usd: float = 0.0,
    parent_id: str | None = None,
    diff: Any = None,
) -> VerdictBundle:
    """Drive one experiment end to end and append its verdict to the ledger.

    train_fn(config, seed) -> RunResult-shaped dict. All seeds are run before
    verification so seed-variance and reproducibility gates have data to judge.
    `diff`, when supplied, drives the diff-level tamper gate.
    """
    runs: list[dict[str, Any]] = []
    for seed in seeds:
        t0 = time.time()
        r = dict(train_fn(config, seed))
        r["wall_seconds"] = time.time() - t0
        runs.append(r)

    candidate = Candidate(
        hypothesis=hypothesis,
        config=config,
        code_hash=code_hash,
        runs=runs,
        cost_usd=cost_usd,
        parent_id=parent_id,
        diff=diff,
    )
    bundle = verifier.run(candidate)
    ledger.append(bundle)
    return bundle
