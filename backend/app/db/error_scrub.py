# backend/app/db/error_scrub.py
"""Credential scrubbing for database error strings (Phase 35, W4 / M-5).

A database driver error can embed the connection URL — and with it the
database username/password — in its message (SQLAlchemy URL-parse errors
and asyncpg/psycopg connection errors both do this in some shapes). The
Phase 33 boot gate logged the raw exception, so a credential *could*
reach a log line or a raised ``RuntimeError``. The verification report
found zero occurrences in probes but no structural guarantee.

This module is that guarantee's choke point: it is stdlib-only (no app
imports, so ``app.db.base`` can adopt it without an import cycle) and
every boundary that logs or raises a database error MUST pass the error
through :func:`scrub_db_error_text` (or raise via
:func:`scrubbed_exception`) first. Formatting a raw DB exception into a
log call, an HTTP/WS payload, or an exception message anywhere is a bug,
even when today's driver message happens not to contain a secret.

What is redacted (patterns, not one string-replace):

* a ``*_URL`` assignment (``DATABASE_URL=postgresql://...``) — the whole
  value, because the URL itself embeds the password;
* the userinfo of any ``scheme://user:password@host`` URL, including
  URLs quoted inside a longer driver message;
* ``key=value`` / ``key: value`` credential assignments (``password``,
  ``passwd``, ``pwd``, ``secret``, ``api_key``, ``*_token``,
  ``client_secret`` and ``<prefix>_password`` / ``<prefix>_secret``
  spellings), quoted or bare;
* JWT-shaped tokens, ``Bearer <token>`` values, and known secret-token
  shapes (``sk-…``, ``ghp_…``, ``xoxb-…``, ``AKIA…``);
* any verbatim *known secret* passed in via ``secrets=`` (callers pass
  the configured URL itself, so even a driver message that echoes the
  URL in a shape no pattern above recognises is still redacted).

Everything else is preserved verbatim — driver detail such as
"connection refused" survives scrubbing, because operators need it.
Scrubbing is idempotent, and the scrubber itself never raises: if the
input cannot even be stringified, the fail-closed fallback replaces the
whole text rather than leaking it.
"""

from __future__ import annotations

import re
from typing import Iterable

__all__ = ["scrub_db_error_text", "scrub_exception", "scrubbed_exception"]

_REDACTED = "<redacted>"
_REDACTED_TOKEN = "<redacted-token>"
_FALLBACK = "<redacted db error>"
_MAX_TEXT_LEN = 100_000

# --- Patterns (compiled once; order of application matters, see below) ---

# DATABASE_URL=postgresql://... / REDIS_URL=... — whole value redacted.
_URL_ASSIGNMENT_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*URL)\s*[:=]\s*['\"]?"
    r"[^\s'\",;)\]]+",
    re.IGNORECASE,
)

# scheme://userinfo@host — the userinfo (user and/or password) is
# redacted; scheme, host, port, path and query survive.
_URL_USERINFO_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*)://[^/\s?#]+@"
)

# password=... / secret: '...' / db_password "..." — key (with any
# prefix, e.g. DB_PASSWORD) plus separator, value redacted.
_CREDENTIAL_KV_RE = re.compile(
    r"(?P<key>\b[A-Za-z0-9_.\-]*?(?:password|passwd|pwd|secret|token"
    r"|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|bearer))"
    r"\s*[:=]\s*['\"]?[^\s'\",;)\]]+",
    re.IGNORECASE,
)

# password 'value' — space-separated quoted form (driver phrasing).
_CREDENTIAL_QUOTED_RE = re.compile(
    r"(?P<key>\b(?:password|passwd))\s+['\"][^'\"]+['\"]",
    re.IGNORECASE,
)

# JWT: eyJ... . ... . ...
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")

# Bearer <token value>
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")

# Known secret-token shapes (see the verification secret scan).
_SECRET_SHAPE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{8,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{8,}|AKIA[A-Z0-9]{16})\b"
)


def _as_text(value: object) -> str:
    """Stringify *value* without ever raising."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - stringification itself failed
        return _FALLBACK


def scrub_db_error_text(value: object, *, secrets: Iterable[str] = ()) -> str:
    """Return *value* (an exception or a string) with credentials redacted.

    ``secrets`` is an optional iterable of known secret strings (e.g. the
    configured ``DATABASE_URL``) redacted verbatim wherever they occur.
    Short/blank entries are ignored so a caller can pass a possibly-empty
    configured value safely. The result is safe to log or embed in a
    raised exception; the input object is never mutated.
    """
    try:
        text = _as_text(value)
        if len(text) > _MAX_TEXT_LEN:
            text = text[:_MAX_TEXT_LEN] + "…[truncated]"
        # 1. Whole-URL assignments first: a DATABASE_URL value must go
        #    entirely, not just its userinfo.
        text = _URL_ASSIGNMENT_RE.sub(
            lambda m: f"{m.group('key')}={_REDACTED}", text
        )
        # 2. Any remaining URL with embedded credentials: redact userinfo.
        text = _URL_USERINFO_RE.sub(
            lambda m: f"{m.group('scheme')}://{_REDACTED}@", text
        )
        # 3. Credential key/value assignments, then quoted forms.
        text = _CREDENTIAL_KV_RE.sub(
            lambda m: f"{m.group('key')}={_REDACTED}", text
        )
        text = _CREDENTIAL_QUOTED_RE.sub(
            lambda m: f"{m.group('key')} '{_REDACTED}'", text
        )
        # 4. Token shapes.
        text = _JWT_RE.sub(_REDACTED_TOKEN, text)
        text = _BEARER_RE.sub(f"Bearer {_REDACTED_TOKEN}", text)
        text = _SECRET_SHAPE_RE.sub(_REDACTED, text)
        # 5. Verbatim known secrets, longest first so a URL containing a
        #    password is redacted as a whole before its parts.
        known = sorted(
            {s for s in secrets if isinstance(s, str) and len(s.strip()) >= 8},
            key=len,
            reverse=True,
        )
        for secret in known:
            if secret in text:
                text = text.replace(secret, _REDACTED)
        return text
    except Exception:  # noqa: BLE001 - scrubber must never raise
        return _FALLBACK


def scrub_exception(exc: BaseException, *, secrets: Iterable[str] = ()) -> str:
    """Scrubbed ``str(exc)``; the exception class name is kept when the
    message itself is empty, so the scrubbed text is never blank."""
    text = scrub_db_error_text(exc, secrets=secrets)
    if not text.strip():
        return f"{type(exc).__name__} (no detail)"
    return text


def scrubbed_exception(
    exc: BaseException, *, secrets: Iterable[str] = ()
) -> Exception:
    """A NEW exception carrying only the scrubbed message of *exc*.

    Use as the ``__cause__`` when re-raising (``raise RuntimeError(...)
    from scrubbed_exception(e)``): chaining to the ORIGINAL exception
    would leak its raw message into any traceback that prints the chain,
    defeating the scrub. The original exception is never mutated.
    """
    return Exception(scrub_exception(exc, secrets=secrets))
