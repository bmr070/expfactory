"""
The demo's planted verdicts are real.

Written because they were not, and because nothing was checking. `examples/
demo_drone.py` sat in the repository through a rename of `Ledger` to
`ExperimentLedger` and stopped importing entirely; nobody noticed, because no
test ran it. By the time it was measured it was wrong about three of its four
scenarios:

  1. the case planted as "seed noise" was the *best model in the demo*, beating
     the case planted as the real improvement, so `best_promoted()` advertised
     the scenario the docstring called noise;
  2. the case planted as a "big honest-looking gain" from leakage gained +0.0005
     over the honest version -- a linear model cannot memorise the rows it is
     handed, so the leak was invisible in the metric;
  3. worst, every seed produced an identical number. lbfgs logistic regression
     ignores `random_state`, so `gate_seed_variance` -- the gate the demo exists
     to showcase -- computed a band of exactly 0.0000 and promoted any positive
     delta at all, while appearing to scrutinise them.

That is the failure this repository is about: an artifact asserting things about
the verifier that no one had checked. The ratchet (W-11) is that the claims are
now enforced at the cheapest sufficient point, which for a calibration is a test
rather than a paragraph in a docstring.

Assertions are on verdicts and margins, not exact floats, so a numpy or
scikit-learn point release cannot make this flaky. Every margin below is at
least 5x, so a real drift still trips it.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo_drone.py"

# The demo needs the `demo` extra (numpy/scipy/scikit-learn), which the verifier
# core deliberately does not depend on. Locally that may be absent and skipping
# is the right call. In CI it must never skip -- a check that can quietly not run
# is how this file rotted in the first place -- so CI sets this variable and the
# import error is allowed to propagate as a hard failure.
REQUIRE = os.environ.get("EXPFACTORY_REQUIRE_DEMO") == "1"

try:
    import sklearn  # noqa: F401

    HAVE_DEPS = True
except ImportError:
    if REQUIRE:
        raise
    HAVE_DEPS = False

pytestmark = pytest.mark.skipif(
    not HAVE_DEPS, reason="needs the `demo` extra; set EXPFACTORY_REQUIRE_DEMO=1 to force"
)


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Run the whole demo once. Four experiments x five seeds, a few seconds."""
    spec = importlib.util.spec_from_file_location("demo_drone", DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger = tmp_path_factory.mktemp("demo") / "drone.jsonl"
    return module.main(ledger)


def test_the_demo_imports_and_runs(results):
    """The regression that started this. It had stopped importing at all."""
    assert set(results) == {"baseline", "A", "B", "C"}
    assert all(len(e.runs) == 5 for e in results.values())


def test_the_seeds_actually_move_the_number(results):
    """The load-bearing one.

    With a deterministic solver and no per-seed resampling, every run returns the
    same value, the noise band is exactly zero, and `gate_seed_variance` promotes
    anything above baseline while looking like it is doing the opposite. A demo
    of a verifier that silently verifies nothing is worse than no demo.
    """
    base = results["baseline"]
    assert base.std_metric > 1e-4, (
        f"baseline std is {base.std_metric:.6f} - the seed-variance gate has no "
        "band to measure against and will rubber-stamp any positive delta"
    )
    assert len({r.val_metric for r in base.runs}) == 5, "seeds are producing identical runs"


def test_the_honest_improvement_promotes(results):
    """A: per-modality standardisation. A real finding, clear of the band."""
    base, a = results["baseline"], results["A"]
    delta = a.mean_metric - base.mean_metric
    band = 2.0 * ((a.std_metric**2 + base.std_metric**2) ** 0.5) / 5**0.5

    assert a.promoted, f"blocked by {a.blocked_by}"
    assert delta > 5 * band, f"delta {delta:+.4f} is no longer comfortably clear of band {band:.4f}"


def test_the_planted_noise_case_is_genuinely_noise(results):
    """B: the scenario this ticket was filed about.

    It must sit *inside* the band, not merely fail to win. A scenario that fails
    for some other reason would still print REJECTED and teach the wrong lesson.
    """
    base, b = results["baseline"], results["B"]
    delta = b.mean_metric - base.mean_metric
    band = 2.0 * ((b.std_metric**2 + base.std_metric**2) ** 0.5) / 5**0.5

    assert b.blocked_by == ["seed_variance"], f"expected a noise rejection, got {b.blocked_by}"
    assert abs(delta) < band, f"delta {delta:+.4f} escaped the band {band:.4f} - recalibrate"


def test_one_lucky_seed_still_looks_like_a_win(results):
    """The reason the noise case is instructive rather than merely negative.

    Reporting the best seed is how a hill-climb drifts for weeks. If no seed
    beats the baseline the demo stops showing why five seeds are needed.
    """
    base, b = results["baseline"], results["B"]
    best_seed = max(r.val_metric for r in b.runs)
    assert best_seed > base.mean_metric, "no seed reads as a win; the noise case lost its point"


def test_the_leak_is_caught_by_id_accounting_not_by_the_metric(results):
    """C: the leak, and the sharpest lesson in the demo.

    Contaminating a 21-parameter linear model with ~127 eval rows per run moves
    the metric by a fraction of a percent — it is *not* the suspicious-looking
    jump the original docstring promised. Nothing about the number gives it away.
    Only the recorded sample ids do, which is why runs carry `overlap_count`.
    """
    a, c = results["A"], results["C"]

    assert "no_leakage" in c.blocked_by
    assert not c.promoted
    assert sum(r.overlap_count for r in c.runs) > 0, "the leak stopped leaking"

    # the metric is NOT the tell: it is indistinguishable from the honest result
    assert abs(c.mean_metric - a.mean_metric) < 0.01, (
        "the leak now moves the metric enough to be noticed by eye, which "
        "undercuts the point that only id accounting catches it"
    )
    # and it sails through the gate that looks at the number
    assert [g.passed for g in c.gates if g.name == "seed_variance"] == [True]


def test_the_promoted_winner_is_the_real_improvement(results):
    """The end-to-end property. The demo's closing line used to advertise the
    scenario its own docstring called noise."""
    promoted = {k for k, e in results.items() if e.promoted}
    assert promoted == {"baseline", "A"}, f"unexpected promotions: {promoted}"
    assert results["A"].mean_metric > results["B"].mean_metric


def test_ci_forces_this_file_to_run():
    """Guards the guard.

    Everything above is skipped when the `demo` extra is absent. That is fine
    locally and unacceptable in CI, where a silent skip would restore exactly the
    condition that let this file rot. Asserted against the workflow rather than
    trusted.
    """
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "EXPFACTORY_REQUIRE_DEMO: 1" in ci, "CI no longer forces the demo test to run"
    assert '".[dev,demo]"' in ci, "CI no longer installs the demo extra"
