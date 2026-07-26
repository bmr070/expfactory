"""
Provenance: a hypothesis resolves to real literature, or it does not ship.

The corpus is checked as data, not only the code that reads it. A citation to a
paper that is not in the corpus, or a mechanism whose source is missing, is a
claim with no provenance — the textual equivalent of a metric with no recorded
run. Both are things this repository refuses to take on trust.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from expfactory.literature import (
    Corpus,
    Mechanism,
    Paper,
    ProvenanceError,
    ResearchHypothesis,
    VenueTier,
    novelty_of,
    provenance_of,
    rank,
    triage_score,
)

ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = ROOT / "docs" / "literature" / "corpus.json"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return Corpus.from_json(CORPUS_PATH)


# --------------------------------------------------------------------------- #
# The shipped corpus
# --------------------------------------------------------------------------- #


def test_the_corpus_loads_and_is_not_empty(corpus: Corpus):
    """Guards the guard: an empty corpus would make everything below vacuous."""
    assert len(corpus.papers) >= 8
    assert len(corpus.mechanisms) >= 5


def test_no_mechanism_cites_a_paper_the_corpus_does_not_have(corpus: Corpus):
    assert corpus.dangling_sources() == []


def test_every_paper_has_a_resolvable_url_and_a_real_date(corpus: Corpus):
    for p in corpus.papers.values():
        assert p.url.startswith("https://"), f"{p.key} has no https url"
        # Paper.__post_init__ already rejects an unparseable date; this catches
        # the other direction — a date in the future, which means a typo.
        assert dt.date.fromisoformat(p.published) <= dt.date.today(), f"{p.key} is dated ahead"


def test_every_paper_records_at_least_one_claim(corpus: Corpus):
    """A citation with no extracted claim is a bookmark. The point of the corpus
    is what was taken from the paper, not that it was seen."""
    for p in corpus.papers.values():
        assert p.claims, f"{p.key} records no claims"


def test_derived_dates_are_marked_as_derived(corpus: Corpus):
    """Honesty about precision. Where the exact submission date was not on the
    page retrieved, the date is the first of the arXiv month — and the record has
    to say so rather than implying day-level accuracy."""
    for p in corpus.papers.values():
        if p.published.endswith("-01") and p.tier is VenueTier.PREPRINT:
            marked = any("date-derived" in c for c in p.claims)
            assert marked, f"{p.key} looks month-derived but does not say so"


def test_the_leakage_finding_that_motivated_g09_is_recorded(corpus: Corpus):
    """G-09 exists because of this paper. If the citation ever disappears, the
    gate becomes a rule with no recorded reason, which is how rules rot."""
    echohawk = corpus.papers["echohawk-2026"]
    assert "session-grouped" in " ".join(echohawk.claims).lower()
    assert any("0.796" in c and "0.745" in c for c in echohawk.claims)
    assert "session-grouped-cv" in corpus.mechanisms


# --------------------------------------------------------------------------- #
# Triage
# --------------------------------------------------------------------------- #


def _paper(key: str, tier: VenueTier, published: str) -> Paper:
    return Paper(
        key=key,
        title=key,
        venue="v",
        tier=tier,
        published=published,
        url="https://x",
        claims=("c",),
    )


def test_selection_outranks_a_preprint_of_the_same_age():
    today = dt.date(2026, 7, 26)
    oral = _paper("a", VenueTier.ORAL, "2026-01-01")
    pre = _paper("b", VenueTier.PREPRINT, "2026-01-01")
    assert triage_score(oral, today) > triage_score(pre, today)


def test_a_recent_preprint_outranks_an_old_oral():
    """The weights are deliberately close. Selection is a real signal and a weak
    one; if it dominated recency, a six-week-old preprint reporting a leak in the
    dataset you are about to use would sort below a two-year-old oral."""
    today = dt.date(2026, 7, 26)
    old_oral = _paper("a", VenueTier.ORAL, "2023-07-26")
    new_pre = _paper("b", VenueTier.PREPRINT, "2026-07-01")
    assert triage_score(new_pre, today) > triage_score(old_oral, today)


def test_ranking_is_stable_for_ties():
    today = dt.date(2026, 7, 26)
    papers = [
        _paper("z", VenueTier.POSTER, "2026-01-01"),
        _paper("a", VenueTier.POSTER, "2026-01-01"),
    ]
    assert [p.key for p, _ in rank(papers, today)] == ["a", "z"]
    assert [p.key for p, _ in rank(list(reversed(papers)), today)] == ["a", "z"]


def test_no_gate_reads_the_venue_tier():
    """Selection signal ranks a reading list. If it ever reached the gate set,
    the factory would be promoting results because their inspiration was an oral,
    which is the purest form of the fooling it exists to prevent."""
    gate_src = (ROOT / "src" / "expfactory" / "gates_v1.py").read_text(encoding="utf-8")
    verifier_src = (ROOT / "src" / "expfactory" / "verifier.py").read_text(encoding="utf-8")
    for name in ("VenueTier", "triage_score", "tier"):
        assert name not in gate_src, f"{name} leaked into the gate set"
        assert name not in verifier_src, f"{name} leaked into the verifier"


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


def test_a_hypothesis_resolves_to_its_papers_and_mechanisms(corpus: Corpus):
    h = ResearchHypothesis(
        key="h1",
        statement="group the split by recording session",
        mechanism_keys=("session-grouped-cv",),
        paper_keys=("echohawk-2026",),
        target_task="acoustic-drone-detection",
        predicted_effect="honest Pd falls relative to the clip-level split",
    )
    papers, mechs = provenance_of(h, corpus)
    assert [p.key for p in papers] == ["echohawk-2026"]
    assert [m.key for m in mechs] == ["session-grouped-cv"]


def test_citing_a_paper_that_does_not_exist_is_refused(corpus: Corpus):
    h = ResearchHypothesis(
        key="h2",
        statement="s",
        mechanism_keys=(),
        paper_keys=("smith-et-al-2027-i-made-this-up",),
        target_task="t",
        predicted_effect="e",
    )
    with pytest.raises(ProvenanceError, match="unknown papers"):
        provenance_of(h, corpus)


def test_a_hypothesis_with_no_citation_is_refused(corpus: Corpus):
    """Raises rather than returning an empty tuple. A silent empty result is
    exactly how an unattributed hypothesis comes to look attributed."""
    h = ResearchHypothesis(
        key="h3",
        statement="a good idea I had",
        mechanism_keys=(),
        paper_keys=(),
        target_task="t",
        predicted_effect="e",
    )
    with pytest.raises(ProvenanceError, match="cites no source paper"):
        provenance_of(h, corpus)


def test_a_mechanism_with_a_dangling_source_is_refused():
    corpus = Corpus(
        papers={"real": _paper("real", VenueTier.PREPRINT, "2026-01-01")},
        mechanisms={"m": Mechanism(key="m", summary="s", source_keys=("ghost",))},
    )
    h = ResearchHypothesis(
        key="h4",
        statement="s",
        mechanism_keys=("m",),
        paper_keys=("real",),
        target_task="t",
        predicted_effect="e",
    )
    with pytest.raises(ProvenanceError, match="unknown paper ghost"):
        provenance_of(h, corpus)


def test_the_content_hash_is_stable_across_processes(corpus: Corpus):
    """It goes into a preregistration, so it has to be reproducible. Python's
    builtin hash() is salted per process and would silently break the binding."""
    kwargs = dict(
        key="h5",
        statement="s",
        mechanism_keys=("session-grouped-cv",),
        paper_keys=("echohawk-2026",),
        target_task="t",
        predicted_effect="e",
    )
    assert (
        ResearchHypothesis(**kwargs).content_hash() == ResearchHypothesis(**kwargs).content_hash()
    )
    assert (
        ResearchHypothesis(**kwargs).content_hash()
        != ResearchHypothesis(**{**kwargs, "predicted_effect": "different"}).content_hash()
    )


def test_hash_ignores_citation_order_but_not_citation_content(corpus: Corpus):
    a = ResearchHypothesis(
        key="h",
        statement="s",
        mechanism_keys=("session-grouped-cv", "rotor-harmonic-bpf"),
        paper_keys=("echohawk-2026",),
        target_task="t",
        predicted_effect="e",
    )
    b = ResearchHypothesis(
        key="h",
        statement="s",
        mechanism_keys=("rotor-harmonic-bpf", "session-grouped-cv"),
        paper_keys=("echohawk-2026",),
        target_task="t",
        predicted_effect="e",
    )
    assert a.content_hash() == b.content_hash()


# --------------------------------------------------------------------------- #
# Novelty classification
# --------------------------------------------------------------------------- #


def test_replication_is_labelled_replication(corpus: Corpus):
    """Re-running a paper's method on its own dataset is worth doing and is not a
    finding. The ledger scored both identically before this existed."""
    h = ResearchHypothesis(
        key="h6",
        statement="reproduce EchoHawk's session-grouped number",
        mechanism_keys=("session-grouped-cv",),
        paper_keys=("echohawk-2026",),
        target_task="acoustic-drone-detection",
        predicted_effect="Pd@1%FAR near 0.745",
        is_replication=True,
    )
    assert novelty_of(h, corpus) == "replication"


def test_combining_domains_is_labelled_cross_domain(corpus: Corpus):
    h = ResearchHypothesis(
        key="h7",
        statement="forecast acoustic track state in latent space",
        mechanism_keys=("latent-forecast-head", "rotor-harmonic-bpf"),
        paper_keys=("ahead-world-model-2026", "echohawk-2026"),
        target_task="acoustic-drone-detection",
        predicted_effect="better carry-through of occluded segments",
    )
    assert novelty_of(h, corpus) == "cross-domain-transfer"


def test_the_corpus_file_is_valid_json_with_a_stated_convention():
    """The dating and tier conventions are load-bearing for honesty. If the
    convention block is dropped, a reader cannot tell a verified date from a
    derived one."""
    raw = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert "_convention" in raw
    text = " ".join(raw["_convention"])
    assert "date-derived" in text or "derived" in text
    assert "preprint" in text
