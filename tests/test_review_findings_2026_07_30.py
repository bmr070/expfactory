"""Regressions for defects found by adversarial review of the 2026-07-30 diff.

Four fresh-context reviewers went at ~7,200 changed lines. Everything here was
**reproduced by execution** before it was fixed, and each test is written so it
fails against the code as it was.

The reviews are the argument for invariant 3 — the reviewer runs in fresh
context, because the author's context rubber-stamps its own reasoning. Every
finding below was in code that shipped green, with tests, written in the same
session that was congratulating itself on verifying by breaking things.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from expfactory.github_tracker import Page, PageWalkRefused, _next_path
from expfactory.linear_tracker import LinearTracker
from expfactory.linear_tracker import PageWalkRefused as LinearPageWalkRefused
from expfactory.prereg import Preregistration
from expfactory.review_fleet import touches_protected
from expfactory.substrate_guard import changed_paths, touched_protected
from expfactory.substrate_guard import main as guard_main
from expfactory.verifier import Candidate, GateVerifier, Ledger, VerdictBundle

# --------------------------------------------------------------------------- #
# Invariant 1 — `promoted` is derived, never settable
#
# "If a caller can set it, the layer is theatre." It was settable three ways.
# --------------------------------------------------------------------------- #


def _bundle(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "exp_id": "e1",
        "promoted": True,
        "blocked_by": (),
        "config": {},
        "code_hash": "abc",
        "seeds": (0, 1, 2),
        "gate_names": ("no_leakage",),
        "mean_metric": 0.5,
        "cost_usd": 0.0,
        "artifact": {},
    }
    base.update(over)
    return base


def test_a_promoted_verdict_cannot_carry_a_blocking_gate() -> None:
    """The direct construction. Was accepted."""
    with pytest.raises(ValueError, match="contradicts blocked_by"):
        VerdictBundle(**_bundle(promoted=True, blocked_by=("preregistration",)))  # type: ignore[arg-type]


def test_a_blocked_verdict_cannot_claim_it_was_not_promoted_by_nothing() -> None:
    """The other direction. A verdict nothing blocked is promoted, by definition."""
    with pytest.raises(ValueError, match="contradicts blocked_by"):
        VerdictBundle(**_bundle(promoted=False, blocked_by=()))  # type: ignore[arg-type]


def test_replace_cannot_flip_promoted() -> None:
    """`frozen=True` stops mutation after construction; it does not make the
    field derived. `replace` builds a new object and used to accept anything."""
    honest = VerdictBundle(**_bundle(promoted=False, blocked_by=("tamper",)))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="contradicts blocked_by"):
        dataclasses.replace(honest, promoted=True)


def test_a_deserialized_verdict_cannot_smuggle_a_promotion() -> None:
    """The one that matters most.

    `verifier.py`'s docstring anticipates this seam becoming a process boundary
    with bundles arriving as JSON. At that point `from_dict` IS the trust
    boundary and `promoted` is a field the far side writes — invariant 9
    inverted. It used to round-trip a contradiction intact.
    """
    forged = {
        "exp_id": "e1",
        "promoted": True,
        "blocked_by": ["preregistration", "attested_run"],
        "config": {},
        "code_hash": "abc",
        "seeds": [0, 1, 2],
        "gate_names": ["preregistration"],
        "mean_metric": 0.99,
        "cost_usd": 0.0,
        "artifact": {},
    }
    with pytest.raises(ValueError, match="contradicts blocked_by"):
        VerdictBundle.from_dict(forged)


def test_the_honest_shapes_still_construct() -> None:
    """A refusal that refused everything would pass the tests above and be
    useless. Both named constructors must still work."""
    assert VerdictBundle(**_bundle(promoted=True, blocked_by=())).promoted is True  # type: ignore[arg-type]
    assert (
        VerdictBundle(**_bundle(promoted=False, blocked_by=("no_leakage",))).promoted is False  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# The Link header: dropping the host was not enough
# --------------------------------------------------------------------------- #


def test_a_protocol_relative_next_path_is_refused() -> None:
    """The token-exfiltration path.

    `https://api.github.com//evil.example/steal` has netloc `api.github.com` and
    path `//evil.example/steal`. The netloc check saw nothing wrong; the
    surviving path is protocol-relative, so any transport composing it with
    `urljoin` or an httpx `base_url` sends the read — and the `Authorization`
    header bound to it — to `evil.example`. Verified before the fix: it resolved
    to `https://evil.example/steal?x=1`.
    """
    with pytest.raises(PageWalkRefused, match="protocol-relative"):
        _next_path({"Link": '<https://api.github.com//evil.example/steal?x=1>; rel="next"'})


def test_an_upward_traversing_next_path_is_refused() -> None:
    """Stays on-host, so no token leaks, but it can aim page two of one issue's
    timeline at another issue's — and a history assembled from two issues
    answers an authorization question about neither."""
    with pytest.raises(PageWalkRefused, match="traverses upward"):
        _next_path({"Link": '<https://api.github.com/repos/o/r/../../../orgs/x>; rel="next"'})


def test_a_multi_valued_rel_is_still_followed() -> None:
    """`rel` is a token *set* per RFC 8288, and `rel="next last"` is the legal
    form on a final page. Matching it exactly meant the walk read that as "no
    next page" and silently returned a prefix."""
    assert _next_path({"Link": '<https://api.github.com/x?page=2>; rel="next last"'}) == "/x?page=2"


