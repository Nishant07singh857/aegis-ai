# backend/tests/unit/test_phase37_w1_n1_scrubbed_reraise.py
"""Phase 37 Worker 1 — N-1 scrubbed re-raise (finish the Phase 36 residual).

Phase 36 scrubbed every DB log line but ``get_db`` /
``get_db_context`` / ``init_db`` still bare-raised the ORIGINAL
exception, so any caller (FastAPI, the lifespan, a service) could
receive — and log, or render into a 500 — a driver message embedding
the DATABASE_URL. This suite proves the raise-site half of the
guarantee, with FAKE placeholders only (``fakeuser`` /
``fakepass-TEST-ONLY`` / ``fakehost.invalid`` — nothing real, nothing
resembling a real credential). For every site it asserts the fake
password and the fake full URL appear NOWHERE: not in the re-raised
exception's message, ``str()``, ``args``/``statement``/``params``/
``orig``, or formatted traceback/chain — while benign driver detail
("connection refused") survives and the exception TYPE is preserved
wherever SQLAlchemy's constructor shape allows it.

The guarantee, and its deliberate boundary:

* ``get_db`` / ``get_db_context`` — a ``SQLAlchemyError`` from the
  session/commit (or from the endpoint's own DB calls, which surface
  at the same yield point) is re-raised as a scrubbed surrogate of
  the SAME type. A NON-SQLAlchemy exception is the ENDPOINT/CALLER's
  own (HTTPException status semantics, validation errors,
  application bugs) and is bare re-raised UNTOUCHED — converting an
  HTTPException would turn a 404 into a 500. A DB error always
  arrives wrapped as SQLAlchemyError; an endpoint that itself
  formats a raw DB exception into its own exception is that
  endpoint's bug, and this dependency cannot scrub arbitrary
  application exceptions without breaking their types.
* ``init_db`` — every failure here is DB-originated (engine.begin /
  create_all; no endpoint body), so SQLAlchemyError keeps its type
  and anything else collapses to a plain scrubbed Exception. Both
  callers catch generic Exception (lifespan re-scrubs and raises
  RuntimeError; scripts/init_db.py exits 1).
* ``get_db_with_retry`` — Phase 36 collapsed everything to plain
  Exception; Phase 37 RESTORES the type (OperationalError /
  DisconnectionError on exhaustion, any SQLAlchemyError on the
  non-retryable path). Zero production callers, grep-verified (a
  guard test below fails loudly if one appears, so the type
  contract is re-examined before it can silently break).
* ``app.api.deps`` is_active checks (REST + WS) — the two remaining
  raw ``exc_info=True`` sites (were lines 505/741, now 515/751):
  scrubbed message + scrubbed exc_info tuple, fail closed.

Probed facts this suite relies on (SQLAlchemy 2.1.5, verified in
this VM, not assumed):

* ``OperationalError.args`` is a SINGLE formatted string, NOT
  ``(statement, params, orig)`` — args-based rebuild would misplace
  the values, so DBAPIError surrogates are rebuilt from the
  structured ``.statement`` / ``.params`` / ``.orig`` attributes.
* ``DisconnectionError`` extends ``SQLAlchemyError`` directly (NOT
  DBAPIError): no ``.statement`` / ``.params`` / ``.orig``
  attributes, takes no keyword arguments — it is rebuilt from its
  plain ``args`` tuple instead.

Phase 37 W1-fix (QA FAIL remediation, D1/D2/D3 — sections H–K):

* D1: ``raise surrogate from None`` INSIDE an ``except`` block sets
  ``__cause__ = None`` but STILL sets ``__context__`` to the raw
  credential-bearing original. The fix computes the surrogate in the
  ``except`` block but raises it AFTER the block exits, so
  ``__context__`` stays ``None`` at every raise site. Section H
  asserts ``__context__ is None`` on the exception each real raise
  site hands the caller; Section I walks the full
  ``__cause__``/``__context__`` chain recursively and asserts no
  fake credential anywhere in it.
* D2: non-``str`` bind-param values were returned untouched while
  SQLAlchemy renders params via ``repr()`` — an opaque object whose
  ``str()``/``repr()`` echoes the URL leaked it into the
  surrogate's own ``str()``. ``_scrub_params_value`` now applies
  ``_scrub_arg``'s str-comparison rule to non-str leaves (Section J).
* D3: a dict param value under a credential-named key
  (``pwd``/``password``/``api_key``/…) is redacted wholesale —
  the key IS the context the leaf scrubber previously discarded
  (Section K). A bare password under a NON-credential key is still
  the disclosed boundary (a): no context, no pattern match.
"""

from __future__ import annotations

import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    OperationalError,
    SQLAlchemyError,
)

pytestmark = pytest.mark.unit

# ── Fake placeholders ONLY (never real credentials, never real hosts) ──
_FAKE_USER = "fakeuser"
_FAKE_PASS = "fakepass-TEST-ONLY"
_FAKE_HOST = "fakehost.invalid"
_FAKE_DB = "fakedb"
_FAKE_URL = (
    f"postgresql+asyncpg://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:5432/{_FAKE_DB}"
)
_FAKE_MSG = (
    f"connection refused: server at {_FAKE_HOST} rejected auth for "
    f"{_FAKE_USER} via {_FAKE_URL} password={_FAKE_PASS}"
)
_SECRETS = (_FAKE_PASS, _FAKE_URL, f"{_FAKE_USER}:{_FAKE_PASS}")

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# Imported at module top ON PURPOSE (not lazily inside the tests):
# importing app.db.base configures stdlib logging, which would detach
# pytest's caplog handler if it happened after a caplog fixture was
# already set up. Importing here — at collection time, as the other
# suites that import Base do — keeps caplog capture intact.
from app.db import base as base_module  # noqa: E402
import app.api.deps as deps_module  # noqa: E402
from app.core.security import TokenPayload  # noqa: E402

