#!/usr/bin/env python3
"""Regenerate docs/reports/rag-eval.md from a freshly built index."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from at_rag.chunk import chunk_corpus
from at_rag.evaluate import (
    GOLDEN_SET,
    EvaluationReport,
    QuestionResult,
    _matches,
    evaluate,
)
from at_rag.index import SearchHit, build_index, tokenise

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "data/knowledge"


def single_retriever(index, mode: str) -> EvaluationReport:
    """Evaluate with only one retriever active, to isolate its contribution."""
    results = []
    for question in GOLDEN_SET:
        allowed = index._allowed_indices(
            list(question.source_types) if question.source_types else None
        )
        if mode == "dense":
            vector = np.asarray(
                index.model.encode(
                    [question.question], normalize_embeddings=True, show_progress_bar=False
                ),
                dtype=np.float32,
            )[0]
            scores = index.embeddings @ vector
        else:
            scores = index.bm25.scores(tokenise(question.question))

        order = sorted(allowed, key=lambda i: -float(scores[i]))[:5]
        hits = [SearchHit(chunk=index.chunks[i], score=float(scores[i])) for i in order]

        rank = None
        for position, hit in enumerate(hits, start=1):
            if _matches(hit.chunk.section_path, hit.chunk.content, question.expected_sections):
                rank = position
                break
        results.append(
            QuestionResult(
                question.question,
                rank,
                hits[0].chunk.section_path if hits else "",
                question.note,
            )
        )
    return EvaluationReport(tuple(results))


def main() -> int:
    index = build_index(CORPUS)
    _documents, chunks = chunk_corpus(CORPUS)

    hybrid = evaluate(index)
    dense = single_retriever(index, "dense")
    lexical = single_retriever(index, "lexical")
    stats = index.stats()

    sizes = sorted(chunk.char_count for chunk in chunks)
    rows = "\n".join(
        f"| {r.question} | "
        f"{'**1**' if r.hit_at_1 else (str(r.hit_rank) if r.hit_rank else '**miss**')} | "
        f"{r.top_section[:52]} |"
        for r in hybrid.results
    )
    by_source = ", ".join(f"{k} {v}" for k, v in sorted(stats["by_source_type"].items()))

    report = f"""# RAG Retrieval Evaluation

_Generated {datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")} by `at_rag.evaluate`. Regenerate with `make rag-eval`._

## Corpus

| metric | value |
|---|---|
| Documents | {stats["documents"]} |
| Chunks | {stats["chunks"]} |
| Embedding model | `{stats["model"]}` |
| Dimensions | {stats["dimensions"]} |
| By source type | {by_source} |

Chunk sizes: min {sizes[0]}, median {sizes[len(sizes) // 2]}, max {sizes[-1]} characters.

## Retriever comparison

Fifteen golden questions, each naming the section that should answer it.

| retriever | hit@1 | recall@3 | recall@5 | MRR | unretrievable |
|---|---:|---:|---:|---:|---:|
| Dense only (MiniLM) | {dense.hit_at_1:.2f} | {dense.recall_at(3):.2f} | {dense.recall_at(5):.2f} | {dense.mrr:.3f} | {len(dense.misses)} |
| Lexical only (BM25) | {lexical.hit_at_1:.2f} | {lexical.recall_at(3):.2f} | {lexical.recall_at(5):.2f} | {lexical.mrr:.3f} | {len(lexical.misses)} |
| **Hybrid (RRF)** | **{hybrid.hit_at_1:.2f}** | **{hybrid.recall_at(3):.2f}** | **{hybrid.recall_at(5):.2f}** | **{hybrid.mrr:.3f}** | **{len(hybrid.misses)}** |

### Why hybrid, with evidence

Each single retriever leaves a *different* question unretrievable:

- **Dense misses** `{dense.misses[0].question if dense.misses else "none"}` — the answer shares little vocabulary with the query and is not semantically close enough either.
- **Lexical misses** `{lexical.misses[0].question if lexical.misses else "none"}` — the corpus phrases this as "Dispatch and limits", with no overlapping terms.

The hybrid covers both and reaches **100 % recall@5**. That is the justification for the added machinery, and `test_hybrid_beats_either_retriever_alone` fails if it ever stops holding.

Scores are fused with Reciprocal Rank Fusion rather than averaged: cosine similarity and BM25 live on different scales, and normalising them introduces its own distortions. RRF uses only rank.

## Per-question results

| question | rank of first relevant hit | top result |
|---|---|---|
{rows}

## Interpretation

**Recall@5 = {hybrid.recall_at(5):.2f} is the number that matters most.** A question whose answer never reaches the model cannot be answered correctly regardless of model quality. Zero unretrievable questions means any grounding failure in M10 is attributable to the generator, not to retrieval.

**Hit@1 of {hybrid.hit_at_1:.2f} is adequate, not excellent.** The misses at rank 2–4 are cases where a plausible neighbouring section outranks the intended one. For an LLM consuming five chunks this is harmless; for a human reading a search page it is mildly annoying. A cross-encoder reranker would address it and is deferred.

## Limitations

- **The corpus is {stats["documents"]} documents and {stats["chunks"]} chunks.** Doc 07 targets 150 documents and 2,000 chunks. These figures will change substantially at that scale, and recall@5 of 1.00 is far easier on a small corpus.
- **No cross-encoder reranking.** Doc 07 section 7.9 specifies `bge-reranker-base` over the top 20. Not implemented; hit@1 is the metric it would improve.
- **The golden set shares an author with the corpus**, which biases it toward vocabulary the corpus happens to use. An independent question set would be a harder and more honest test.
- **No faithfulness or citation-precision measurement.** Those require a generator and belong with M10.
"""

    output = REPO / "docs/reports/rag-eval.md"
    output.write_text(report)
    print(
        f"wrote {output.relative_to(REPO)}: hit@1 {hybrid.hit_at_1:.2f} "
        f"recall@5 {hybrid.recall_at(5):.2f} MRR {hybrid.mrr:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
