"""Retrieval evaluation (Doc 07 section 7.9).

A retriever that has never been measured is a retriever that does not work; it
just has not been caught yet. This module defines a golden set of questions with
the chunks that should answer them, and reports the metrics that matter for
grounding:

* **Recall@k** — is the answer anywhere in what the model will see? If not, the
  model cannot answer correctly no matter how good it is.
* **MRR** — how far down the list is it? A correct chunk at rank 5 competes with
  four irrelevant ones for the model's attention.
* **Hit@1** — is the top result right? This is what a user reading a search page
  actually judges.

The golden set names *section paths* rather than chunk ids, because chunk ids
change whenever the chunker changes and the expectation should survive that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from at_rag.index import KnowledgeIndex


@dataclass(frozen=True, slots=True)
class GoldenQuestion:
    """A question with the section that should answer it."""

    question: str
    #: Substring matched against a chunk's section path. Any hit whose path
    #: contains one of these is considered relevant.
    expected_sections: tuple[str, ...]
    source_types: tuple[str, ...] | None = None
    note: str = ""


#: Written to cover the query shapes this corpus actually attracts: symptom
#: descriptions, exact task codes, threshold lookups, and policy questions.
GOLDEN_SET: tuple[GoldenQuestion, ...] = (
    GoldenQuestion(
        "why would HPC outlet temperature be rising",
        ("Trend interpretation",),
        note="symptom phrased in plain language, not manual vocabulary",
    ),
    GoldenQuestion(
        "task 72-31-00-200-802",
        ("72-31-00-200-802",),
        note="exact task code; dense retrieval alone fails this",
    ),
    GoldenQuestion(
        "how do I inspect the high pressure compressor with a borescope",
        ("borescope", "72-31-00-200-802"),
    ),
    GoldenQuestion(
        "what does a water wash recover",
        ("performance restoration", "water wash", "72-31-00-700-804"),
    ),
    GoldenQuestion(
        "when must an engine be grounded",
        ("Dispatch and limits", "Escalation"),
    ),
    GoldenQuestion(
        "what health index means critical",
        ("Health bands", "Dispatch and limits"),
    ),
    GoldenQuestion(
        "falling coolant bleed flow on the HP turbine",
        ("72-51-00-200-820", "coolant bleed", "Trend interpretation"),
    ),
    GoldenQuestion(
        "rising fuel flow ratio combustor",
        ("72-41-00-200-810", "Combustor"),
    ),
    GoldenQuestion(
        "how should remaining useful life predictions be used for planning",
        ("predictive model output", "Use of predictive"),
    ),
    GoldenQuestion(
        "what are the C-MAPSS fault modes",
        ("Fault modes",),
        source_types=("NASA",),
    ),
    GoldenQuestion(
        "why must operating condition be accounted for",
        ("Operating conditions", "Trend interpretation"),
        note="the ADR-014 finding, stated in the corpus",
    ),
    GoldenQuestion(
        "does a predictive tool determine airworthiness",
        ("predictive analytics", "Use of predictive", "Dispatch and limits"),
    ),
    GoldenQuestion(
        "what records must be kept after maintenance",
        ("Record keeping",),
    ),
    GoldenQuestion(
        "fan blade damage after bird strike",
        ("72-61-00-200-830", "Fan"),
    ),
    GoldenQuestion(
        "when does an anomaly alert require action",
        ("Anomaly response", "Escalation"),
    ),
)


@dataclass(frozen=True, slots=True)
class QuestionResult:
    question: str
    hit_rank: int | None
    top_section: str
    note: str = ""

    @property
    def hit_at_1(self) -> bool:
        return self.hit_rank == 1

    def hit_at_k(self, k: int) -> bool:
        return self.hit_rank is not None and self.hit_rank <= k

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.hit_rank if self.hit_rank else 0.0


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    results: tuple[QuestionResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def hit_at_1(self) -> float:
        return sum(r.hit_at_1 for r in self.results) / max(1, self.total)

    def recall_at(self, k: int) -> float:
        return sum(r.hit_at_k(k) for r in self.results) / max(1, self.total)

    @property
    def mrr(self) -> float:
        return sum(r.reciprocal_rank for r in self.results) / max(1, self.total)

    @property
    def misses(self) -> tuple[QuestionResult, ...]:
        return tuple(r for r in self.results if r.hit_rank is None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "questions": self.total,
            "hit_at_1": round(self.hit_at_1, 4),
            "recall_at_3": round(self.recall_at(3), 4),
            "recall_at_5": round(self.recall_at(5), 4),
            "mrr": round(self.mrr, 4),
            "misses": [r.question for r in self.misses],
        }


def _matches(section_path: str, content: str, expected: tuple[str, ...]) -> bool:
    haystack = f"{section_path}\n{content}".lower()
    return any(term.lower() in haystack for term in expected)


def evaluate(
    index: KnowledgeIndex,
    questions: tuple[GoldenQuestion, ...] = GOLDEN_SET,
    *,
    limit: int = 5,
) -> EvaluationReport:
    """Run the golden set against an index."""
    results: list[QuestionResult] = []

    for question in questions:
        hits = index.search(
            question.question,
            limit=limit,
            source_types=list(question.source_types) if question.source_types else None,
        )

        rank: int | None = None
        for position, hit in enumerate(hits, start=1):
            if _matches(hit.chunk.section_path, hit.chunk.content, question.expected_sections):
                rank = position
                break

        results.append(
            QuestionResult(
                question=question.question,
                hit_rank=rank,
                top_section=hits[0].chunk.section_path if hits else "",
                note=question.note,
            )
        )

    return EvaluationReport(results=tuple(results))
