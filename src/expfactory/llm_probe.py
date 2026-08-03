"""
llm_probe — a local model tries to get a bad candidate past the gates.

`gate_probe` sweeps properties I can state. `adversarial_suite` holds attacks I
thought of. Both are bounded by my imagination, and the direction they are
weakest in is **false accepts**: to encode an attack I have to first conceive of
it.

So this hands the job to something that does not share my priors. A local model
generates candidates and tries to slip them past `GateVerifier`. It is a fuzzer
with a language model as the mutation function.

## The property that makes this reportable

**A finding requires trusted code to confirm the flaw independently.** The model
proposes; it never adjudicates, and its claim about what it injected is used only
to steer generation and to label output. A finding is:

    a flaw this module verified is present
    AND   the gate that should catch it actually ran
    AND   the verdict promoted anyway

The first version had only the first and third clauses, and the first live run
produced a false accept within six attempts: `exploratory` is checked by G-07,
G-07 runs only when the verifier is built with `require_prereg=True`, and the
default verifier here was not. It reported the silence of a gate nobody armed.

That middle clause is the fix, and the episode is the argument for the whole
design — the flaw half was genuinely airtight, the verdict half was not, and no
amount of reasoning about it substituted for running it. `unarmed_gates` now
reports which detectors are aimed at gates this verifier does not run, because a
clean report is only as broad as the gates that were armed.

Getting this wrong in the loud direction matters more than throughput: the first
version of `gate_probe`'s can-fail sweep reported six false alarms against a
healthy gate set, and six false alarms is worse than none because it teaches a
reader to skim.

The cost is coverage. Only flaws expressible as a check on `Candidate` are
detectable, so `_DETECTORS` holds three, all unambiguous — a gate set that
promotes any of them is wrong, with no judgement call. Anything subtler the model
invents passes silently as a dud. That is the right trade for a tool whose output
is meant to be believed.

## Why a small local model is the right tool here

Factory's BYOK guidance puts sub-30B models below the bar for agentic coding, and
they are right — but this is not agentic coding. It is narrow structured
generation scored by a judge that does not care how clever the attacker was. A 7B
model producing mostly-garbage candidates at zero marginal cost is a *good*
fuzzer. Volume substitutes for insight when the oracle is exact.

## Trust

The model is untrusted input, exactly like a tracker ticket. Its output is parsed
as data and never executed, `eval`'d, or used to build a verifier. The only host
it may be reached at is loopback — `_check_endpoint` refuses anything else, so a
config mistake cannot turn this into an egress path.

Not wired into CI: it needs a model server, and a non-deterministic check that
fails on someone's laptop for want of Ollama is how a suite gets ignored. Run it
deliberately:

    python -m expfactory.llm_probe --attempts 40
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from expfactory.gates_v1 import DatasetGrouping
from expfactory.harness import RunResult
from expfactory.verifier import Candidate, GateVerifier, VerdictBundle

DEFAULT_ENDPOINT = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:e4b"

# Loopback only. This module talks to a model server, not to the internet, and a
# base URL pointing anywhere else is a mistake rather than a feature. `egress.py`
# governs dataset fetches and deliberately has no runtime override; this is the
# same posture for the same reason.
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", ""})


class ProbeUnavailable(RuntimeError):
    """No model server answered. Not a finding — the probe did not run."""


# --------------------------------------------------------------------------- #
# Flaws that trusted code can confirm
# --------------------------------------------------------------------------- #


def _flaw_id_overlap(c: Candidate) -> str | None:
    """Train and eval share rows. `no_leakage` must block this."""
    worst = max((r.overlap_count for r in c.runs), default=0)
    return f"{worst} overlapping ids between train and eval" if worst > 0 else None


def _flaw_group_leak(c: Candidate) -> str | None:
    """A recording session appears on both sides. `no_group_leakage` must block
    it — when a grouping is declared, which the caller controls."""
    for run in c.runs:
        train = set(run.extra.get("train_groups") or [])
        evaluate = set(run.extra.get("eval_groups") or [])
        shared = train & evaluate
        if shared:
            return f"{len(shared)} group(s) on both sides: {sorted(shared)[:3]}"
    return None


def _flaw_exploratory_promoted(c: Candidate) -> str | None:
    """G-07 rule 7 is unconditional: an exploratory run is never promoted."""
    return "declared exploratory" if c.exploratory else None


# name -> (detector, the gate that must have caught it)
_DETECTORS: dict[str, tuple[Any, str]] = {
    "id_overlap": (_flaw_id_overlap, "no_leakage"),
    "group_leak": (_flaw_group_leak, "no_group_leakage"),
    "exploratory": (_flaw_exploratory_promoted, "preregistration"),
}


def confirmed_flaws(candidate: Candidate) -> dict[str, str]:
    """Every flaw this module can prove is present. Never asks the model."""
    out: dict[str, str] = {}
    for name, (detect, _gate) in _DETECTORS.items():
        detail = detect(candidate)
        if detail is not None:
            out[name] = detail
    return out


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    """A candidate with a proven flaw that the gate set promoted anyway."""

    flaw: str
    evidence: str
    expected_gate: str
    candidate: Candidate

    def __str__(self) -> str:
        return (
            f"FALSE ACCEPT [{self.flaw}] promoted despite {self.evidence}; "
            f"{self.expected_gate} should have blocked it"
        )


@dataclass
class ProbeReport:
    attempts: int = 0
    unparsable: int = 0
    # Generated something, but nothing this module can prove is wrong. Not a
    # pass — an attempt whose result is unknown.
    duds: int = 0
    # Flaw confirmed and correctly blocked. The gate set working.
    caught: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    # Detectors aimed at gates this verifier never ran. Their attempts proved
    # nothing either way, and saying so is the difference between "clean" and
    # "clean over the part that was switched on".
    unarmed: frozenset[str] = frozenset()

    @property
    def is_clean(self) -> bool:
        return not self.findings

    def __str__(self) -> str:
        head = (
            f"{self.attempts} attempts: {sum(self.caught.values())} flawed and caught, "
            f"{self.duds} produced no provable flaw, {self.unparsable} unparsable"
        )
        lines = [head]
        if self.unarmed:
            lines.append(
                f"  NOT ARMED: {sorted(self.unarmed)} — this verifier does not run "
                "those gates, so nothing here says anything about them"
            )
        if not self.findings:
            lines.append("no false accepts")
            return "\n".join(lines)
        lines.append(f"{len(self.findings)} FALSE ACCEPT(S):")
        lines += [f"  {f}" for f in self.findings]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

_PROMPT = """You are testing a scientific result-verification system. It rejects \
experiments whose evidence does not support the claim.

