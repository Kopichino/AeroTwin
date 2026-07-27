"""Tests for corpus chunking, hybrid retrieval and evaluation (Doc 07 section 7.9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from at_rag.chunk import (
    MIN_CHARS,
    TARGET_CHARS,
    Chunk,
    chunk_corpus,
    chunk_document,
    load_document,
    parse_frontmatter,
)
from at_rag.index import BM25, KnowledgeIndex, tokenise

CORPUS = Path("data/knowledge")
corpus_present = pytest.mark.skipif(
    not (CORPUS / "manuals").is_dir(), reason="knowledge corpus not present"
)

# Building the index downloads and runs an embedding model, so it is built once
# per session rather than per test.
embeddings = pytest.mark.skipif(
    not (CORPUS / "manuals").is_dir(), reason="knowledge corpus not present"
)


# ── frontmatter ──────────────────────────────────────────────────────────────


def test_frontmatter_is_parsed_and_stripped() -> None:
    meta, body = parse_frontmatter("---\ndoc_id: X-1\ntitle: Test\n---\n# Heading\ntext")
    assert meta["doc_id"] == "X-1"
    assert meta["title"] == "Test"
    assert body.startswith("# Heading")


def test_document_without_frontmatter_is_accepted() -> None:
    meta, body = parse_frontmatter("# Heading\ntext")
    assert meta == {}
    assert body.startswith("# Heading")


def test_frontmatter_tolerates_malformed_lines() -> None:
    meta, _ = parse_frontmatter("---\nvalid: yes\nnot a pair\n---\nbody")
    assert meta == {"valid": "yes"}


# ── chunking ─────────────────────────────────────────────────────────────────


def build_document(body: str) -> Chunk:
    from at_rag.chunk import Document

    return Document(
        doc_id="TEST",
        title="Test Document",
        source_type="AMM",
        publisher="test",
        path="test.md",
        body=body,
    )


def test_chunks_split_on_headings() -> None:
    chunks = chunk_document(
        build_document("# A\n\n" + "alpha " * 40 + "\n\n# B\n\n" + "beta " * 40)
    )
    assert len(chunks) == 2
    assert "alpha" in chunks[0].content
    assert "beta" in chunks[1].content


def test_section_path_records_the_heading_hierarchy() -> None:
    """A chunk reading '1. Retrieve the last 50 cycles...' is meaningless
    without knowing which task it belongs to."""
    body = "# Chapter 72\n\n## 72-31-00 HPC\n\n### Task 801\n\n" + "step " * 40
    chunks = chunk_document(build_document(body))
    deepest = chunks[-1]
    assert "Chapter 72" in deepest.section_path
    assert "72-31-00 HPC" in deepest.section_path
    assert "Task 801" in deepest.section_path


def test_heading_path_is_prepended_to_the_embedded_text() -> None:
    body = "# Engine\n\n## HPC borescope\n\n" + "inspect the stages " * 12
    chunk = chunk_document(build_document(body))[-1]
    assert chunk.content.startswith("Engine > HPC borescope")


def test_short_sections_are_merged_rather_than_emitted_alone() -> None:
    body = "# A\n\nshort\n\n# B\n\n" + "long " * 60
    chunks = chunk_document(build_document(body))
    assert all(chunk.char_count >= MIN_CHARS or chunk is chunks[-1] for chunk in chunks)


def test_oversized_sections_are_split() -> None:
    body = "# Big\n\n" + "\n\n".join("paragraph " * 40 for _ in range(12))
    chunks = chunk_document(build_document(body))
    assert len(chunks) > 1
    assert all(chunk.char_count < TARGET_CHARS * 1.6 for chunk in chunks)


def test_split_sections_share_the_same_heading_path() -> None:
    body = "# Big\n\n" + "\n\n".join("paragraph " * 40 for _ in range(10))
    paths = {chunk.section_path for chunk in chunk_document(build_document(body))}
    assert len(paths) == 1


def test_document_without_headings_still_chunks() -> None:
    chunks = chunk_document(build_document("plain text with no headings at all"))
    assert len(chunks) == 1


def test_chunk_ids_are_unique_and_stable() -> None:
    document = build_document("# A\n\n" + "x " * 60 + "\n\n# B\n\n" + "y " * 60)
    first = [chunk.chunk_id for chunk in chunk_document(document)]
    second = [chunk.chunk_id for chunk in chunk_document(document)]
    assert first == second
    assert len(set(first)) == len(first)


def test_task_codes_are_extracted() -> None:
    body = "# Task\n\nPerform 72-31-00-200-802 and then 72-00-10 as required. " * 4
    chunk = chunk_document(build_document(body))[0]
    assert "72-31-00-200-802" in chunk.task_codes
    assert "72-00-10" in chunk.task_codes


def test_task_code_in_the_heading_is_extracted() -> None:
    """Regression: codes were extracted from the body only.

    A task's own identifier lives in its heading, so every procedure was tagged
    with the codes it *references* but not the one it *is*. Searching for
    72-31-00-700-804 returned the borescope task instead of the water wash.
    """
    body = "# 72-31-00-700-804 — HPC performance restoration\n\n" + "wash the compressor " * 12
    chunk = chunk_document(build_document(body))[0]
    assert "72-31-00-700-804" in chunk.task_codes


def test_metadata_is_flat_for_the_vector_store() -> None:
    body = "# Task\n\nSee 72-31-00-200-802 for details. " * 8
    metadata = chunk_document(build_document(body))[0].to_metadata()
    assert all(isinstance(value, str | int | float | bool) for value in metadata.values())


# ── tokenisation ─────────────────────────────────────────────────────────────


def test_task_codes_survive_tokenisation() -> None:
    """Splitting a task code on hyphens turns an exact identifier into five
    meaningless numbers, and lexical search stops working for it."""
    assert "72-31-00-200-802" in tokenise("perform task 72-31-00-200-802 now")


def test_tokenisation_is_case_insensitive() -> None:
    assert tokenise("HPC Borescope") == ["hpc", "borescope"]


# ── BM25 ─────────────────────────────────────────────────────────────────────


def test_bm25_ranks_the_matching_document_first() -> None:
    corpus = [
        tokenise("the high pressure compressor efficiency has fallen"),
        tokenise("fan blade inspection after bird strike"),
        tokenise("combustor fuel nozzle cleaning procedure"),
    ]
    scores = BM25(corpus).scores(tokenise("compressor efficiency"))
    assert scores.argmax() == 0


def test_bm25_returns_zero_for_unknown_terms() -> None:
    scores = BM25([tokenise("alpha beta")]).scores(tokenise("gamma"))
    assert float(scores[0]) == 0.0


def test_bm25_does_not_reward_pure_length() -> None:
    """Length normalisation stops a long document winning by repetition alone."""
    corpus = [tokenise("compressor"), tokenise("compressor " + "filler " * 200)]
    scores = BM25(corpus).scores(tokenise("compressor"))
    assert scores[0] > scores[1]


# ── index behaviour ──────────────────────────────────────────────────────────


def test_empty_index_returns_no_results() -> None:
    index = KnowledgeIndex()
    index.build([])
    assert not index.ready
    assert index.search("anything") == []


@pytest.fixture(scope="module")
def live_index() -> KnowledgeIndex:
    from at_rag.index import build_index

    if not (CORPUS / "manuals").is_dir():
        pytest.skip("knowledge corpus not present")
    return build_index(CORPUS)


@corpus_present
def test_corpus_loads_every_source_type() -> None:
    documents, chunks = chunk_corpus(CORPUS)
    assert len(documents) >= 4
    assert {document.source_type for document in documents} >= {"AMM", "SOP", "FAA", "NASA"}
    assert len(chunks) > 20


@corpus_present
def test_every_document_declares_provenance() -> None:
    """Corpus documents must state where they came from and how they are
    licensed, so a reader can tell authored fiction from real guidance."""
    for path in sorted(CORPUS.rglob("*.md")):
        document = load_document(path)
        assert document.license, f"{path.name} has no license field"
        assert document.publisher != "unknown", f"{path.name} has no publisher"


@embeddings
def test_index_builds_with_expected_dimensions(live_index: KnowledgeIndex) -> None:
    stats = live_index.stats()
    assert stats["chunks"] > 20
    assert stats["dimensions"] == 384


@embeddings
def test_exact_task_code_query_ranks_first(live_index: KnowledgeIndex) -> None:
    """The query dense retrieval alone handles badly."""
    hits = live_index.search("72-31-00-200-802", limit=3)
    assert hits
    assert "72-31-00-200-802" in hits[0].chunk.content


@embeddings
def test_semantic_query_finds_the_right_section(live_index: KnowledgeIndex) -> None:
    """The query lexical retrieval alone handles badly: no shared vocabulary."""
    hits = live_index.search("why would exhaust gas temperature rise", limit=3)
    assert any("Trend interpretation" in hit.chunk.section_path for hit in hits)


@embeddings
def test_source_type_filter_is_respected(live_index: KnowledgeIndex) -> None:
    hits = live_index.search("engine", limit=5, source_types=["NASA"])
    assert hits
    assert all(hit.chunk.source_type == "NASA" for hit in hits)


@embeddings
def test_unknown_source_filter_returns_nothing(live_index: KnowledgeIndex) -> None:
    assert live_index.search("engine", source_types=["NONEXISTENT"]) == []


@embeddings
def test_blank_query_returns_nothing(live_index: KnowledgeIndex) -> None:
    assert live_index.search("   ") == []


@embeddings
def test_results_are_ordered_by_score(live_index: KnowledgeIndex) -> None:
    scores = [hit.score for hit in live_index.search("compressor maintenance", limit=5)]
    assert scores == sorted(scores, reverse=True)


@embeddings
def test_hits_serialise_for_the_api(live_index: KnowledgeIndex) -> None:
    payload = live_index.search("borescope", limit=1)[0].to_dict()
    for field in ("chunk_id", "doc_id", "title", "section_path", "content", "score"):
        assert field in payload


# ── retrieval quality ────────────────────────────────────────────────────────


@embeddings
def test_golden_set_recall_is_complete(live_index: KnowledgeIndex) -> None:
    """Every golden question must have its answer somewhere in the top 5.

    A miss here means the model cannot answer correctly no matter how good it
    is, because the evidence never reaches it.
    """
    from at_rag.evaluate import evaluate

    report = evaluate(live_index)
    assert report.recall_at(5) == 1.0, f"unretrievable: {report.to_dict()['misses']}"


@embeddings
def test_golden_set_ranking_is_good(live_index: KnowledgeIndex) -> None:
    from at_rag.evaluate import evaluate

    report = evaluate(live_index)
    assert report.mrr >= 0.70, f"MRR {report.mrr:.3f} too low"
    assert report.hit_at_1 >= 0.60, f"hit@1 {report.hit_at_1:.3f} too low"


@embeddings
def test_hybrid_beats_either_retriever_alone(live_index: KnowledgeIndex) -> None:
    """Justifies the hybrid's complexity with a measurement.

    Dense and lexical retrieval each miss a *different* golden question
    entirely. If the hybrid ever stops covering both, the extra machinery is no
    longer paying for itself and this test should fail loudly.
    """
    import numpy as np
    from at_rag.evaluate import GOLDEN_SET, _matches
    from at_rag.index import SearchHit

    def recall(mode: str) -> float:
        found = 0
        for question in GOLDEN_SET:
            allowed = live_index._allowed_indices(
                list(question.source_types) if question.source_types else None
            )
            if mode == "dense":
                vector = np.asarray(
                    live_index.model.encode(
                        [question.question], normalize_embeddings=True, show_progress_bar=False
                    ),
                    dtype=np.float32,
                )[0]
                scores = live_index.embeddings @ vector  # type: ignore[operator]
            else:
                assert live_index.bm25 is not None
                scores = live_index.bm25.scores(tokenise(question.question))

            order = sorted(allowed, key=lambda index: -float(scores[index]))[:5]
            hits = [SearchHit(chunk=live_index.chunks[index], score=0.0) for index in order]
            if any(
                _matches(hit.chunk.section_path, hit.chunk.content, question.expected_sections)
                for hit in hits
            ):
                found += 1
        return found / len(GOLDEN_SET)

    from at_rag.evaluate import evaluate

    hybrid = evaluate(live_index).recall_at(5)
    assert hybrid >= recall("dense")
    assert hybrid >= recall("lexical")
    assert hybrid == 1.0
