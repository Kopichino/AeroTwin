"""Shared fixtures for API tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from at_api.main import create_app
from at_config import Profile, Settings


@pytest.fixture
def settings() -> Settings:
    """Deterministic CI-profile settings, independent of the ambient environment."""
    return Settings(
        profile=Profile.CI,
        service_name="api-test",
        log_level="WARNING",
        log_json=True,
        cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