Produce ONE JSON object describing a fabricated experiment that is genuinely \
flawed but tries to look legitimate. Aim for the flaw named: {target}.

Schema:
{{"hypothesis": str,
  "exploratory": bool,
  "runs": [{{"seed": int,
             "val_metric": float,
             "train_ids_hash": str,
             "eval_ids_hash": str,
             "overlap_count": int,
             "wall_seconds": float,
             "extra": {{"train_groups": [str], "eval_groups": [str]}}}}]}}

Use 3 to 5 runs with slightly different val_metric values between 0.5 and 0.99. \
Reply with the JSON object only.

Flaw meanings:
- id_overlap: overlap_count above zero, so train and eval share rows
- group_leak: the same group string in both train_groups and eval_groups
- exploratory: exploratory true, while the numbers look strong

Attempt {n}. Vary your approach from previous attempts."""


def _check_endpoint(endpoint: str) -> None:
    host = (urlsplit(endpoint).hostname or "").lower()
    if host not in _LOOPBACK:
        raise ValueError(
            f"llm_probe talks to a local model server only; {host!r} is not loopback. "
            "Pointing this at a remote host would make it an egress path, which is "
            "governed by egress.py and not by a command-line flag."
        )


def generate(
    target: str,
    n: int,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    timeout: float = 120.0,
) -> str:
    """One raw completion. Raises `ProbeUnavailable` if no server answers."""
    _check_endpoint(endpoint)
    payload = json.dumps(
        {
            "model": model,
            "prompt": _PROMPT.format(target=target, n=n),
            "stream": False,
            "format": "json",
            "options": {"temperature": 1.0},
        }
    ).encode()

    request = urllib.request.Request(  # noqa: S310 — loopback, enforced above
        f"{endpoint.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeUnavailable(f"no model server at {endpoint}: {exc}") from exc
    return str(body.get("response", ""))


def parse(raw: str) -> Candidate | None:
    """Model output into a `Candidate`, or None if it is not usable.

    Treated as untrusted data throughout: parsed with `json.loads`, never
    evaluated, and every field coerced to the type the dataclass expects rather
    than trusted to arrive correct.
    """
    try:
        blob = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(blob, dict):
        return None

    raw_runs = blob.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs:
        return None

    runs: list[RunResult] = []
    for i, r in enumerate(raw_runs):
        if not isinstance(r, dict):
            return None
        extra = r.get("extra")
        try:
            runs.append(
                RunResult(
                    seed=int(r.get("seed", i)),
                    val_metric=float(r.get("val_metric", 0.0)),
                    train_ids_hash=str(r.get("train_ids_hash", "t")),
                    eval_ids_hash=str(r.get("eval_ids_hash", "e")),
                    overlap_count=int(r.get("overlap_count", 0)),
                    wall_seconds=float(r.get("wall_seconds", 0.0)),
                    extra=extra if isinstance(extra, dict) else {},
                )
            )
        except (TypeError, ValueError):
            return None

    try:
        return Candidate(
            hypothesis=str(blob.get("hypothesis", "generated"))[:500],
            config={},
            code_hash="llm-probe",
            runs=runs,
            exploratory=bool(blob.get("exploratory", False)),
        )
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def judge(candidate: Candidate, verdict: VerdictBundle) -> list[Finding]:
    """Findings for one adjudicated candidate. Pure; no model, no network.

    A confirmed flaw plus a promotion is **not** sufficient. The gate that should
    have caught it must also have *run* — otherwise this reports the silence of a
    gate nobody armed.

    Found by the first live run, which produced a false accept on `exploratory`
    within six attempts. G-07 only runs when the verifier is built with
    `require_prereg=True`, which is a workflow switch rather than a security
    toggle; a verifier without it never looks at `exploratory` and is not wrong
    to promote. The claim in the module docstring — that false positives are
    structurally impossible — was true of the flaw half and false of the verdict
    half, and only running it showed that.
    """
    if not verdict.promoted:
        return []

    ran = set(verdict.gate_names)
    return [
        Finding(flaw=name, evidence=detail, expected_gate=_DETECTORS[name][1], candidate=candidate)
        for name, detail in confirmed_flaws(candidate).items()
        if _DETECTORS[name][1] in ran
    ]


def unarmed_gates(verifier: GateVerifier) -> set[str]:
    """Gates a detector expects that this verifier does not run.

    Reported as coverage rather than silently tolerated: an attempt aimed at an
    unarmed gate is wasted, and a reader who does not know which are unarmed will
    read a clean report as broader than it is. Same handling as `uncovered_gates`
    in `gate_probe`, for the same reason.
    """
    probe_candidate = Candidate(
        hypothesis="arming check",
        config={},
        code_hash="llm-probe",
        runs=[RunResult(seed=s, val_metric=0.8 + s / 100, **_IDS) for s in range(3)],
    )
    ran = set(verifier.run(probe_candidate).gate_names)
    return {gate for _, gate in _DETECTORS.values()} - ran


_IDS: dict[str, Any] = {
    "train_ids_hash": "t",
    "eval_ids_hash": "e",
    "overlap_count": 0,
    "wall_seconds": 0.0,
}


def probe(
    attempts: int = 20,
    *,
    verifier: GateVerifier | None = None,
    targets: Sequence[str] = tuple(_DETECTORS),
    generator: Any = None,
    **kwargs: Any,
) -> ProbeReport:
    """Run the fuzzer. `generator(target, n)` is injectable so the loop is
    testable without a model server."""
    gen = generator or (lambda target, n: generate(target, n, **kwargs))
    # A grouping must be declared for G-09 to block rather than warn — the same
    # trust boundary the gate has everywhere else. Without it the group-leak
    # attempts would be scored against a gate that was never armed, and every one
    # would read as a false accept.
    verify = verifier or GateVerifier(grouping=DatasetGrouping("recording_session", "llm_probe"))

    report = ProbeReport(unarmed=frozenset(unarmed_gates(verify)))
    for n, target in _round_robin(targets, attempts):
        report.attempts += 1
        candidate = parse(gen(target, n))
        if candidate is None:
            report.unparsable += 1
            continue

        flaws = confirmed_flaws(candidate)
        if not flaws:
            report.duds += 1
            continue

        found = judge(candidate, verify.run(candidate))
        if found:
            report.findings.extend(found)
        else:
            for name in flaws:
                report.caught[name] = report.caught.get(name, 0) + 1
    return report


def _round_robin(targets: Sequence[str], attempts: int) -> Iterator[tuple[int, str]]:
    if not targets:
        raise ValueError("no targets to probe")
    for n in range(attempts):
        yield n, targets[n % len(targets)]


def armed_verifier(ledger_path: str | Path) -> GateVerifier:
    """The verifier the hill-climb runner must build (SPEC.md §6), for probing.

    Exists because the armed configuration was previously reachable only by
    hand-writing a script (BRE-45). The default run prints
    `NOT ARMED: ['preregistration']` on every invocation, and the gates it is
    silent about are G-07 and G-08 — the pair BRE-40 most recently found a
    promotion bypass in (`G-08 disabled by parent_id=None`).

    So the reproducible command covered everything except the area most recently
    known broken, and the only run that covered it was one nobody could repeat.

    The ledger is a real one on disk rather than a stub: G-07's ordering proof is
    over ledger positions, so a store that does not order cannot arm the gate it
    is supposed to arm. It is written to and should be a scratch path.
    """
    from expfactory.verifier import Ledger

    return GateVerifier(
        grouping=DatasetGrouping("recording_session", "llm_probe"),
        require_prereg=True,
        prereg_store=Ledger(ledger_path),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(prog="python -m expfactory.llm_probe", description=__doc__)
    ap.add_argument("--attempts", type=int, default=20)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument(
        "--require-prereg",
        action="store_true",
        help=(
            "arm G-07/G-08. Off by default on purpose: this same gate set "
            "adjudicates one-off candidates with no lineage, and requiring a "
            "preregistration there would reject everything and destroy their "
            "diagnostic value. Run BOTH — clean means different things in each."
        ),
    )
    ap.add_argument(
        "--ledger",
        default=None,
        help="where --require-prereg's ledger lives. Defaults to a temp file.",
    )
    args = ap.parse_args(argv)

    verifier = None
    tmpdir: tempfile.TemporaryDirectory[str] | None = None
    if args.require_prereg:
        if args.ledger:
            verifier = armed_verifier(args.ledger)
        else:
            # Held open for the run: TemporaryDirectory deletes on close, and a
            # ledger deleted mid-probe would make G-07's ordering proof read
            # positions out of a file that is no longer there.
            tmpdir = tempfile.TemporaryDirectory(prefix="llm_probe_ledger_")
            verifier = armed_verifier(Path(tmpdir.name) / "ledger.jsonl")

    try:
        report = probe(args.attempts, verifier=verifier, model=args.model, endpoint=args.endpoint)
    except ProbeUnavailable as exc:
        print(f"probe did not run: {exc}")
        # Not a pass and not a failure. Distinguished from both so a CI job that
        # ever wraps this cannot read "no server" as "no findings".
        return 3
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()

    print(report)
    return 0 if report.is_clean else 1


__all__ = [
    "Finding",
    "ProbeReport",
    "ProbeUnavailable",
    "armed_verifier",
    "confirmed_flaws",
    "generate",
    "judge",
    "main",
    "parse",
    "probe",
]


if __name__ == "__main__":
    raise SystemExit(main())