# Reuse (N-2 pattern): _fpdf_stub_if_absent from the Phase 33 DB-boot
# suite — bare-name import is the established pattern in this suite.
from test_phase33_db_boot_fail_fast import _fpdf_stub_if_absent


def _load_main_module():
    # Skip ONLY on a missing-module import failure. Every other
    # import failure propagates and FAILS loudly.
    with _fpdf_stub_if_absent():
        try:
            import app.main as main_module
        except ImportError as exc:
            pytest.skip(f"app.main not importable here (missing dependency): {exc}")
        return main_module


def _assert_no_leak(*texts: str) -> None:
    """No fake credential in any of the given texts."""
    for text in texts:
        assert text is not None
        for secret in _SECRETS:
            assert secret not in text, secret


def _formatted(caplog) -> str:
    """Full rendered text of every captured record, tracebacks included."""
    fmt = logging.Formatter()
    return "\n".join(fmt.format(r) for r in caplog.records)


def _formatted_traceback(excinfo) -> str:
    return "".join(
        traceback.format_exception(excinfo.type, excinfo.value, excinfo.tb)
    )


# ── Fakes ─────────────────────────────────────────────────────────────


class _FakeSession:
    """Minimal async session: commit/rollback/close are controllable."""

    def __init__(self, commit_side_effect=None):
        self.commit = AsyncMock(side_effect=commit_side_effect)
        self.rollback = AsyncMock()
        self.close = AsyncMock()


class _FakeSessionCM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


class _FakeBeginCM:
    """``engine.begin()`` stand-in; enter may raise, or yield a conn."""

    def __init__(self, conn=None, enter_side_effect=None):
        self.conn = conn
        self.enter_side_effect = enter_side_effect

    async def __aenter__(self):
        if self.enter_side_effect is not None:
            raise self.enter_side_effect
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, dialect_name="sqlite", begin_cm=None, dispose_side_effect=None):
        self.dialect = SimpleNamespace(name=dialect_name)
        self._begin_cm = begin_cm
        self.dispose = AsyncMock(side_effect=dispose_side_effect)

    def begin(self):
        if isinstance(self._begin_cm, list):
            return self._begin_cm.pop(0)
        return self._begin_cm

    def connect(self):
        return self._begin_cm


async def _drain(gen):
    async for _ in gen:
        pass


def _fake_operational_error() -> OperationalError:
    """OperationalError carrying the fake URL in statement, params
    and orig — the real-world driver shape (URL userinfo survives
    SQLAlchemy's own ``password=`` pre-redaction)."""
    return OperationalError(
        f"connection refused: {_FAKE_URL}",
        {"p": _FAKE_URL, "n": 1},
        Exception(f"orig {_FAKE_URL} password={_FAKE_PASS}"),
    )


@pytest.fixture
def _db_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="aegis.db")
    caplog.set_level(logging.DEBUG, logger="aegis.api.deps")
    caplog.set_level(logging.DEBUG, logger="aegis.main")
    return caplog


# ══════════════════════════════════════════════════════════════════
# Section A — _scrubbed_surrogate: type-preserving rebuild
# ══════════════════════════════════════════════════════════════════