def test_an_ordinary_foreign_host_still_has_its_host_dropped() -> None:
    """The documented behaviour must survive the new refusals."""
    assert _next_path({"Link": '<https://evil.example/x?page=2>; rel="next"'}) == "/x?page=2"


def test_a_page_cannot_be_built_without_headers() -> None:
    """`headers` defaulted to `{}`, which preserved exactly the omission the type
    was introduced to make impossible: `Page(json.loads(body))` type-checks,
    satisfies the protocol, and silently makes every read one page deep."""
    with pytest.raises(TypeError):
        Page([])  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Linear: cannot-locate is not empty
# --------------------------------------------------------------------------- #


class _TwoPages:
    """Page one well-formed with `hasNextPage`; page two unlocatable.

    `{"data": {"issue": null}}` with no `errors` key is what a partial GraphQL
    failure looks like, and `_call` does not raise on it.
    """

    def __init__(self) -> None:
        self.calls = 0

    def query(self, document: str, variables: dict[str, object]) -> dict[str, object]:
        if "labels" in document:
            # Complete and well formed: `label_actor` resolves the label id here
            # first, and an empty answer would short-circuit before the history
            # walk this test is about.
            return {
                "data": {
                    "issue": {
                        "labels": {
                            "nodes": [{"id": "L1", "name": "agent-ready"}],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        if "history" not in document:
            return {"data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
        self.calls += 1
        if self.calls == 1:
            return {
                "data": {
                    "issue": {
                        "history": {
                            "nodes": [
                                {
                                    "createdAt": "2026-07-01T00:00:01.000Z",
                                    "actor": {"name": "Brett R", "displayName": "Brett R"},
                                    "botActor": None,
                                    "addedLabelIds": ["L1"],
                                }
                            ],
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                        }
                    }
                }
            }
        return {"data": {"issue": None}}


def test_an_unlocatable_connection_is_refused_not_treated_as_empty() -> None:
    """The live fail-open, and the one this module's docstring already claimed
    was closed.

    The walk used to degrade an unlocatable connection to `{}`, which made
    `nodes` empty, `pageInfo` empty, `hasNextPage` falsy — and then *returned the
    pages it already had*. Reproduced before the fix: `label_actor` answered
    `'Brett R'` for a ticket whose most recent grant was a bot's, because the
    page carrying the bot's re-application was the one that went missing.

    A superseded grant read as current is an authorization decision made on
    partial data, which is the whole failure the pagination work exists to
    remove.
    """
    tracker = LinearTracker("BRE", _TwoPages())
    with pytest.raises(LinearPageWalkRefused, match="could not be located"):
        tracker.label_actor("BRE-1", "agent-ready")


# --------------------------------------------------------------------------- #
# BRE-39 — the wall did not fire on a rename
#
# Driven through real git, like the rest of `test_substrate_guard.py`, because
# the thing under test is partly "what does git actually print".
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout


def _repo_with_harness(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    (repo / "src" / "expfactory").mkdir(parents=True)
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    # Enough content that a rename is detected as a rename rather than an
    # add/delete pair, which is the case the bypass relied on.
    (repo / "src" / "expfactory" / "verifier.py").write_text(
        "\n".join(f"line {n} of the verification substrate" for n in range(40)) + "\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    return repo


def test_renaming_a_protected_module_still_trips_the_guard(tmp_path: Path, monkeypatch) -> None:
    """`git mv gates_v1.py gates.py` used to produce a green required check that
    positively asserted the substrate was untouched.

    `git diff --name-only` prints only the *destination* of a detected rename, so
    the protected name was never in the list the basename check reads. No admin
    override, no timeline entry — and every other control here assumes the wall
    holds, because `enforce_admins` is off and CODEOWNERS binds non-admins only.
    """
    repo = _repo_with_harness(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    src = repo / "src" / "expfactory" / "verifier.py"
    dst = repo / "src" / "expfactory" / "verifier_impl.py"
    _git(repo, "mv", str(src), str(dst))
    # Gut it on the way, so this is not even a pure rename.
    dst.write_text("\n".join(f"line {n} of the verification substrate" for n in range(18)) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "moved and gutted")

    monkeypatch.chdir(repo)
    assert guard_main(["--base", "main"]) == 1, "a renamed protected module walked past the wall"


def test_the_rename_source_is_reported_not_just_the_destination(
    tmp_path: Path, monkeypatch
) -> None:
    """A file leaving the protected set is a change to the protected set."""
    repo = _repo_with_harness(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(
        repo,
        "mv",
        str(repo / "src" / "expfactory" / "verifier.py"),
        str(repo / "src" / "expfactory" / "renamed.py"),
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "moved")

    monkeypatch.chdir(repo)
    paths = changed_paths("main")
    assert "src/expfactory/verifier.py" in paths, f"rename source missing from {paths}"
    assert "src/expfactory/renamed.py" in paths, f"rename destination missing from {paths}"


@pytest.mark.parametrize(
    "path",
    [
        "src/expfactory/Verifier.py",  # case-insensitive filesystem: same file
        "src\\expfactory\\verifier.py",  # a caller on Windows
        "SRC/EXPFACTORY/GATES_V1.PY",
    ],
)
def test_a_differently_spelled_protected_path_is_still_protected(path: str) -> None:
    """The only net for these was a glob in `tests/`, whose basenames are not
    protected — so the catch lived in the half a PR can weaken freely."""
    assert touched_protected([path]) == [path]
    assert touches_protected((path,)) is True


# --------------------------------------------------------------------------- #
# BRE-40 — three confirmed routes to a promoted fake result
# --------------------------------------------------------------------------- #

_LINEAGE = "exp-parent-1"


def _runs(metric: float = 0.75, seeds: tuple[int, ...] = (0, 1, 2)) -> list[dict[str, object]]:
    return [
        dict(
            seed=s,
            val_metric=metric,
            train_ids_hash="t",
            eval_ids_hash="e",
            overlap_count=0,
            wall_seconds=0.0,
        )
        for s in seeds
    ]


def test_a_candidate_cannot_disown_the_lineage_its_prereg_declares(tmp_path: Path) -> None:
    """G-08 was switched off by a field the agent writes.

    Churn was counted from `candidate.parent_id` while the baseline came from
    `record.parent_id`, and nothing reconciled them.
    `non_promoting_prereg_count` returns 0 immediately for `None`, so filing
    against a real parent (keeping rule 8 happy) and submitting with `None`
    zeroed the only gate that sees across preregistrations.

    Reproduced before the fix: eight shopped preregistrations under one parent,
    and the eighth promoted.
    """
    store = Ledger(tmp_path / "ledger.jsonl")
    prereg = Preregistration(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=0.70,
        minimum_effect=0.02,
        seeds=(0, 1, 2),
        parent_id=_LINEAGE,
    )
    store.append_prereg(prereg)
    verifier = GateVerifier(require_prereg=True, prereg_store=store)

    disowned = Candidate(
        hypothesis="h",
        config={},
        code_hash="c",
        runs=_runs(),
        parent_id=None,  # the prereg says `_LINEAGE`
        prereg_hash=prereg.hash,
    )
    with pytest.raises(ValueError, match="lineage"):
        verifier.run(disowned)


def test_declaring_one_seed_five_times_is_refused() -> None:
    """The seed lottery, which the variance gates actively endorsed.

    Rule 5 compares sorted tuples, so a declared multiset of duplicates was
    honoured exactly, and nothing counted distinct seeds. Five runs of seed 7
    give zero spread, which makes the noise band tiny and the dominance gap
    exactly zero — so `seed_variance` reported "real" and
    `no_single_seed_dominance` reported "balanced across seeds".
    """
    with pytest.raises(ValueError, match="duplicates"):
        Preregistration(
            primary_metric="val_metric",
            direction="maximize",
            baseline_value=0.50,
            minimum_effect=0.0,
            seeds=(7, 7, 7, 7, 7),
            parent_id=_LINEAGE,
        )


def test_distinct_seeds_still_construct() -> None:
    """A refusal that refused every seed set would pass the test above and be
    useless."""
    assert Preregistration(
        primary_metric="val_metric",
        direction="maximize",
        baseline_value=0.50,
        minimum_effect=0.0,
        seeds=(0, 1, 2),
        parent_id=_LINEAGE,
    ).seeds == (0, 1, 2)


def test_a_nan_parent_metric_cannot_launder_a_baseline(tmp_path: Path) -> None:
    """BRE-28 closed the write boundary and left the read boundary open.

    `get_verdict_metric` filtered NaN; `get_verdict_metrics` — the reader rules 6
    and 8 actually consume — did not. A NaN parent metric made rule 8's
    `abs(parent - declared) > tol` False, so the forged-baseline check reported
    agreement with a comparison it never made, and every guardrail comparison
    against NaN was False in both branches so none could fire.

    The ledger is append-only, so a single such row was an unfalsifiable
    baseline for its whole lineage, permanently.
    """
    store = Ledger(tmp_path / "ledger.jsonl")
    store.append(
        VerdictBundle(
            exp_id=_LINEAGE,
            promoted=True,
            blocked_by=(),
            config={},
            code_hash="c",
            seeds=(0, 1, 2),
            gate_names=("no_leakage",),
            mean_metric=0.70,
            cost_usd=0.0,
            artifact={},
            metrics={"val_metric": float("nan"), "latency_ms": float("nan")},
        )
    )
    # Absent, not poisoned. Rule 6 already handles "no recorded value on parent"
    # and blocks; an unreadable row would instead jam the whole lineage.
    assert store.get_verdict_metrics(_LINEAGE) == {}


def test_a_finite_parent_metric_is_still_read(tmp_path: Path) -> None:
    """The filter must not eat honest rows."""
    store = Ledger(tmp_path / "ledger.jsonl")
    store.append(
        VerdictBundle(
            exp_id=_LINEAGE,
            promoted=True,
            blocked_by=(),
            config={},
            code_hash="c",
            seeds=(0, 1, 2),
            gate_names=("no_leakage",),
            mean_metric=0.70,
            cost_usd=0.0,
            artifact={},
            metrics={"val_metric": 0.70, "latency_ms": 12.5},
        )
    )
    assert store.get_verdict_metrics(_LINEAGE) == {"val_metric": 0.70, "latency_ms": 12.5}
