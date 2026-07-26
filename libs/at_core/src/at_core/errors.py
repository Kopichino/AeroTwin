"""Application error hierarchy mapped to RFC 9457 Problem Details.

Every error carries a stable machine-readable ``code`` (enumerated in Doc 12
section 12.9) that the frontend maps to user-facing copy and a recovery action.
Raising these from any layer produces a correct HTTP response via the
``problem_details`` exception handler -- routers never build error responses by hand.
"""

# ruff: noqa: N818
# N818 (exception names must end in "Error") is intentionally disabled for this
# module. These classes model *domain* failures and are named to read naturally at
# the raise site -- `raise EngineNotFound(...)` and `raise RateLimited(...)` are
# clearer than the suffixed forms, and the shared `AppError` base already makes the
# hierarchy obvious.

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable error codes. Renaming a member is a breaking API change."""

    VALIDATION_FAILED = "VALIDATION_FAILED"
    INVALID_SCENARIO = "INVALID_SCENARIO"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN_ROLE = "FORBIDDEN_ROLE"
    ENGINE_NOT_FOUND = "ENGINE_NOT_FOUND"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    TWIN_INVALID_TRANSITION = "TWIN_INVALID_TRANSITION"
    WORK_PACKAGE_STATE_CONFLICT = "WORK_PACKAGE_STATE_CONFLICT"
    CYCLE_OUT_OF_RANGE = "CYCLE_OUT_OF_RANGE"
    RATE_LIMITED = "RATE_LIMITED"
    INFERENCE_UNAVAILABLE = "INFERENCE_UNAVAILABLE"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    INTERNAL = "INTERNAL"


ERROR_BASE_URI = "https://aerotwin.dev/errors"


class AppError(Exception):
    """Base class for all deliberate application errors."""

    status: int = 500
    code: ErrorCode = ErrorCode.INTERNAL
    title: str = "Internal server error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.detail = detail or self.title
        self.errors = errors or []
        self.headers = headers or {}
        super().__init__(self.detail)

    @property
    def type_uri(self) -> str:
        """RFC 9457 ``type`` member, derived from the code."""
        return f"{ERROR_BASE_URI}/{self.code.value.lower().replace('_', '-')}"

    def to_problem(
        self, instance: str | None = None, trace_id: str | None = None
    ) -> dict[str, Any]:
        """Render as an RFC 9457 problem document (Doc 05 section 5.6)."""
        problem: dict[str, Any] = {
            "type": self.type_uri,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code.value,
        }
        if instance:
            problem["instance"] = instance
        if trace_id:
            problem["trace_id"] = trace_id
        if self.errors:
            problem["errors"] = self.errors
        return problem


class ValidationError(AppError):
    status = 400
    code = ErrorCode.VALIDATION_FAILED
    title = "Request validation failed"


class Unauthenticated(AppError):
    status = 401
    code = ErrorCode.UNAUTHENTICATED
    title = "Authentication required"


class ForbiddenRole(AppError):
    status = 403
    code = ErrorCode.FORBIDDEN_ROLE
    title = "Insufficient role"


class NotFound(AppError):
    status = 404
    code = ErrorCode.ENGINE_NOT_FOUND
    title = "Resource not found"


class EngineNotFound(NotFound):
    code = ErrorCode.ENGINE_NOT_FOUND
    title = "Engine not found"


class RunNotFound(NotFound):
    code = ErrorCode.RUN_NOT_FOUND
    title = "Agent run not found"


class TwinInvalidTransition(AppError):
    status = 409
    code = ErrorCode.TWIN_INVALID_TRANSITION
    title = "Twin is not in a state that accepts this command"


class CycleOutOfRange(AppError):
    status = 422
    code = ErrorCode.CYCLE_OUT_OF_RANGE
    title = "Requested cycle is outside the engine trajectory"


class RateLimited(AppError):
    status = 429
    code = ErrorCode.RATE_LIMITED
    title = "Rate limit exceeded"


class InferenceUnavailable(AppError):
    status = 503
    code = ErrorCode.INFERENCE_UNAVAILABLE
    title = "Prediction service unavailable"


class LLMUnavailable(AppError):
    status = 503
    code = ErrorCode.LLM_UNAVAILABLE
    title = "Language model provider unavailable"


class AgentTimeout(AppError):
    status = 504
    code = ErrorCode.AGENT_TIMEOUT
    title = "Agent run exceeded its time budget"
