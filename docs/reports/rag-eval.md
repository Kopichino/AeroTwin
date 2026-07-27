# RAG Retrieval Evaluation

_Generated 2026-07-27 12:36 UTC by `at_rag.evaluate`. Regenerate with `make rag-eval`._

## Corpus

| metric | value |
|---|---|
| Documents | 4 |
| Chunks | 30 |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| Dimensions | 384 |
| By source type | AMM 13, FAA 5, NASA 6, SOP 6 |

Chunk sizes: min 219, median 489, max 1086 characters.

## Retriever comparison

Fifteen golden questions, each naming the section that should answer it.

| retriever | hit@1 | recall@3 | recall@5 | MRR | unretrievable |
|---|---:|---:|---:|---:|---:|
| Dense only (MiniLM) | 0.73 | 0.87 | 0.93 | 0.802 | 1 |
| Lexical only (BM25) | 0.73 | 0.93 | 0.93 | 0.822 | 1 |
| **Hybrid (RRF)** | **0.73** | **0.93** | **1.00** | **0.828** | **0** |

### Why hybrid, with evidence

Each single retriever leaves a *different* question unretrievable:

- **Dense misses** `why must operating condition be accounted for` — the answer shares little vocabulary with the query and is not semantically close enough either.
- **Lexical misses** `when must an engine be grounded` — the corpus phrases this as "Dispatch and limits", with no overlapping terms.

The hybrid covers both and reaches **100 % recall@5**. That is the justification for the added machinery, and `test_hybrid_beats_either_retriever_alone` fails if it ever stops holding.

Scores are fused with Reciprocal Rank Fusion rather than averaged: cosine similarity and BM25 live on different scales, and normalising them introduces its own distortions. RRF uses only rank.

## Per-question results

| question | rank of first relevant hit | top result |
|---|---|---|
| why would HPC outlet temperature be rising | **1** | Chapter 72 — Engine > 72-00-20 — Trend interpretatio |
| task 72-31-00-200-802 | **1** | Chapter 72 — Engine > 72-31-00 — High-pressure compr |
| how do I inspect the high pressure compressor with a borescope | **1** | Chapter 72 — Engine > 72-31-00 — High-pressure compr |
| what does a water wash recover | **1** | Chapter 72 — Engine > 72-31-00 — High-pressure compr |
| when must an engine be grounded | 4 | Damage Propagation Modeling for Aircraft Engine Run- |
| what health index means critical | 3 | SOP-CM-001 — Engine Condition Monitoring > 2. Daily  |
| falling coolant bleed flow on the HP turbine | **1** | Chapter 72 — Engine > 72-51-00 — High-pressure turbi |
| rising fuel flow ratio combustor | **1** | Chapter 72 — Engine > 72-41-00 — Combustor > 72-41-0 |
| how should remaining useful life predictions be used for planning | **1** | SOP-CM-001 — Engine Condition Monitoring > 6. Use of |
| what are the C-MAPSS fault modes | 2 | Damage Propagation Modeling for Aircraft Engine Run- |
| why must operating condition be accounted for | 3 | Advisory Circular 43.13 — paraphrased extract > Insp |
| does a predictive tool determine airworthiness | **1** | Advisory Circular 43.13 — paraphrased extract > Use  |
| what records must be kept after maintenance | **1** | Advisory Circular 43.13 — paraphrased extract > Reco |
| fan blade damage after bird strike | **1** | Chapter 72 — Engine > 72-61-00 — Fan > 72-61-00-200- |
| when does an anomaly alert require action | **1** | SOP-CM-001 — Engine Condition Monitoring > 4. Anomal |

## Interpretation

**Recall@5 = 1.00 is the number that matters most.** A question whose answer never reaches the model cannot be answered correctly regardless of model quality. Zero unretrievable questions means any grounding failure in M10 is attributable to the generator, not to retrieval.

**Hit@1 of 0.73 is adequate, not excellent.** The misses at rank 2–4 are cases where a plausible neighbouring section outranks the intended one. For an LLM consuming five chunks this is harmless; for a human reading a search page it is mildly annoying. A cross-encoder reranker would address it and is deferred.

## Limitations

- **The corpus is 4 documents and 30 chunks.** Doc 07 targets 150 documents and 2,000 chunks. These figures will change substantially at that scale, and recall@5 of 1.00 is far easier on a small corpus.
- **No cross-encoder reranking.** Doc 07 section 7.9 specifies `bge-reranker-base` over the top 20. Not implemented; hit@1 is the metric it would improve.
- **The golden set shares an author with the corpus**, which biases it toward vocabulary the corpus happens to use. An independent question set would be a harder and more honest test.
- **No faithfulness or citation-precision measurement.** Those require a generator and belong with M10.
