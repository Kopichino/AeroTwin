"""Tests for the RFC 9457 problem-details error contract (Doc 12 section 12.9)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from at_core.errors import (
    AppError,
    EngineNotFound,
    ErrorCode,
    RateLimited,
    TwinInvalidTransition,
    ValidationError,
)

PROBLEM_JSON = "application/problem+json"


class SeekBody(BaseModel):
    """Declared at module level.

    Under PEP 563 (``from __future__ import annotations``) FastAPI resolves route
    annotations against the *module* globals. A model defined inside a fixture is
    invisible there and silently binds to ``fastapi.Body``, collapsing the request
    body into a single scalar field. Module scope is required for correct routing.
    """

    cycle: int


@pytest.fixture
def error_app(app: FastAPI) -> FastAPI:
    """Mount routes that raise each error class so the contract can be asserted."""

    @app.get("/boom/engine")
    async def _engine() -> None:
        raise EngineNotFound("Engine FD001-train-U999 does not exist.")

    @app.get("/boom/transition")
    async def _transition() -> None:
        raise TwinInvalidTransition(
            "Engine is PAUSED; SEEK requires RUNNING or PAUSED.",
        )

    @app.get("/boom/ratelimit")
    async def _ratelimit() -> None:
        raise RateLimited("Too many copilot requests.", headers={"Retry-After": "30"})

    @app.get("/boom/unhandled")
    async def _unhandled() -> None:
        raise RuntimeError("a bug that escaped")

    @app.post("/boom/validate")
    async def _validate(body: SeekBody) -> dict[str, int]:
        return {"cycle": body.cycle}

    return app


@pytest.fixture
def error_client(error_app: FastAPI) -> TestClient:
    return TestClient(error_app, raise_server_exceptions=False)


def test_not_found_shape(error_client: TestClient) -> None:
    response = error_client.get("/boom/engine")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["code"] == ErrorCode.ENGINE_NOT_FOUND.value
    assert body["type"] == "https://aerotwin.dev/errors/engine-not-found"
    assert body["instance"] == "/boom/engine"
    assert body["status"] == 404
    assert "FD001-train-U999" in body["detail"]


def test_problem_includes_trace_id_for_support(error_client: TestClient) -> None:
    response = error_client.get("/boom/engine")
    assert response.json()["trace_id"] == response.headers["X-Trace-Id"]


def test_conflict_maps_to_409(error_client: TestClient) -> None:
    response = error_client.get("/boom/transition")
    assert response.status_code == 409
    assert response.json()["code"] == ErrorCode.TWIN_INVALID_TRANSITION.value


def test_rate_limit_preserves_retry_after_header(error_client: TestClient) -> None:
    response = error_client.get("/boom/ratelimit")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


def test_unhandled_exception_does_not_leak_internals(error_client: TestClient) -> None:
    response = error_client.get("/boom/unhandled")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == ErrorCode.INTERNAL.value
    assert "a bug that escaped" not in str(body)
    assert body["trace_id"]


def test_validation_error_lists_offending_fields(error_client: TestClient) -> None:
    response = error_client.post("/boom/validate", json={"cycle": "not-an-int"})
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == ErrorCode.VALIDATION_FAILED.value
    assert body["errors"][0]["field"] == "cycle"


def test_routing_404_uses_the_problem_shape(error_client: TestClient) -> None:
    body = error_client.get("/no/such/route").json()
    assert body["status"] == 404
    assert "code" in body and "type" in body


# ── unit-level checks on the error classes themselves ────────────────────────


def test_type_uri_derives_from_code() -> None:
    assert TwinInvalidTransition().type_uri == (
        "https://aerotwin.dev/errors/twin-invalid-transition"
    )


def test_default_detail_falls_back_to_title() -> None:
    error = EngineNotFound()
    assert error.detail == error.title


def test_to_problem_omits_empty_optional_members() -> None:
    problem = AppError("boom").to_problem()
    assert "instance" not in problem
    assert "trace_id" not in problem
    assert "errors" not in problem


def test_validation_error_carries_structured_field_errors() -> None:
    problem = ValidationError(
        "bad", errors=[{"field": "speed", "message": "must be one of ..."}]
    ).to_problem()
    assert problem["errors"][0]["field"] == "speed"