class TestScrubbedSurrogate:
    def test_operational_error_preserves_type_and_scrubs_everything(self):
        raw = _fake_operational_error()
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is OperationalError
        assert isinstance(surrogate, DBAPIError)
        _assert_no_leak(
            str(surrogate),
            surrogate.statement or "",
            str(surrogate.params),
            str(surrogate.orig),
        )
        assert "connection refused" in str(surrogate)
        assert "connection refused" in (surrogate.statement or "")
        # The orig is a scrubbed surrogate, not the raw exception.
        assert type(surrogate.orig) is Exception
        # The original exception object is untouched.
        assert _FAKE_PASS in str(raw)
        assert _FAKE_URL in str(raw)
        # Raised from None: no raw chain can print.
        with pytest.raises(OperationalError) as excinfo:
            raise surrogate from None
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__suppress_context__ is True
        _assert_no_leak(_formatted_traceback(excinfo))

    def test_disconnection_error_preserves_type(self):
        raw = DisconnectionError(
            f"disconnect {_FAKE_URL}", None, Exception("o")
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is DisconnectionError
        _assert_no_leak(str(surrogate))
        assert "disconnect" in str(surrogate)
        assert _FAKE_URL in str(raw)

    def test_integrity_error_hide_parameters_preserved(self):
        raw = IntegrityError(
            f"dup key {_FAKE_URL}",
            {"a": _FAKE_URL},
            Exception("o"),
            hide_parameters=True,
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is IntegrityError
        assert surrogate.hide_parameters is True
        _assert_no_leak(str(surrogate))
        assert "dup key" in str(surrogate)
        # Params stay hidden in str() AND are scrubbed regardless.
        assert "hidden due to hide_parameters=True" in str(surrogate)
        assert _FAKE_URL not in str(surrogate.params)

    def test_plain_sqlalchemy_error_preserves_type(self):
        raw = SQLAlchemyError(_FAKE_MSG)
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is SQLAlchemyError
        _assert_no_leak(str(surrogate))
        assert "connection refused" in str(surrogate)
        assert _FAKE_PASS in str(raw)

    def test_non_sqlalchemy_collapses_to_plain_exception(self):
        raw = RuntimeError(_FAKE_MSG)
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is Exception
        _assert_no_leak(str(surrogate))
        assert "connection refused" in str(surrogate)
        assert _FAKE_PASS in str(raw)

    def test_unreconstructible_sqlalchemy_error_falls_back(self):
        """A subclass whose constructor refuses the scrubbed args
        must still yield a clean plain Exception, never the raw one
        and never a constructor crash."""

        class _Weird(SQLAlchemyError):
            def __init__(self, *a, **k):
                raise TypeError("cannot construct")

        raw = SQLAlchemyError.__new__(_Weird)
        raw.args = (_FAKE_MSG,)
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is Exception
        _assert_no_leak(str(surrogate))
        assert "connection refused" in str(surrogate)

    def test_nested_params_containers_scrubbed_leaf_wise(self):
        raw = OperationalError(
            "SELECT 1",
            {"where": {"u": _FAKE_URL}, "tags": [_FAKE_URL, "ok"], "n": 1},
            Exception("o"),
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is OperationalError
        _assert_no_leak(str(surrogate), str(surrogate.params))
        assert surrogate.params["tags"][1] == "ok"
        assert surrogate.params["n"] == 1

    def test_non_exception_orig_replaced_safely(self):
        """A pathological non-exception ``orig`` (e.g. a string
        carrying the URL) is replaced by a scrubbed Exception,
        never passed through."""
        raw = OperationalError("SELECT 1", None, f"weird orig {_FAKE_URL}")
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is OperationalError
        _assert_no_leak(str(surrogate))
        assert isinstance(surrogate.orig, Exception)

    def test_opaque_arg_replaced_when_str_carries_secret(self):
        """An ``args`` element whose ``str()`` echoes the URL is
        replaced by its scrubbed string form."""

        class _Opaque:
            def __str__(self):
                return f"dbapi conn {_FAKE_URL}"

        raw = SQLAlchemyError("msg", _Opaque())
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is SQLAlchemyError
        _assert_no_leak(str(surrogate))
        assert "dbapi conn" in str(surrogate)


# ══════════════════════════════════════════════════════════════════
# Section B — app.db.base: get_db (FastAPI dependency)
# ══════════════════════════════════════════════════════════════════


class TestGetDbScrubbedReraise:
    @pytest.mark.asyncio
    async def test_commit_operational_error_reraises_same_type_scrubbed(
        self, monkeypatch, _db_logs
    ):
        raw = _fake_operational_error()
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(OperationalError) as excinfo:
            await _drain(base_module.get_db())
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "connection refused" in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        # W1-fix: the surrogate is raised with NO context at all
        # (``_raise_clean_surrogate``), so there is nothing to
        # suppress — the old ``from None`` assertion
        # (``__suppress_context__ is True``) no longer applies.
        assert excinfo.value.__context__ is None
        session.rollback.assert_awaited_once()
        _assert_no_leak(_formatted(_db_logs))
        assert "Database session error" in _formatted(_db_logs)
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_endpoint_operational_error_reraises_same_type_scrubbed(
        self, monkeypatch, _db_logs
    ):
        """An OperationalError raised by the endpoint's own DB call
        surfaces at the yield point and is scrubbed the same way."""
        raw = _fake_operational_error()
        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        gen = base_module.get_db()
        await gen.__anext__()
        try:
            with pytest.raises(OperationalError) as excinfo:
                await gen.athrow(raw)
        finally:
            await gen.aclose()
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "connection refused" in str(excinfo.value)
        session.rollback.assert_awaited_once()
        _assert_no_leak(_formatted(_db_logs))

    @pytest.mark.asyncio
    async def test_endpoint_http_exception_passes_through_untouched(
        self, monkeypatch, _db_logs
    ):
        """Boundary: an HTTPException from the endpoint body must
        reach FastAPI as the SAME object (a 404 must stay a 404)."""
        raw = HTTPException(status_code=404, detail="not here")
        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        gen = base_module.get_db()
        await gen.__anext__()
        try:
            with pytest.raises(HTTPException) as excinfo:
                await gen.athrow(raw)
        finally:
            await gen.aclose()
        assert excinfo.value is raw
        assert excinfo.value.status_code == 404
        session.rollback.assert_awaited_once()
        # The log line is still scrubbed (no secret here anyway).
        assert "Unexpected session error" in _formatted(_db_logs)

    @pytest.mark.asyncio
    async def test_endpoint_generic_error_passes_through_untouched(
        self, monkeypatch, _db_logs
    ):
        raw = RuntimeError(_FAKE_MSG)
        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        gen = base_module.get_db()
        await gen.__anext__()
        try:
            with pytest.raises(RuntimeError) as excinfo:
                await gen.athrow(raw)
        finally:
            await gen.aclose()
        assert excinfo.value is raw
        session.rollback.assert_awaited_once()
        _assert_no_leak(_formatted(_db_logs))


# ══════════════════════════════════════════════════════════════════
# Section C — app.db.base: get_db_context
# ══════════════════════════════════════════════════════════════════


class TestGetDbContextScrubbedReraise:
    @pytest.mark.asyncio
    async def test_operational_error_reraises_same_type_scrubbed(
        self, monkeypatch, _db_logs
    ):
        raw = _fake_operational_error()
        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(OperationalError) as excinfo:
            async with base_module.get_db_context():
                raise raw
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "connection refused" in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        session.rollback.assert_awaited_once()
        _assert_no_leak(_formatted(_db_logs))
        assert "Database context error" in _formatted(_db_logs)
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_http_exception_passes_through_untouched(
        self, monkeypatch, _db_logs
    ):
        raw = HTTPException(status_code=403, detail="forbidden")
        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(HTTPException) as excinfo:
            async with base_module.get_db_context():
                raise raw
        assert excinfo.value is raw
        assert excinfo.value.status_code == 403
        session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generic_error_passes_through_untouched(
        self, monkeypatch, _db_logs
    ):
        raw = ValueError("caller bug, no secret")
        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(ValueError) as excinfo:
            async with base_module.get_db_context():
                raise raw
        assert excinfo.value is raw
        session.rollback.assert_awaited_once()


# ══════════════════════════════════════════════════════════════════
# Section D — app.db.base: init_db
# ══════════════════════════════════════════════════════════════════


class TestInitDbScrubbedReraise:
    @pytest.mark.asyncio
    async def test_operational_error_reraises_same_type_scrubbed(
        self, monkeypatch, _db_logs
    ):
        raw = _fake_operational_error()
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        with pytest.raises(OperationalError) as excinfo:
            await base_module.init_db()
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "connection refused" in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        _assert_no_leak(_formatted(_db_logs))
        assert "Database initialization failed" in _formatted(_db_logs)
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_non_sqlalchemy_collapses_to_plain_scrubbed_exception(
        self, monkeypatch, _db_logs
    ):
        """init_db has no endpoint body, so a non-SQLAlchemy failure
        collapses to the plain scrubbed Exception (documented in
        base.py); the message is preserved scrubbed."""
        raw = RuntimeError(_FAKE_MSG)
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        with pytest.raises(Exception) as excinfo:
            await base_module.init_db()
        assert type(excinfo.value) is Exception
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "connection refused" in str(excinfo.value)
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_verbatim_configured_url_redacted_at_raise_site(
        self, monkeypatch, _db_logs
    ):
        """The verbatim-secret path: when the configured DATABASE_URL
        itself is the fake URL, a driver message echoing it in a
        shape no pattern recognises is still redacted — because
        ``_db_secrets()`` passes the configured URL to the scrubber.
        The bare password alone matches no pattern, so ONLY the
        verbatim entry can catch it."""
        monkeypatch.setattr(base_module.settings, "DATABASE_URL", _FAKE_PASS)
        raw = RuntimeError(f"driver said {_FAKE_PASS} and nothing else")
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        with pytest.raises(Exception) as excinfo:
            await base_module.init_db()
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "driver said" in str(excinfo.value)
        _assert_no_leak(_formatted(_db_logs))


# ══════════════════════════════════════════════════════════════════
# Section E — app.db.base: get_db_with_retry (type restored)
# ══════════════════════════════════════════════════════════════════


class TestGetDbWithRetryTypeRestored:
    @pytest.mark.asyncio
    async def test_disconnection_error_exhaustion_preserves_type(
        self, monkeypatch, _db_logs
    ):
        raw = DisconnectionError(
            f"disconnect {_FAKE_URL}", None, Exception("o")
        )
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(DisconnectionError) as excinfo:
            await _drain(
                base_module.get_db_with_retry(max_retries=2, retry_delay=0)
            )
        assert session.commit.await_count == 2
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "disconnect" in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        _assert_no_leak(_formatted(_db_logs))

    @pytest.mark.asyncio
    async def test_non_retryable_integrity_error_preserves_type(
        self, monkeypatch, _db_logs
    ):
        raw = IntegrityError(
            f"dup key {_FAKE_URL}", {"a": 1}, Exception("o")
        )
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(IntegrityError) as excinfo:
            await _drain(
                base_module.get_db_with_retry(max_retries=3, retry_delay=0)
            )
        assert session.commit.await_count == 1
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "dup key" in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        _assert_no_leak(_formatted(_db_logs))
        assert "Database error (non-retryable)" in _formatted(_db_logs)

    def test_no_production_callers_guard(self):
        """Zero production callers, grep-verified. If a caller ever
        appears in ``backend/app``, this fails loudly on purpose:
        the restored exception types are part of the contract, and
        a new caller must re-examine it instead of silently
        depending on (or breaking against) a type."""
        hits = []
        for path in sorted((_BACKEND_DIR / "app").rglob("*.py")):
            for lineno, line in enumerate(
                path.read_text().splitlines(), 1
            ):
                if (
                    "get_db_with_retry" in line
                    and "def get_db_with_retry" not in line
                ):
                    hits.append(
                        f"{path.relative_to(_BACKEND_DIR)}:{lineno}: "
                        f"{line.strip()}"
                    )
        assert hits == [], (
            "get_db_with_retry has production callers now — re-examine "
            "the restored-type contract before keeping them: "
            + "; ".join(hits)
        )


# ══════════════════════════════════════════════════════════════════
# Section F — app.api.deps: is_active checks (raw exc_info=True)
# ══════════════════════════════════════════════════════════════════


class TestDepsIsActiveScrub:
    @pytest.mark.asyncio
    async def test_authenticate_checked_is_active_failure_scrubbed(
        self, monkeypatch, _db_logs
    ):
        payload = TokenPayload(user_id="db-user-1", username="db-user-1")
        monkeypatch.setattr(
            deps_module,
            "authenticate_token_or_key",
            lambda bearer, api_key: payload,
        )
        raw = _fake_operational_error()

        async def _boom(user_id):
            raise raw

        monkeypatch.setattr(deps_module, "_db_user_is_active", _boom)
        result = await deps_module.authenticate_checked(
            HTTPAuthorizationCredentials(
                scheme="Bearer", credentials="fake-jwt"
            ),
            None,
        )
        assert result is None  # fail closed, never raises
        text = _formatted(_db_logs)
        _assert_no_leak(text)
        assert "is_active check failed" in text
        assert "failing closed" in text
        assert "connection refused" in text
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_authenticate_websocket_is_active_failure_scrubbed(
        self, monkeypatch, _db_logs
    ):
        payload = TokenPayload(user_id="db-user-1", username="db-user-1")
        monkeypatch.setattr(
            deps_module,
            "security_manager",
            SimpleNamespace(decode_token=lambda cred: payload),
        )
        raw = _fake_operational_error()

        async def _boom(user_id):
            raise raw

        monkeypatch.setattr(deps_module, "_db_user_is_active", _boom)
        result = await deps_module.authenticate_websocket(None, token="fake-jwt")
        assert result is None  # fail closed, never raises
        text = _formatted(_db_logs)
        _assert_no_leak(text)
        assert "WS is_active check failed" in text
        assert "failing closed" in text
        assert "connection refused" in text
        assert _FAKE_PASS in str(raw)


# ══════════════════════════════════════════════════════════════════
# Section G — lifespan gate with the REAL init_db re-raise
# ══════════════════════════════════════════════════════════════════


class TestLifespanGateWithRealInitDb:
    @pytest.mark.asyncio
    async def test_real_init_db_surrogate_reaches_gate_clean(
        self, monkeypatch, _db_logs
    ):
        """End to end: the REAL init_db (fake engine, real code path)
        raises the scrubbed OperationalError surrogate, the lifespan
        gate catches it and raises RuntimeError — no fake credential
        in the message, the chain, or any log line. The configured
        URL here IS the fake URL, so the verbatim-secret path is
        exercised too."""
        main = _load_main_module()
        monkeypatch.setattr(main.settings, "DATABASE_URL", _FAKE_URL)
        raw = _fake_operational_error()
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        with pytest.raises(RuntimeError) as excinfo:
            await main._init_database_or_fail_fast()
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "DATABASE_URL is configured" in str(excinfo.value)
        assert "will NOT silently fall back to in-memory state" in str(
            excinfo.value
        )
        _assert_no_leak(_formatted(_db_logs))
        assert _FAKE_PASS in str(raw)


# ══════════════════════════════════════════════════════════════════
# Section H — Phase 37 W1-fix (QA D1): __context__ must be None
# ══════════════════════════════════════════════════════════════════
#
# QA FAIL D1: ``raise surrogate from None`` INSIDE an ``except``
# block sets ``__cause__ = None`` but STILL sets ``__context__`` to
# the raw credential-bearing original — ``from None`` only clears
# the cause. 11 of 12 probe cases leaked the full original (URL +
# password in statement/params/orig) as ``surrogate.__context__``.
# The fix computes the surrogate in the ``except`` block but raises
# it AFTER the block exits, via ``_raise_clean_surrogate`` — and the
# helper (not the reordering alone) is what makes the guarantee
# hold: probed, reordering alone does NOT clear ``__context__``
# when the raw exception is thrown in via ``athrow`` DURING
# unwinding — ``sys.exception()`` is still the raw at the raise
# point after the block. That is exactly the ``@asynccontextmanager``
# shape (``get_db_context`` itself, and FastAPI's finalisation of
# ``get_db``'s endpoint-error path), while a direct ``gen.athrow``
# in normal flow and a plain async function are clean with
# reordering alone. The helper raises, catches the surrogate
# itself, clears its ``__context__``, and bare re-raises (a bare
# re-raise does not touch ``__context__``) — verified in every
# shape. Every test below drives a REAL raise site and asserts
# ``__context__ is None`` on the exception the caller receives —
# not on a surrogate raised by the test itself (which would be
# trivially context-free). Non-vacuity: all but the exhaustion case
# FAIL on the pre-fix code (the exhaustion raise at base.py:619
# already sat outside any ``except`` block, so it was clean before
# and this test pins that).


class TestScrubbedReraiseContextIsNone:
    @pytest.mark.asyncio
    async def test_get_db_context_is_none(self, monkeypatch, _db_logs):
        raw = _fake_operational_error()
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(OperationalError) as excinfo:
            await _drain(base_module.get_db())
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None
        _assert_no_leak(str(excinfo.value.__context__ or ""))

    @pytest.mark.asyncio
    async def test_get_db_endpoint_error_via_asynccontextmanager_context_is_none(
        self, monkeypatch, _db_logs
    ):
        """The FastAPI shape: the endpoint's OperationalError is
        thrown into ``get_db`` via ``athrow`` DURING unwinding by
        ``@asynccontextmanager`` — exactly how FastAPI finalises
        yield-dependencies. Reordering alone does NOT clear
        ``__context__`` here (``sys.exception()`` is still the raw
        at the raise point); only ``_raise_clean_surrogate`` does.
        Driving the bare generator with ``gen.athrow`` in normal
        flow would NOT reproduce this shape."""
        raw = _fake_operational_error()
        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        cm = asynccontextmanager(base_module.get_db)
        with pytest.raises(OperationalError) as excinfo:
            async with cm():
                raise raw
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None
        _assert_no_leak(str(excinfo.value), _formatted_traceback(excinfo))
        assert "connection refused" in str(excinfo.value)
        session.rollback.assert_awaited_once()
        _assert_no_leak(_formatted(_db_logs))
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_get_db_context_manager_context_is_none(
        self, monkeypatch, _db_logs
    ):
        raw = _fake_operational_error()
        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(OperationalError) as excinfo:
            async with base_module.get_db_context():
                raise raw
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    @pytest.mark.asyncio
    async def test_get_db_with_retry_non_retryable_context_is_none(
        self, monkeypatch, _db_logs
    ):
        raw = IntegrityError(
            f"dup key {_FAKE_URL}", {"a": 1}, Exception("o")
        )
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(IntegrityError) as excinfo:
            await _drain(
                base_module.get_db_with_retry(max_retries=3, retry_delay=0)
            )
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    @pytest.mark.asyncio
    async def test_get_db_with_retry_non_sqlalchemy_context_is_none(
        self, monkeypatch, _db_logs
    ):
        raw = RuntimeError(_FAKE_MSG)
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(Exception) as excinfo:
            await _drain(
                base_module.get_db_with_retry(max_retries=3, retry_delay=0)
            )
        assert type(excinfo.value) is Exception
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    @pytest.mark.asyncio
    async def test_get_db_with_retry_exhaustion_context_is_none(
        self, monkeypatch, _db_logs
    ):
        """The exhaustion raise already sat outside any ``except``
        block (after the retry loop), so it was clean pre-fix too —
        this pins that it stays clean."""
        raw = DisconnectionError(
            f"disconnect {_FAKE_URL}", None, Exception("o")
        )
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(DisconnectionError) as excinfo:
            await _drain(
                base_module.get_db_with_retry(max_retries=2, retry_delay=0)
            )
        assert session.commit.await_count == 2
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    @pytest.mark.asyncio
    async def test_init_db_sqlalchemy_context_is_none(
        self, monkeypatch, _db_logs
    ):
        raw = _fake_operational_error()
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        with pytest.raises(OperationalError) as excinfo:
            await base_module.init_db()
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    @pytest.mark.asyncio
    async def test_init_db_non_sqlalchemy_context_is_none(
        self, monkeypatch, _db_logs
    ):
        raw = RuntimeError(_FAKE_MSG)
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        with pytest.raises(Exception) as excinfo:
            await base_module.init_db()
        assert type(excinfo.value) is Exception
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None


# ══════════════════════════════════════════════════════════════════
# Section I — Phase 37 W1-fix (QA D1): full chain walk
# ══════════════════════════════════════════════════════════════════
#
# ``__cause__ is None`` alone does not prove the guarantee: the
# leak lived on ``__context__`` (and, when the original was itself
# chained, on ``__context__.__cause__``). Walk the whole reachable
# chain — ``__cause__`` and ``__context__``, recursively, without
# revisiting nodes — and assert no fake credential in the ``str()``,
# ``args``, ``statement``, ``params`` or ``orig`` of ANY node.


def _walk_chain(exc: BaseException):
    """Yield every exception reachable via __cause__/__context__."""
    seen: set = set()
    stack = [exc]
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        stack.append(node.__cause__)
        stack.append(node.__context__)


def _assert_chain_clean(exc: BaseException) -> None:
    """No fake credential in any node of the exception chain."""
    for node in _walk_chain(exc):
        _assert_no_leak(str(node))
        for arg in getattr(node, "args", ()):
            _assert_no_leak(str(arg))
        for attr in ("statement", "params", "orig"):
            if hasattr(node, attr):
                _assert_no_leak(str(getattr(node, attr)))


class TestFullChainWalk:
    @pytest.mark.asyncio
    async def test_get_db_chain_walk_clean(self, monkeypatch, _db_logs):
        raw = _fake_operational_error()
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(OperationalError) as excinfo:
            await _drain(base_module.get_db())
        _assert_chain_clean(excinfo.value)
        # The chain is exactly the surrogate: no cause, no context.
        # (``orig`` is an attribute, not a chain link — it is
        # checked by _assert_chain_clean's attribute sweep.)
        assert list(_walk_chain(excinfo.value)) == [excinfo.value]
        assert excinfo.value.__context__ is None
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_init_db_chain_walk_clean(self, monkeypatch, _db_logs):
        raw = _fake_operational_error()
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        with pytest.raises(OperationalError) as excinfo:
            await base_module.init_db()
        _assert_chain_clean(excinfo.value)
        assert excinfo.value.__context__ is None
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_chained_original_chain_walk_clean(
        self, monkeypatch, _db_logs
    ):
        """The original was itself chained (``raise raw from
        inner``): pre-fix the surrogate's ``__context__`` was the
        raw error AND ``__context__.__cause__`` was the inner
        credential-bearing exception. Both links must be gone."""
        inner = RuntimeError(f"inner driver detail {_FAKE_URL}")
        raw = _fake_operational_error()
        raw.__cause__ = inner
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(OperationalError) as excinfo:
            await _drain(base_module.get_db())
        _assert_chain_clean(excinfo.value)
        assert excinfo.value.__context__ is None
        assert _FAKE_URL in str(inner)

    @pytest.mark.asyncio
    async def test_lifespan_gate_chain_walk_clean(
        self, monkeypatch, _db_logs
    ):
        """End to end: the lifespan RuntimeError's ``__context__``
        is the (clean) init_db surrogate, whose own ``__context__``
        must be None — the raw error must not survive one more
        link down the chain."""
        main = _load_main_module()
        monkeypatch.setattr(main.settings, "DATABASE_URL", _FAKE_URL)
        raw = _fake_operational_error()
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        with pytest.raises(RuntimeError) as excinfo:
            await main._init_database_or_fail_fast()
        _assert_chain_clean(excinfo.value)
        assert isinstance(excinfo.value.__context__, OperationalError)
        assert excinfo.value.__context__.__context__ is None
        assert _FAKE_PASS in str(raw)


# ══════════════════════════════════════════════════════════════════
# Section J — Phase 37 W1-fix (QA D2): opaque bind params
# ══════════════════════════════════════════════════════════════════
#
# ``_scrub_params_value`` returned non-``str`` values untouched
# while SQLAlchemy renders ``params`` via ``repr()`` in ``str(exc)``
# — an opaque object whose ``str()``/``repr()`` echoes the URL
# leaked it into the surrogate's own ``str()`` and ``params``.
# The fix applies ``_scrub_arg``'s str-comparison rule to non-str
# leaves: a value is kept ONLY when its string form survives
# scrubbing unchanged, otherwise it is replaced by the scrubbed
# string. Types of clean values are preserved exactly.


class TestOpaqueParamScrub:
    def test_opaque_param_object_replaced_when_str_carries_url(self):
        """QA D2 probe shape: a bind param that is an object whose
        repr echoes the DATABASE_URL."""

        class _Opaque:
            def __str__(self):
                return f"dbapi conn {_FAKE_URL}"

            __repr__ = __str__

        raw = OperationalError(
            "SELECT 1", {"conn": _Opaque(), "n": 1, "s": "ok"}, Exception("o")
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is OperationalError
        _assert_no_leak(str(surrogate), str(surrogate.params))
        # The opaque value is replaced by its scrubbed string form.
        assert "dbapi conn" in str(surrogate.params["conn"])
        # Clean values keep their types.
        assert surrogate.params["n"] == 1
        assert type(surrogate.params["n"]) is int
        assert surrogate.params["s"] == "ok"

    def test_clean_opaque_param_preserved_by_identity(self):
        """Type preservation guard (passes pre-fix too): a clean
        opaque object is returned untouched — same object, not a
        stringified copy."""

        class _Clean:
            def __str__(self):
                return "clean conn object"

        clean = _Clean()
        raw = OperationalError(
            "SELECT 1", {"conn": clean, "n": 1, "flag": True, "v": None},
            Exception("o"),
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        assert surrogate.params["conn"] is clean
        assert surrogate.params["n"] == 1
        assert type(surrogate.params["n"]) is int
        assert surrogate.params["flag"] is True
        assert surrogate.params["v"] is None
        _assert_no_leak(str(surrogate), str(surrogate.params))

    def test_opaque_param_inside_nested_containers(self):
        """The rule applies leaf-wise inside nested params, not
        only at the top level."""

        class _Opaque:
            def __str__(self):
                return f"nested {_FAKE_URL}"

            __repr__ = __str__

        raw = OperationalError(
            "SELECT 1",
            {"tags": [_Opaque(), "ok"], "where": {"c": _Opaque()}},
            Exception("o"),
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        _assert_no_leak(str(surrogate), str(surrogate.params))
        assert surrogate.params["tags"][1] == "ok"
        assert "nested" in str(surrogate.params["tags"][0])
        assert "nested" in str(surrogate.params["where"]["c"])


# ══════════════════════════════════════════════════════════════════
# Section J — Phase 37 W1-fix2 (QA re-gate D1r): clean __str__ /
# secret __repr__ opaque params
# ══════════════════════════════════════════════════════════════════
#
# The W1-fix D2 rule checked only ``str()``, but SQLAlchemy renders
# ``params`` / ``args`` via ``repr()`` in ``str(exc)`` — an object
# with a clean ``__str__`` and a secret-bearing ``__repr__`` was
# kept untouched (identity preserved) and the full URL leaked into
# the surrogate's own ``str()``. The rule now keeps a non-``str``
# leaf ONLY when BOTH ``str()`` and ``repr()`` survive scrubbing
# unchanged, in both ``_scrub_params_value`` and ``_scrub_arg``.
# NOTE: the existing §J test pins ``__repr__ = __str__`` and CANNOT
# catch this channel — the tests below use a DIVERGENT repr on
# purpose (clean str, secret repr).


class TestOpaqueParamReprScrub:
    def test_opaque_param_clean_str_secret_repr_replaced(self):
        """QA re-gate D1r probe shape (params channel): clean
        ``__str__``, ``__repr__`` echoing the DATABASE_URL."""

        class _CleanStrSecretRepr:
            def __str__(self):
                return "clean dbapi conn object"

            def __repr__(self):
                return f"<dbapi conn {_FAKE_URL}>"

        leaky = _CleanStrSecretRepr()
        raw = OperationalError(
            "SELECT 1", {"conn": leaky, "n": 1, "s": "ok"}, Exception("o")
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is OperationalError
        _assert_no_leak(str(surrogate), str(surrogate.params))
        # The object is NOT kept: repr leaks, so it is replaced by
        # its (clean) scrubbed str form.
        assert surrogate.params["conn"] is not leaky
        assert surrogate.params["conn"] == "clean dbapi conn object"
        # Clean values keep their types.
        assert surrogate.params["n"] == 1
        assert type(surrogate.params["n"]) is int
        assert surrogate.params["s"] == "ok"

    def test_opaque_arg_clean_str_secret_repr_replaced(self):
        """Same channel on the ``_scrub_arg`` (args) side: a plain
        ``SQLAlchemyError`` arg whose repr echoes the URL."""

        class _CleanStrSecretRepr:
            def __str__(self):
                return "clean dbapi conn object"

            def __repr__(self):
                return f"<dbapi conn {_FAKE_URL}>"

        leaky = _CleanStrSecretRepr()
        raw = SQLAlchemyError("msg", leaky)
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is SQLAlchemyError
        _assert_no_leak(str(surrogate), str(surrogate.args))
        assert surrogate.args[1] is not leaky
        assert surrogate.args[1] == "clean dbapi conn object"

    def test_opaque_param_clean_str_secret_repr_nested(self):
        """The repr rule applies leaf-wise inside nested params,
        not only at the top level."""

        class _CleanStrSecretRepr:
            def __str__(self):
                return "nested clean str"

            def __repr__(self):
                return f"<nested {_FAKE_URL}>"

        raw = OperationalError(
            "SELECT 1",
            {"tags": [_CleanStrSecretRepr(), "ok"]},
            Exception("o"),
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        _assert_no_leak(str(surrogate), str(surrogate.params))
        assert surrogate.params["tags"][0] == "nested clean str"
        assert surrogate.params["tags"][1] == "ok"


# ══════════════════════════════════════════════════════════════════
# Section K — Phase 37 W1-fix (QA D3): credential-keyed params
# ══════════════════════════════════════════════════════════════════
#
# Keys and values were scrubbed independently, so a bare password
# bound under a credential-named key (``{"pwd": "<password>"}``)
# survived in ``surrogate.params`` and in ``str(surrogate)`` via
# the rendered ``[parameters: …]`` block. The fix redacts a dict
# value wholesale when its key matches a credential-key pattern
# (same pattern family as ``_CREDENTIAL_KV_RE`` in
# ``app/db/error_scrub.py``). Precondition is contrived (binding
# the DB password itself as a query param), but the fix is cheap.


class TestCredentialKeyedParamScrub:
    def test_credential_keyed_dict_values_redacted_wholesale(self):
        raw = OperationalError(
            "SELECT 1",
            {
                "pwd": _FAKE_PASS,
                "db_password": _FAKE_PASS,
                "api_key": _FAKE_PASS,
                "where": {"pwd": _FAKE_PASS, "ok": "keep"},
                "n": 1,
            },
            Exception("o"),
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        assert type(surrogate) is OperationalError
        _assert_no_leak(str(surrogate), str(surrogate.params))
        assert surrogate.params["pwd"] == "<redacted>"
        assert surrogate.params["db_password"] == "<redacted>"
        assert surrogate.params["api_key"] == "<redacted>"
        assert surrogate.params["where"]["pwd"] == "<redacted>"
        # Non-credential values are untouched.
        assert surrogate.params["where"]["ok"] == "keep"
        assert surrogate.params["n"] == 1

    def test_credential_key_variants(self):
        """Key spellings from the same family as
        ``_CREDENTIAL_KV_RE``: password/passwd/pwd/secret/token and
        prefixed forms; ``password_hash`` is NOT a credential key
        (same as the KV pattern, which needs a ``=``/``:`` after
        the key word)."""
        raw = OperationalError(
            "SELECT 1",
            {
                "password": _FAKE_PASS,
                "passwd": _FAKE_PASS,
                "secret": _FAKE_PASS,
                "token": _FAKE_PASS,
                "client_secret": _FAKE_PASS,
                "DB_PWD": _FAKE_PASS,
                "password_hash": "abc123-not-a-secret",
            },
            Exception("o"),
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        for key in (
            "password",
            "passwd",
            "secret",
            "token",
            "client_secret",
            "DB_PWD",
        ):
            assert surrogate.params[key] == "<redacted>", key
        assert surrogate.params["password_hash"] == "abc123-not-a-secret"
        _assert_no_leak(str(surrogate), str(surrogate.params))

    def test_bare_password_under_non_credential_key_is_disclosed_boundary(
        self,
    ):
        """Boundary (a), params edition: a bare password under a key
        that names no credential (``x``) matches no pattern — the
        key is the only context the scrubber has, and ``x`` gives
        it none. This is the honestly disclosed boundary, pinned
        here so a future change to it is deliberate, not silent."""
        raw = OperationalError("SELECT 1", {"x": _FAKE_PASS}, Exception("o"))
        surrogate = base_module._scrubbed_surrogate(raw)
        assert surrogate.params["x"] == _FAKE_PASS


# ══════════════════════════════════════════════════════════════════
# Section K — Phase 37 W1-fix2 (QA re-gate D2r): credential-keyed
# values nested past the depth cap
# ══════════════════════════════════════════════════════════════════
#
# The ``_depth >= 4`` cap stringified the value, and a stringified
# dict ``{'pwd': '<password>'}`` matches no scrub pattern — a
# credential-keyed value nested 5+ deep leaked the bare password,
# falsifying the ledger's "redacted wholesale at any nesting depth"
# claim. At the cap, credential-keyed values are now redacted
# (iteratively, any depth) BEFORE stringifying.
#
# Depth convention used here: the ``{"pwd": ...}`` dict's nesting
# depth counting the params dict itself as depth 1 — the convention
# of QA's re-gate probe (depths 1–4 clean, depths 5–6 leaked
# pre-fix). Depth ``d`` is processed by ``_scrub_params_value`` at
# ``_depth == d - 1``; the cap fires at ``_depth >= 4``, i.e. from
# depth 5 down. Depths 1–4 were already clean pre-fix (dict branch);
# depths 5–8 are the defect range (cap branch).


def _params_with_pwd_at(depth: int):
    """Params dict whose ``{"pwd": ...}`` dict sits at nesting
    depth ``depth`` (params dict itself = depth 1)."""
    node: dict = {"pwd": _FAKE_PASS}
    for _ in range(depth - 1):
        node = {"inner": node}
    return node


def _pwd_node(params: dict, depth: int):
    """Navigate to the ``{"pwd": ...}`` dict of a params dict
    built by :func:`_params_with_pwd_at`."""
    node = params
    for _ in range(depth - 1):
        node = node["inner"]
    return node


class TestCredentialKeyedDeepNesting:
    @pytest.mark.parametrize("depth", [1, 2, 3, 4])
    def test_credential_keyed_nesting_depths_1_to_4_still_clean(self, depth):
        """Control: depths 1–4 were already clean pre-fix (dict
        branch redacts); they stay clean and keep their shape."""
        raw = OperationalError(
            "SELECT 1", _params_with_pwd_at(depth), Exception("o")
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        _assert_no_leak(str(surrogate), str(surrogate.params))
        assert _pwd_node(surrogate.params, depth)["pwd"] == "<redacted>"

    @pytest.mark.parametrize("depth", [5, 6, 7, 8])
    def test_credential_keyed_nesting_depths_5_to_8_redacted(self, depth):
        """QA re-gate D2r: depths 5–8 leaked the bare password
        pre-fix (cap stringified the dict); after the fix the
        credential value is redacted before stringifying, so the
        stringified form carries ``'<redacted>'``, not the
        password."""
        raw = OperationalError(
            "SELECT 1", _params_with_pwd_at(depth), Exception("o")
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        _assert_no_leak(str(surrogate), str(surrogate.params))
        # The cap fires at the dict processed at _depth 4 (nesting
        # depth 5), so everything from there down survives only as
        # a string — with the credential value redacted inside it.
        node = surrogate.params
        for _ in range(4):
            node = node["inner"]
        assert isinstance(node, str)
        assert "'pwd': '<redacted>'" in node
        assert node.count("'inner'") == depth - 5
        assert _FAKE_PASS not in node

    def test_credential_keyed_inside_list_at_depth_cap(self):
        """The cap redaction also reaches dicts inside a list at
        the cap (not only plain dicts)."""
        raw = OperationalError(
            "SELECT 1",
            {"wrap": {"a": {"b": {"c": [{"pwd": _FAKE_PASS}, "ok"]}}}},
            Exception("o"),
        )
        surrogate = base_module._scrubbed_surrogate(raw)
        _assert_no_leak(str(surrogate), str(surrogate.params))
        capped = surrogate.params["wrap"]["a"]["b"]["c"]
        assert isinstance(capped, str)
        assert capped == "[{'pwd': '<redacted>'}, 'ok']"
