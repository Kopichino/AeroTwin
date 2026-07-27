"""Embedding index and hybrid retrieval (Doc 07 section 7.9).

Retrieval is **hybrid**: dense embeddings for semantic similarity, BM25 for
lexical overlap, fused with Reciprocal Rank Fusion.

Dense retrieval alone fails on exactly the queries this corpus attracts. A
technician searching for task `72-31-00-200-802` gets semantically similar
procedures rather than that task, because an embedding model has no notion that
a specific identifier must match exactly. BM25 alone fails the opposite way: it
misses "compressor is running hot" against a section titled "HPC efficiency
loss". Each covers the other's blind spot.

RRF is used for fusion rather than score averaging because the two systems'
scores are not comparable — cosine similarity and BM25 live on different scales,
and normalising them introduces its own distortions. RRF only uses rank.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from at_rag.chunk import Chunk

#: Small, fast, and good enough for a corpus of this size. A larger model would
#: improve recall marginally at several times the download and inference cost.
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

#: RRF damping constant. 60 is the value from the original paper and is not
#: sensitive enough to be worth tuning on a corpus this small.
RRF_K = 60

BM25_K1 = 1.5
BM25_B = 0.75

TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def tokenise(text: str) -> list[str]:
    """Lowercase word tokens, keeping hyphenated task codes intact.

    `72-31-00-200-802` must survive as one token; splitting it on hyphens would
    turn an exact identifier into five meaningless numbers.
    """
    return TOKEN.findall(text.lower())


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One retrieved chunk with its provenance and score."""

    chunk: Chunk
    score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "doc_id": self.chunk.doc_id,
            "title": self.chunk.title,
            "source_type": self.chunk.source_type,
            "section_path": self.chunk.section_path,
            "content": self.chunk.content,
            "score": round(self.score, 5),
            "dense_rank": self.dense_rank,
            "lexical_rank": self.lexical_rank,
            "task_codes": list(self.chunk.task_codes),
        }


class BM25:
    """Okapi BM25 over the chunk collection.

    Implemented directly rather than pulling in a search dependency: the corpus
    is a few hundred chunks held in memory, and the algorithm is short enough
    that a dependency would cost more than it saves.
    """

    def __init__(self, corpus: list[list[str]]) -> None:
        self.corpus = corpus
        self.length = [len(document) for document in corpus]
        self.average_length = sum(self.length) / max(1, len(corpus))
        self.frequencies = [Counter(document) for document in corpus]

        document_frequency: Counter[str] = Counter()
        for document in corpus:
            document_frequency.update(set(document))

        total = len(corpus)
        self.idf = {
            term: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

    def scores(self, query: list[str]) -> np.ndarray:
        result = np.zeros(len(self.corpus), dtype=np.float32)
        for term in query:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for index, frequencies in enumerate(self.frequencies):
                count = frequencies.get(term, 0)
                if count == 0:
                    continue
                norm = 1 - BM25_B + BM25_B * self.length[index] / self.average_length
                result[index] += idf * (count * (BM25_K1 + 1)) / (count + BM25_K1 * norm)
        return result


class KnowledgeIndex:
    """In-memory hybrid index over the knowledge corpus.

    Held in memory rather than in ChromaDB. The corpus is small enough that the
    whole embedding matrix is a few megabytes, a numpy dot product over it is
    sub-millisecond, and it removes a service from the deployment. The interface
    is deliberately narrow so a Chroma-backed implementation can replace it when
    the corpus grows past what fits comfortably in a process.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._model: Any = None
        self.chunks: list[Chunk] = []
        self.embeddings: np.ndarray | None = None
        self.bm25: BM25 | None = None

    @property
    def model(self) -> Any:
        """Lazily loaded so importing this module stays cheap."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def size(self) -> int:
        return len(self.chunks)

    @property
    def ready(self) -> bool:
        return self.embeddings is not None and self.size > 0

    def build(self, chunks: list[Chunk], *, batch_size: int = 32) -> None:
        """Embed every chunk and build the lexical index."""
        self.chunks = chunks
        if not chunks:
            self.embeddings = None
            self.bm25 = None
            return

        texts = [chunk.content for chunk in chunks]
        # Normalised embeddings turn cosine similarity into a dot product.
        self.embeddings = np.asarray(
            self.model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )
        self.bm25 = BM25([tokenise(text) for text in texts])

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_types: list[str] | None = None,
        candidates: int = 20,
    ) -> list[SearchHit]:
        """Hybrid search with reciprocal rank fusion."""
        if not self.ready or self.embeddings is None or self.bm25 is None:
            return []

        query = query.strip()
        if not query:
            return []

        allowed = self._allowed_indices(source_types)
        if not allowed:
            return []

        query_vector = np.asarray(
            self.model.encode([query], normalize_embeddings=True, show_progress_bar=False),
            dtype=np.float32,
        )[0]
        dense = self.embeddings @ query_vector
        lexical = self.bm25.scores(tokenise(query))

        dense_ranks = self._rank(dense, allowed, candidates)
        lexical_ranks = self._rank(lexical, allowed, candidates)

        fused: dict[int, float] = {}
        for index, rank in dense_ranks.items():
            fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + rank)
        for index, rank in lexical_ranks.items():
            fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + rank)

        ordered = sorted(fused.items(), key=lambda item: -item[1])[:limit]
        return [
            SearchHit(
                chunk=self.chunks[index],
                score=score,
                dense_rank=dense_ranks.get(index),
                lexical_rank=lexical_ranks.get(index),
            )
            for index, score in ordered
        ]

    def _allowed_indices(self, source_types: list[str] | None) -> set[int]:
        if not source_types:
            return set(range(self.size))
        wanted = {value.upper() for value in source_types}
        return {
            index for index, chunk in enumerate(self.chunks) if chunk.source_type.upper() in wanted
        }

    @staticmethod
    def _rank(scores: np.ndarray, allowed: set[int], limit: int) -> dict[int, int]:
        """Rank the allowed indices by score, best first, 1-based."""
        ordering = sorted(allowed, key=lambda index: -float(scores[index]))[:limit]
        return {index: rank for rank, index in enumerate(ordering, start=1)}

    def stats(self) -> dict[str, Any]:
        by_source: Counter[str] = Counter(chunk.source_type for chunk in self.chunks)
        by_document: Counter[str] = Counter(chunk.doc_id for chunk in self.chunks)
        return {
            "chunks": self.size,
            "documents": len(by_document),
            "model": self.model_name,
            "dimensions": int(self.embeddings.shape[1]) if self.embeddings is not None else 0,
            "by_source_type": dict(by_source),
        }


def build_index(corpus_root: Path, model_name: str = DEFAULT_MODEL) -> KnowledgeIndex:
    """Load, chunk and embed a corpus directory."""
    from at_rag.chunk import chunk_corpus

    _documents, chunks = chunk_corpus(corpus_root)
    index = KnowledgeIndex(model_name)
    index.build(chunks)
    return index
