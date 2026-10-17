# backend/app/exceptions.py
"""
AEGIS AI - Shared exception hierarchy.

All backend modules raise exceptions from this hierarchy so that API
handlers, agents, and services can catch broad categories
(:class:`AegisError`) or specific ones (``except NotFoundError``)
without importing implementation details from each other.

HTTP status codes are attached to the exceptions the API layer maps
to responses; non-API code can ignore them.
"""

from typing import Optional


class AegisError(Exception):
    """
    Base class for all AEGIS AI errors.

    Carries a human-readable ``message``, an optional machine-readable
    ``code`` for clients, and an optional ``details`` payload with
    extra context (field names, IDs, limits hit, ...).
    """

    #: Default HTTP status used by API error handlers.
    status_code: int = 500
    #: Machine-readable error code, e.g. "not_found".
    error_code: str = "internal_error"

    def __init__(
        self,
        message: str = "",
        *,
        code: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message or self.error_code)
        self.message = message or self.error_code
        self.code = code or self.error_code
        self.details = details or {}

    def to_dict(self) -> dict:
        """Serialize for API error responses."""
        return {"error": self.code, "message": self.message, "details": self.details}


# ── Configuration / startup ──────────────────────────────────────────


class ConfigError(AegisError):
    """Invalid or missing configuration (env vars, settings files)."""
    status_code = 500
    error_code = "config_error"


class DependencyError(AegisError):
    """A required external dependency (DB, Redis, GPU, model file) is unavailable."""
    status_code = 503
    error_code = "dependency_unavailable"


# ── API / auth / request handling ────────────────────────────────────


class AuthError(AegisError):
    """Authentication failed (bad credentials, bad/expired token)."""
    status_code = 401
    error_code = "auth_failed"


class PermissionError(AegisError):
    """Authenticated but not allowed to perform this action."""
    status_code = 403
    error_code = "forbidden"


class NotFoundError(AegisError):
    """Requested resource (team, player, match, ...) does not exist."""
    status_code = 404
    error_code = "not_found"


class ConflictError(AegisError):
    """Request conflicts with current state (duplicate, version mismatch)."""
    status_code = 409
    error_code = "conflict"


class ValidationError(AegisError):
    """Request payload or parameters failed validation."""
    status_code = 422
    error_code = "validation_error"


class RateLimitError(AegisError):
    """Rate limit exceeded; client should back off and retry later."""
    status_code = 429
    error_code = "rate_limited"


# ── Agents / ML / vision ─────────────────────────────────────────────


class AgentError(AegisError):
    """An autonomous agent failed to complete its task."""
    status_code = 500
    error_code = "agent_error"


class VisionError(AegisError):
    """Video capture or detector pipeline failure."""
    status_code = 500
    error_code = "vision_error"


class ModelError(AegisError):
    """ML model load/inference failure (missing weights, shape mismatch)."""
    status_code = 500
    error_code = "model_error"


class BroadcastError(AegisError):
    """OBS/broadcast output failure."""
    status_code = 502
    error_code = "broadcast_error"


__all__ = [
    "AegisError",
    "ConfigError",
    "DependencyError",
    "AuthError",
    "PermissionError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "RateLimitError",
    "AgentError",
    "VisionError",
    "ModelError",
    "BroadcastError",
]
