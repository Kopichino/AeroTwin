"""Twelve-factor configuration for every AeroTwin service.

Precedence: environment variable > .env file > default (Doc 01 section 1.8.1).
``Settings`` is never imported at module scope in application code -- it is
dependency-injected so tests can substitute a profile without monkeypatching.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    LOCAL = "local"
    DOCKER = "docker"
    CI = "ci"
    DEMO = "demo"


class LLMProvider(StrEnum):
    """Provider abstraction (ADR-015). ``NONE`` enables deterministic fallbacks."""

    OPENAI = "openai"
    GROQ = "groq"
    OLLAMA = "ollama"
    NONE = "none"


class Settings(BaseSettings):
    """Root settings object shared by api, twin-engine, inference and agent-runtime."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AT_",
        extra="ignore",
        frozen=True,
    )

    # ── identity ─────────────────────────────────────────────────────────────
    profile: Profile = Profile.LOCAL
    service_name: str = "api"
    version: str = "0.1.0"
    debug: bool = False

    # ── datastores ───────────────────────────────────────────────────────────
    postgres_dsn: PostgresDsn = Field(
        default="postgresql+asyncpg://aerotwin:aerotwin@localhost:5432/aerotwin"  # type: ignore[assignment]
    )
    redis_dsn: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[assignment]
    chroma_host: str = "localhost"
    chroma_port: int = 8003
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── http ─────────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    inference_url: str = "http://localhost:8001"
    inference_timeout_ms: int = 50

    # ── security ─────────────────────────────────────────────────────────────
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 604_800
    ws_ticket_ttl_s: int = 30
    max_ws_connections_per_user: int = 3

    # ── replay / twin ────────────────────────────────────────────────────────
    replay_speed: float = 1.0
    cycle_duration_ms: int = 1000
    shard_index: int = 0
    shard_count: int = 1
    shard_lease_ttl_ms: int = 15_000
    snapshot_every_cycles: int = 50
    publish_max_hz: float = 4.0

    # ── ml ───────────────────────────────────────────────────────────────────
    model_registry_path: str = "models/registry.json"
    explain_every_cycles: int = 10
    inference_batch_max: int = 64
    inference_batch_wait_ms: int = 8

    # ── agents ───────────────────────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.NONE
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    agent_max_tokens_per_run: int = 12_000
    agent_wall_timeout_s: int = 25
    agent_recursion_limit: int = 18
    agent_max_critic_loops: int = 2

    # ── observability ────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"

    # ── rate limits (requests per minute) ────────────────────────────────────
    rate_limit_default: int = 300
    rate_limit_copilot: int = 20
    rate_limit_simulate: int = 10

    @field_validator("shard_index")
    @classmethod
    def _validate_shard(cls, value: int, info: object) -> int:
        if value < 0:
            raise ValueError("shard_index must be non-negative")
        return value

    @field_validator("replay_speed")
    @classmethod
    def _validate_speed(cls, value: float) -> float:
        allowed = {0.5, 1, 2, 4, 8, 16, 32}
        if value not in allowed:
            raise ValueError(f"replay_speed must be one of {sorted(allowed)}")
        return value

    @property
    def is_production_like(self) -> bool:
        return self.profile in (Profile.DOCKER, Profile.DEMO)

    @property
    def llm_enabled(self) -> bool:
        """Whether a real LLM is configured. False triggers deterministic fallbacks."""
        return self.llm_provider is not LLMProvider.NONE

    def redis_key(self, *parts: str) -> str:
        """Build a namespaced Redis key: ``at:{profile}:{parts...}`` (Doc 04 section 4.3)."""
        return ":".join(("at", self.profile.value, *parts))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Call ``get_settings.cache_clear()`` in tests."""
    return Settings()
