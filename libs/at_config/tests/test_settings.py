"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from at_config import LLMProvider, Profile, Settings


def test_defaults_are_safe_for_local_dev() -> None:
    settings = Settings()
    assert settings.profile is Profile.LOCAL
    assert settings.llm_provider is LLMProvider.NONE
    assert settings.llm_enabled is False


def test_env_prefix_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AT_REPLAY_SPEED", "8")
    monkeypatch.setenv("AT_PROFILE", "docker")
    settings = Settings()
    assert settings.replay_speed == 8.0
    assert settings.is_production_like is True


def test_invalid_replay_speed_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AT_REPLAY_SPEED", "3")
    with pytest.raises(ValueError, match="replay_speed"):
        Settings()


def test_negative_shard_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AT_SHARD_INDEX", "-1")
    with pytest.raises(ValueError, match="shard_index"):
        Settings()


def test_redis_key_namespacing() -> None:
    settings = Settings()
    assert settings.redis_key("twin", "abc", "state") == "at:local:twin:abc:state"


def test_settings_are_frozen() -> None:
    """Immutability prevents a request handler mutating global config at runtime."""
    settings = Settings()
    with pytest.raises(PydanticValidationError):
        settings.api_port = 9999  # type: ignore[misc]


def test_llm_enabled_when_provider_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AT_LLM_PROVIDER", "openai")
    assert Settings().llm_enabled is True
