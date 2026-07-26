"""Tests for the health probes and the application shell."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_liveness_reports_service_identity(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "api-test"
    assert body["uptime_s"] >= 0


def test_readiness_enumerates_every_dependency(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    names = {dep["name"] for dep in body["dependencies"]}
    assert names == {"postgres", "redis", "inference"}


def test_readiness_does_not_fake_unwired_dependencies(client: TestClient) -> None:
    """M1 must report 'skipped', never 'ok', for datastores that are not yet wired."""
    body = client.get("/health/ready").json()
    for dep in body["dependencies"]:
        assert dep["status"] == "skipped"
        assert "wired in M" in dep["detail"]


def test_deep_health_never_leaks_secrets(client: TestClient) -> None:
    body = client.get("/health/deep").json()
    serialised = str(body)
    assert "dev-only-secret" not in serialised
    assert body["agents"]["api_key_configured"] is False
    assert "llm_api_key" not in serialised


def test_deep_health_exposes_runtime_configuration(client: TestClient) -> None:
    body = client.get("/health/deep").json()
    assert body["replay"]["shard"] == "0/1"
    assert body["profile"] == "ci"
    assert body["milestone"] == "M1"


def test_root_points_at_the_docs(client: TestClient) -> None:
    body = client.get("/").json()
    assert body["docs"] == "/docs"


# ── cross-cutting middleware behaviour ───────────────────────────────────────


def test_every_response_carries_a_trace_id(client: TestClient) -> None:
    response = client.get("/health/live")
    trace = response.headers.get("X-Trace-Id")
    assert trace and len(trace) == 32


def test_incoming_trace_id_is_propagated_not_replaced(client: TestClient) -> None:
    """Distributed tracing requires honouring an upstream trace id."""
    supplied = "a" * 32
    response = client.get("/health/live", headers={"X-Trace-Id": supplied})
    assert response.headers["X-Trace-Id"] == supplied


def test_trace_ids_are_unique_per_request(client: TestClient) -> None:
    first = client.get("/health/live").headers["X-Trace-Id"]
    second = client.get("/health/live").headers["X-Trace-Id"]
    assert first != second


def test_server_timing_header_is_emitted(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.headers["Server-Timing"].startswith("app;dur=")


def test_cors_preflight_allows_the_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/health/live",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
