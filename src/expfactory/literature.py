"""
literature — where a hypothesis comes from, recorded rather than remembered.

The factory could already adjudicate whether a result is real. It had no notion
of where the *idea* came from, which leaves two holes:

1. **HARKing has no counter-evidence.** G-07 stops an agent inventing the
   hypothesis after seeing results, but only within a lineage. Nothing recorded
   that a hypothesis was motivated by a specific paper, so "we predicted this"
   was unfalsifiable.
2. **Replication and research were indistinguishable.** Re-running a paper's
   method on that paper's dataset is a useful act, and it is not a finding. With
   no provenance the ledger recorded both identically.

So a `ResearchHypothesis` cites the `Paper`s that motivated it and the
`Mechanism` it transfers. The citation is content-hashed into the
preregistration, which means it is fixed before the run rather than written to
fit the outcome.

## Why venue selection is a recorded field

Programme committees run an expensive filter and publish the result: oral,
spotlight, highlight. That signal is noisy — it tracks novelty and presentation
as much as correctness, and plenty of load-bearing work is a poster or a
preprint. It is recorded as *one* ranked input to triage, never as a promotion
criterion. Nothing in the gate set reads it. A gate that promoted results
because their inspiration was an oral would be the purest form of the fooling
this repository exists to prevent.

## The corpus is data, not code

This module is verification substrate: `provenance_of` decides whether a
hypothesis is properly attributed. The papers themselves live in
`docs/literature/corpus.json`, which is not substrate, so the reading list can
be updated without an override on the gate layer.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class VenueTier(enum.StrEnum):
    """How hard a filter the work passed, best-effort and deliberately coarse.

    Ranked because the brief was to weight work selected for presentation. Read
    only by `triage_score`; no gate consults it.
    """

    ORAL = "oral"
    SPOTLIGHT = "spotlight"
    HIGHLIGHT = "highlight"
    POSTER = "poster"
    WORKSHOP = "workshop"
    JOURNAL = "journal"
    PREPRINT = "preprint"


# Weight per tier. The gap between selected and unselected is real but small:
# treating a preprint as a tenth of an oral would have discarded EchoHawk, which
# is a preprint and is the most load-bearing paper in the current corpus.
_TIER_WEIGHT: dict[VenueTier, float] = {
    VenueTier.ORAL: 1.00,
    VenueTier.SPOTLIGHT: 0.95,
    VenueTier.HIGHLIGHT: 0.90,
    VenueTier.POSTER: 0.75,
    VenueTier.JOURNAL: 0.75,
    VenueTier.WORKSHOP: 0.70,
    VenueTier.PREPRINT: 0.65,
}


@dataclass(frozen=True)
class Paper:
    """One piece of source literature.

    `claims` are the specific quantitative statements worth transferring, in the
    paper's own terms. Kept verbatim so that a hypothesis built on a
    misremembered number is caught by reading the record.
    """

    key: str
    title: str
    venue: str
    tier: VenueTier
    published: str  # ISO date, the version actually read
    url: str
    domains: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # A citation with no date cannot be recency-ranked and, worse, cannot be
        # checked against the run that cites it.
        dt.date.fromisoformat(self.published)

    @property
    def age_days(self) -> int:
        return (dt.date.today() - dt.date.fromisoformat(self.published)).days


@dataclass(frozen=True)
class Mechanism:
    """A technique lifted out of a paper, stated so it can be applied elsewhere.

    Separate from `Paper` because the unit of transfer is not the paper: it is
    one idea inside it, and the same idea often appears in several. Keeping them
    apart is what lets the corpus answer "what could we try" rather than only
    "what have we read".
    """

    key: str
    summary: str
    source_keys: tuple[str, ...]
    # What has to be true of the target task for the transfer to make sense.
    preconditions: tuple[str, ...] = ()
    # Honest cost, because a mechanism that needs a week of GPU is not the same
    # proposal as one that is a loss-function change.
    cost: str = "unknown"


@dataclass(frozen=True)
class ResearchHypothesis:
    """A mechanism, a target, and a prediction — fixed before the run.

    This is the object that gets hashed into a preregistration. The prediction is
    a direction and a magnitude, not a hope: `predicted_effect` is prose for the
    reader, and the machine-checkable form lives in the prereg's guardrails.
    """

    key: str
    statement: str
    mechanism_keys: tuple[str, ...]
    paper_keys: tuple[str, ...]
    target_task: str
    predicted_effect: str
    # Replication is honourable and is not a finding; recording which it is stops
    # the ledger from scoring them the same.
    is_replication: bool = False
    notes: str = ""

    def content_hash(self) -> str:
        """Stable across processes, unlike `hash()`. Matches the prereg scheme."""
        payload = json.dumps(
            {
                "key": self.key,
                "statement": self.statement,
                "mechanism_keys": sorted(self.mechanism_keys),
                "paper_keys": sorted(self.paper_keys),
                "target_task": self.target_task,
                "predicted_effect": self.predicted_effect,
                "is_replication": self.is_replication,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class Corpus:
    """The reading list, plus what has been extracted from it."""

    papers: dict[str, Paper] = field(default_factory=dict)
    mechanisms: dict[str, Mechanism] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: str | Path) -> Corpus:
        raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        papers = {
            p["key"]: Paper(
                key=p["key"],
                title=p["title"],
                venue=p["venue"],
                tier=VenueTier(p["tier"]),
                published=p["published"],
                url=p["url"],
                domains=tuple(p.get("domains", ())),
                claims=tuple(p.get("claims", ())),
            )
            for p in raw.get("papers", [])
        }
        mechanisms = {
            m["key"]: Mechanism(
                key=m["key"],
                summary=m["summary"],
                source_keys=tuple(m["source_keys"]),
                preconditions=tuple(m.get("preconditions", ())),
                cost=m.get("cost", "unknown"),
            )
            for m in raw.get("mechanisms", [])
        }
        return cls(papers=papers, mechanisms=mechanisms)

    def dangling_sources(self) -> list[str]:
        """Mechanisms citing a paper the corpus does not contain.

        A mechanism whose source is missing is a claim with no provenance, which
        is the condition this module exists to make impossible.
        """
        return sorted(
            f"{m.key} -> {src}"
            for m in self.mechanisms.values()
            for src in m.source_keys
            if src not in self.papers
        )


def triage_score(paper: Paper, today: dt.date | None = None, half_life_days: int = 365) -> float:
    """Rank a paper for reading attention. Selection signal x recency decay.

    Not a quality judgement and not consulted by any gate. It answers "what
    should be read next" when the corpus is larger than the reading budget, and
    nothing else.
    """
    today = today or dt.date.today()
    age = max((today - dt.date.fromisoformat(paper.published)).days, 0)
    # annotated because `float ** float` widens to Any under strict mypy
    recency: float = 0.5 ** (age / half_life_days)
    return _TIER_WEIGHT[paper.tier] * recency


def rank(papers: Iterable[Paper], today: dt.date | None = None) -> list[tuple[Paper, float]]:
    scored = [(p, triage_score(p, today)) for p in papers]
    # key on the score and then the paper key, so ties do not reorder between
    # runs and a ranked reading list is reproducible.
    return sorted(scored, key=lambda t: (-t[1], t[0].key))


class ProvenanceError(ValueError):
    """A hypothesis whose attribution does not resolve."""


def provenance_of(
    hypothesis: ResearchHypothesis, corpus: Corpus
) -> tuple[tuple[Paper, ...], tuple[Mechanism, ...]]:
    """Resolve a hypothesis to the literature it claims, or refuse.

    Raises rather than returning empty, because a silent empty tuple is exactly
    how an unattributed hypothesis would come to look attributed. Callers that
    want the soft form should catch it.
    """
    missing_papers = [k for k in hypothesis.paper_keys if k not in corpus.papers]
    missing_mechs = [k for k in hypothesis.mechanism_keys if k not in corpus.mechanisms]
    if missing_papers or missing_mechs:
        raise ProvenanceError(
            f"{hypothesis.key} cites unknown papers {missing_papers} "
            f"and unknown mechanisms {missing_mechs}"
        )
    if not hypothesis.paper_keys:
        raise ProvenanceError(f"{hypothesis.key} cites no source paper")

    # Every mechanism's own sources must also be in the corpus, or the chain
    # from claim to citation has a hole in the middle.
    papers = tuple(corpus.papers[k] for k in sorted(hypothesis.paper_keys))
    mechs = tuple(corpus.mechanisms[k] for k in sorted(hypothesis.mechanism_keys))
    for m in mechs:
        for src in m.source_keys:
            if src not in corpus.papers:
                raise ProvenanceError(f"mechanism {m.key} cites unknown paper {src}")
    return papers, mechs


def novelty_of(hypothesis: ResearchHypothesis, corpus: Corpus) -> str:
    """Classify the *kind* of contribution, for the record rather than for a gate.

    Three kinds, and the distinction matters because they warrant different
    scepticism. A transfer that combines mechanisms from different domains is the
    interesting case and also the one most likely to be wrong.
    """
    if hypothesis.is_replication:
        return "replication"
    _, mechs = provenance_of(hypothesis, corpus)
    domains = {d for m in mechs for src in m.source_keys for d in corpus.papers[src].domains}
    return "cross-domain-transfer" if len(domains) > 1 else "within-domain-extension"


def reading_list(corpus: Corpus, limit: int = 10, today: dt.date | None = None) -> Sequence[str]:
    """Human-facing: what to read next, and why it is ranked where it is."""
    return [
        f"{score:.2f}  [{p.tier}] {p.published}  {p.title}"
        for p, score in rank(corpus.papers.values(), today)[:limit]
    ]
