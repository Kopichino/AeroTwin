# M9 — RAG Knowledge Base — Completion Report

**Status:** complete, awaiting approval
**Commit:** `14cc893` · 496 backend + 51 frontend tests · 12 architecture contracts kept

---

## (a) The risk I flagged did not materialise

I said I'd check the disk budget before committing to `sentence-transformers`, and say so plainly if embeddings weren't feasible here rather than ship an untested index.

Measured: wheels are **23 MB**, the MiniLM model is **~90 MB**, HuggingFace is reachable. Total footprint ~1 GB against 12 GB free. **Embeddings are real and tested.**

---

## (b) What was built

| Component | Detail |
|---|---|
| **Corpus** | 4 documents, each with a provenance header. Authored AT-9000 AMM Ch. 72, condition-monitoring SOP, paraphrased FAA AC 43.13 extract, NASA C-MAPSS damage-propagation summary. |
| **`at_rag.chunk`** | Heading-aware splitting, heading paths prepended to embedded text and carried as citations, ATA task-code extraction. |
| **`at_rag.index`** | Hybrid dense (MiniLM, 384-d) + BM25, fused with Reciprocal Rank Fusion. In-memory. |
| **`at_rag.evaluate`** | 15 golden questions; recall@k, MRR, hit@1. |
| **API** | `/knowledge/search`, `/documents`, `/chunks/{id}`, `/stats` |

---

## (c) The hybrid, justified with data

| retriever | hit@1 | recall@3 | recall@5 | MRR | unretrievable |
|---|---:|---:|---:|---:|---:|
| Dense only (MiniLM) | 0.73 | 0.87 | 0.93 | 0.802 | **1** |
| Lexical only (BM25) | 0.73 | 0.93 | 0.93 | 0.822 | **1** |
| **Hybrid (RRF)** | 0.73 | 0.93 | **1.00** | **0.828** | **0** |

They fail on *different* questions:

- **Dense misses** "why must operating condition be accounted for" — little shared vocabulary, not semantically close enough either.
- **Lexical misses** "when must an engine be grounded" — the corpus says "Dispatch and limits", zero overlapping terms.

The hybrid covers both. `test_hybrid_beats_either_retriever_alone` fails if that ever stops being true, so the added machinery has to keep earning its place.

**Recall@5 = 1.00 is the number that matters.** A question whose answer never reaches the model cannot be answered regardless of model quality — so any grounding failure in M10 will be attributable to the generator, not retrieval.

---

## (d) Two bugs found by testing

**1. Orphan chunks.** The short-section merge only looked *backwards*, so a short section at the start of a document became a standalone chunk — one was **8 characters**, pure retrieval noise. Now merges forward as well, with an all-short document still producing a chunk.

**2. Task codes missed their own section.** Codes were extracted from the body only, but a task's identifier lives in its *heading*. Every procedure was tagged with the codes it **references** but not the one it **is** — searching `72-31-00-700-804` returned the borescope task instead of the water wash it names. Chunks with codes went 4 → 12 after the fix.

Both are the kind of defect that produces plausible-looking wrong answers rather than errors, which is exactly what makes them dangerous in a RAG system.

---

## (e) Design decisions

**Heading-aware chunking, not fixed-size.** A maintenance task cut mid-procedure retrieves as two fragments that each look complete and neither of which is actionable — and the model has no way to know a step is missing.

**RRF, not score averaging.** Cosine similarity and BM25 live on different scales; normalising them introduces its own distortions. RRF uses only rank.

**In-memory, not ChromaDB.** The corpus is 30 chunks — the whole embedding matrix is a few hundred KB and a numpy dot product over it is sub-millisecond. Chroma is installed and the interface is narrow enough to swap when the corpus outgrows a process.

**Provenance on every document.** Each file states its publisher and licence, and the authored ones say plainly they are fiction and must not be used to maintain a real engine.

---

## (f) Honest gaps

- **The corpus is 4 documents / 30 chunks.** Doc 07 targets 150 documents and 2,000 chunks. **Recall@5 = 1.00 is much easier at this scale** and these figures will move at 50× the corpus size. This is the largest caveat on the whole milestone.
- **No cross-encoder reranking.** Doc 07 §7.9 specifies `bge-reranker-base`; hit@1 (0.73) is the metric it would improve.
- **The golden set shares an author with the corpus**, biasing it toward vocabulary the corpus happens to use. An independent question set would be a harder, more honest test.
- **No faithfulness or citation-precision measurement** — needs a generator, belongs with M10.
- **No `/knowledge` UI.** The API is complete and tested; the search page is not built.

---

## (g) Cumulative state

| Metric | M6 | M7 | M8 | M9 |
|---|---|---|---|---|
| Backend tests | 423 | 453 | 453 | **496** |
| Frontend tests | 29 | 29 | 51 | 51 |
| Contracts | 11 | 11 | 11 | **12** |
| API routes | 8 | 10 | 10 | **14** |

New contract: **RAG is standalone** — `at_rag` may not import `at_api`, `at_twin`, `at_ml` or `fastapi`, so the index can be built in an offline batch job and M10's agent runtime can consume retrieval through MCP without pulling in the streaming stack.

---

## (h) Next: M10 — Multi-Agent System & Copilot

The other headline feature: seven LangGraph agents, three MCP tool servers, an LLM provider abstraction with deterministic fallbacks, and the copilot UI with a live tool-trace viewer.

**Two risks to flag now.** First, `langgraph` + `langchain` is a heavy dependency tree — I'll check the budget as I did here. Second, and more important: **no LLM API key is configured in this environment.** ADR-015 anticipated this — `LLM_PROVIDER=none` routes every agent to a deterministic fallback, and NFR-10 requires the platform to work that way. So I can build and fully test the graph, the tools, the routing and the fallbacks; what I **cannot** verify here is real LLM-generated output quality. I'll be explicit in the report about which parts are executed and which need your API key.
