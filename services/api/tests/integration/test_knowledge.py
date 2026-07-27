"""Tests for the knowledge search endpoints (Doc 12 section 12.8)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from at_api.main import create_app
from at_config import Profile, Settings

CORPUS = Path("data/knowledge")
corpus_present = pytest.mark.skipif(
    not (CORPUS / "manuals").is_dir(), reason="knowledge corpus not present"
)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app: FastAPI = create_app(
        Settings(
            profile=Profile.CI,
            service_name="api-test",
            log_level="ERROR",
            twin_synthetic=True,
            tick_hz=10.0,
        )
    )
    with TestClient(app) as test_client:
        yield test_client


@corpus_present
def test_search_returns_ranked_results(client: TestClient) -> None:
    body = client.get("/api/v1/knowledge/search?q=borescope inspection").json()
    assert body["count"] > 0
    scores = [result["score"] for result in body["results"]]
    assert scores == sorted(scores, reverse=True)


@corpus_present
def test_search_result_carries_citable_provenance(client: TestClient) -> None:
    """A result the UI cannot attribute to a document is not citable."""
    result = client.get("/api/v1/knowledge/search?q=water wash").json()["results"][0]
    for field in ("chunk_id", "doc_id", "title", "source_type", "section_path", "content"):
        assert result[field], f"missing {field}"


@corpus_present
def test_exact_task_code_search(client: TestClient) -> None:
    body = client.get("/api/v1/knowledge/search?q=72-31-00-200-802").json()
    assert "72-31-00-200-802" in body["results"][0]["content"]


@corpus_present
def test_source_type_filter(client: TestClient) -> None:
    body = client.get("/api/v1/knowledge/search?q=engine&source_type=NASA").json()
    assert body["count"] > 0
    assert all(result["source_type"] == "NASA" for result in body["results"])


@corpus_present
def test_limit_is_respected(client: TestClient) -> None:
    assert client.get("/api/v1/knowledge/search?q=engine&limit=2").json()["count"] <= 2


def test_short_query_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/knowledge/search?q=a")
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_FAILED"


def test_missing_query_is_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/knowledge/search").status_code == 400


@corpus_present
def test_documents_lists_the_corpus(client: TestClient) -> None:
    body = client.get("/api/v1/knowledge/documents").json()
    assert body["count"] >= 4
    assert {document["source_type"] for document in body["documents"]} >= {"AMM", "SOP"}


@corpus_present
def test_chunk_can_be_resolved_from_a_search_result(client: TestClient) -> None:
    """Citation resolution: a chunk id shown in the UI must fetch its source."""
    hit = client.get("/api/v1/knowledge/search?q=grounded").json()["results"][0]
    chunk = client.get(f"/api/v1/knowledge/chunks/{hit['chunk_id']}").json()
    assert chunk["chunk_id"] == hit["chunk_id"]
    assert chunk["content"] == hit["content"]


@corpus_present
def test_unknown_chunk_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/knowledge/chunks/NOPE-999").status_code == 404


@corpus_present
def test_stats_report_the_index(client: TestClient) -> None:
    stats = client.get("/api/v1/knowledge/stats").json()
    assert stats["chunks"] > 20
    assert stats["dimensions"] == 384
