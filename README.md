# expfactory

A software factory for **empirical work** — ML experiments where CI cannot be the
verifier, because there are no tests to pass, only numbers that may or may not be
real.

> **Adopt infrastructure. Build verification.** No existing system has a ledger or
> anti-fooling gates, because they all verify code against tests.

## The problem

The deterministic factory (ticket → PR → CI green → merge) works because CI is
ground truth. In research hill-climbing there is no CI: an agent proposes a
change, a number moves, and *nothing in the loop knows whether the number is
real*. An autonomous agent optimising against a metric will find the cheapest
path to moving it — seed lottery, train/eval leakage, holdout burn, a quietly
deleted assertion, or reporting whichever metric happened to improve.

`expfactory` is the replacement for CI in that setting: an **append-only ledger**
plus **deterministic anti-fooling gates**, where promotion is a derived property
of recorded evidence and never something a caller can assert.

## Install and verify

```bash
pip install -e ".[dev]"

pytest                            # gate lane: deterministic, free, every commit
mypy                              # strict across the verification core
ruff check src tests
ruff format --check src tests     # CI checks both; a long line in a test has failed a build
python -m expfactory.selfcheck    # boundary test: known-answer fixtures
```

## Use

```python
from expfactory import Candidate, GateVerifier, Ledger

verifier = GateVerifier()
ledger = Ledger("runs/ledger.jsonl")

bundle = verifier.run(Candidate(
    hypothesis="wider fusion head",
    config={"width": 256},
    code_hash="a1b2c3",
    runs=[...],          # one record per seed
))

ledger.append(bundle)
print(bundle.promoted, bundle.blocked_by)
```

`bundle.promoted` is computed from the gates. There is no setter, the dataclass is
frozen, and both verifier implementations route through the same named
constructors — so a promotion cannot be forged, only earned.

## What counts as success

**A run in which every proposed improvement is correctly rejected is a passing
run.** The bar is "the gates behaved correctly," not "the model improved." The
proving workload (drone-vs-bird detection) is a vehicle for stressing the
factory, not a product.

## The gates

| Gate | Catches |
|---|---|
| `no_leakage` | train/eval index overlap |
| `reproducible` | same seed, different number |
| `seed_variance` | gains inside the noise band |
| `too_good_to_be_true` | implausible jumps (escalates, non-blocking) |
| `holdout_budget` | over-querying the lockbox |
| `cost` | per-experiment spend |
| `no_single_seed_dominance` | one lucky seed carrying the mean (baseline-free) |
| `no_test_tampering` | diffs that weaken verification itself |
| `G-07` preregistration | HARKing / metric-shopping (opt in with `require_prereg=True`) |
| `G-08` prereg churn | serial re-filing until one lands (opt in with `require_prereg=True`) |
| `G-09` group leakage | the same source in train and eval under different sample ids |
| `G-10` attestation | a candidate citing a compute job the registry never issued |

Every gate traces to a fixture in the adversarial suite. New gates are added only
when a fixture proves the set misses something.

## Verification layers

```
L0  deterministic gates   run FIRST, block, cheap, ratchet
L1  fresh-context review  runs only on L0-passing candidates, never overrides L0
L2  human                 judges the result, not the arithmetic
```

The ordering is load-bearing. An LLM reviewer cannot detect leakage (set
arithmetic on IDs), cannot enforce a budget across restarts (durable state), and
cannot ratchet. Worse, if L1 ran first it would *launder* a fake result by
attaching a persuasive endorsement to it.

## Documentation

| Document | Read when |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | The handbook: architecture, commands, standards, invariants. |
| [`AGENTS.md`](AGENTS.md) | Thin entry point for any coding agent; points at CLAUDE.md. |
| [`docs/SPEC.md`](docs/SPEC.md) | Before changing anything under `src/`. |
| [`docs/MAP.md`](docs/MAP.md) | Before proposing an architecture change. 12 closed decisions + an explicit rejections list. |
| [`docs/MAP2.md`](docs/MAP2.md) | Before starting new design work. Open territory. |
| [`docs/decisions/`](docs/decisions/) | Why a specific call was made. |
| [`docs/tickets/`](docs/tickets/) | Build slices, dependency-ordered. |
| [`docs/TRACKING.md`](docs/TRACKING.md) | Where work lives: Linear is the queue, GitHub holds code. No sync. |

## Status

**Built and green.** The loop is closed end to end: `ticket → runner → agent →
verifier → gates → ledger`, all ten gates, plus `JobRegistry` and the detach path
for jobs that outlive an agent session. `LocalGpuSubstrate` is the first real
`ComputeSubstrate` behind that seam.

**Not ready for a live agent.** An external review on 2026-07-28
([`docs/reference/code-review-insights-2026-07-28.html`](docs/reference/code-review-insights-2026-07-28.html))
found four paths that let an untrusted party bypass a stated control or leave an
expensive run unaccounted for. Two are P0. They are filed as BRE-28 through
BRE-32 and are tracked in
[`docs/DISPATCH-READINESS.md`](docs/DISPATCH-READINESS.md).

The review's verdict on the architecture was that it is sound and the gap is
implementation completeness at the same boundary. That distinction is the point:
the thesis is not in question, the door latches are.

### Where compute fits

The GPU is **one slice of one lane**, not the factory. Work arriving here splits
two ways, and conflating them is the failure mode the design is built against:

| Lane | Verifier | Needs a GPU? |
|---|---|---|
| Deterministic — web, infra, tooling | CI is ground truth | no |
| Empirical — ML research, hill-climbing | this repo's gates on recorded evidence | only when a job trains a model |

`ComputeSubstrate` is deliberately hardware-neutral (`submit`, `poll`,
`fetch_artifact`). Compute is pluggable: local GPU today, edge and infra later.
Nothing above that seam should name a card.

Provisioning in [`provision/`](provision/) is applied by hand, never by an agent.
`main` is protected: PR required, CI green on 3.11 and 3.13, and code-owner
review on the verification substrate. Every merge currently needs `--admin`,
because a solo owner cannot approve their own PR — that is BRE-18 happening
literally, not a workaround.
