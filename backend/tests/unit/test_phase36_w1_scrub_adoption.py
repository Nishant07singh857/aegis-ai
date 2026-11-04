# backend/tests/unit/test_phase36_w1_scrub_adoption.py
"""Phase 36 Worker 1 — N-1 scrub adoption (Phase 35 built the scrubber,
adoption was incomplete).

Phase 35's QA verdict (ledger §5): ``app.db.error_scrub`` itself is
correct, but ``backend/app/db/base.py`` had ZERO scrub calls (raw
``{e}`` logging at 10+ sites, several with ``exc_info=True``;
``get_db_with_retry`` re-raised the raw exception),
``backend/scripts/init_db.py`` logged raw errors, and the login
helpers in ``app/main.py`` logged raw DB exceptions — which also
falsified the module comment claiming every DB exception passes
through the scrubber.

This suite proves the adoption at EACH site, with FAKE placeholders
only (``fakeuser`` / ``fakepass-TEST-ONLY`` / ``fakehost.invalid`` —
nothing real, nothing resembling a real credential). For every site
it asserts the fake password and the fake full URL appear NOWHERE:
not in the log text, not in a formatted traceback of the logged
record, and not in a re-raised exception's message or chain — while
benign driver detail ("connection refused") survives.

Coverage notes (honest scope):
* ``exc_info=True`` sites are proven via the FORMATTED record text
  (``logging.Formatter().format(record)``), because scrubbing only
  the message f-string would still leak through the traceback's
  final line — the production fix substitutes a scrubbed surrogate
  as the ``exc_info`` value (see ``app.db.base._scrubbed_exc_info``).
* ``get_db`` / ``get_db_context`` / ``init_db`` now re-raise a
  SCRUBBED surrogate (Phase 37, W1 / N-1): same type for
  SQLAlchemyError, plain scrubbed ``Exception`` for a non-SQLAlchemy
  failure in ``init_db``. The one deliberate exception is the
  generic-``Exception`` branch of ``get_db`` / ``get_db_context``,
  which catches the ENDPOINT/CALLER's own exceptions (HTTPException
  status semantics, ...) and bare re-raises them untouched — see
  the Phase 37 suite for the boundary proof.
* SQLAlchemy itself pre-redacts a bare ``password=...`` key/value
  inside ``OperationalError.__str__`` (verified in a probe: the
  ``[SQL: ...]`` rendering shows ``password=<redacted>`` while the
  embedded URL userinfo survives verbatim). The retry tests therefore
  carry the leak in the URL userinfo — the real-world driver shape —
  and the ``password=`` form is proven on the plain-``Exception``
  paths, where SQLAlchemy does not intervene.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import OperationalError, SQLAlchemyError

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


class _RecordingLogger:
    """Stand-in for a structlog logger: records rendered text."""

    def __init__(self):
        self.calls = []

    def _record(self, *args, **kwargs):
        self.calls.append(
            " ".join(str(a) for a in args)
            + " "
            + " ".join(f"{k}={v}" for k, v in kwargs.items())
        )

    def info(self, *args, **kwargs):
        self._record(*args, **kwargs)

    def warning(self, *args, **kwargs):
        self._record(*args, **kwargs)

    def error(self, *args, **kwargs):
        self._record(*args, **kwargs)

    def critical(self, *args, **kwargs):
        self._record(*args, **kwargs)

    def debug(self, *args, **kwargs):
        self._record(*args, **kwargs)

    @property
    def text(self):
        return "\n".join(self.calls)


async def _drain(gen):
    async for _ in gen:
        pass


# ══════════════════════════════════════════════════════════════════
# Section A — app.db.base: get_db / get_db_context
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def _db_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="aegis.db")
    return caplog


class TestGetDbScrub:
    @pytest.mark.asyncio
    async def test_sqlalchemy_error_log_and_traceback_scrubbed(
        self, monkeypatch, _db_logs
    ):

        raw = SQLAlchemyError(_FAKE_MSG)
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(SQLAlchemyError):
            await _drain(base_module.get_db())
        text = _formatted(_db_logs)
        _assert_no_leak(text)
        assert "connection refused" in text
        assert "Database session error" in text
        # The raw exception object is untouched (scrub builds a copy).
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_generic_error_log_and_traceback_scrubbed(
        self, monkeypatch, _db_logs
    ):

        raw = RuntimeError(_FAKE_MSG)
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(RuntimeError):
            await _drain(base_module.get_db())
        text = _formatted(_db_logs)
        _assert_no_leak(text)
        assert "Unexpected session error" in text
        assert "connection refused" in text

    @pytest.mark.asyncio
    async def test_context_manager_error_scrubbed(self, monkeypatch, _db_logs):

        raw = RuntimeError(_FAKE_MSG)
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(RuntimeError):
            async with base_module.get_db_context():
                pass
        text = _formatted(_db_logs)
        _assert_no_leak(text)
        assert "Database context error" in text
        assert "connection refused" in text

    @pytest.mark.asyncio
    async def test_success_path_logs_nothing_suspicious(
        self, monkeypatch, _db_logs
    ):

        session = _FakeSession()
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        await _drain(base_module.get_db())
        session.commit.assert_awaited_once()
        assert _db_logs.records == []


# ══════════════════════════════════════════════════════════════════
# Section B — app.db.base: get_db_with_retry (re-raise via scrubbed)
# ══════════════════════════════════════════════════════════════════


class TestGetDbWithRetryScrub:
    @pytest.mark.asyncio
    async def test_operational_error_exhaustion_reraises_scrubbed(
        self, monkeypatch, _db_logs
    ):

        raw = OperationalError(
            f"connection refused: {_FAKE_URL}", None, Exception("orig")
        )
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(OperationalError) as excinfo:
            await _drain(
                base_module.get_db_with_retry(max_retries=2, retry_delay=0)
            )
        # Retried exactly max_retries times, then stopped.
        assert session.commit.await_count == 2
        # The re-raised exception is the scrubbed surrogate: the
        # OperationalError TYPE is restored (Phase 37, W1 / N-1 —
        # Phase 36 collapsed it to plain Exception), while message
        # and chain are all clean.
        assert type(excinfo.value) is OperationalError
        _assert_no_leak(str(excinfo.value))
        assert "connection refused" in str(excinfo.value)
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None
        formatted = "".join(
            traceback.format_exception(
                excinfo.type, excinfo.value, excinfo.tb
            )
        )
        _assert_no_leak(formatted)
        # Logs: both retry warnings scrubbed, no traceback leak.
        _assert_no_leak(_formatted(_db_logs))
        assert "Database connection attempt 1/2 failed" in _formatted(_db_logs)
        assert "Database connection attempt 2/2 failed" in _formatted(_db_logs)
        # The original exception is untouched.
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_non_retryable_error_reraises_scrubbed_immediately(
        self, monkeypatch, _db_logs
    ):

        raw = ValueError(_FAKE_MSG)
        session = _FakeSession(commit_side_effect=raw)
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        with pytest.raises(Exception) as excinfo:
            await _drain(
                base_module.get_db_with_retry(max_retries=3, retry_delay=0)
            )
        # No retry on a non-retryable error.
        assert session.commit.await_count == 1
        assert type(excinfo.value) is Exception
        _assert_no_leak(str(excinfo.value))
        assert "connection refused" in str(excinfo.value)
        # W1-fix (Phase 37, QA D1): the surrogate is raised with NO
        # context at all (``_raise_clean_surrogate``) — the old
        # ``from None`` behaviour (``__suppress_context__`` True,
        # ``__context__`` still referencing the raw exception) is
        # the defect this fix removes, so the leak proof is now
        # ``__context__ is None`` itself, plus the formatted
        # traceback carrying no secret.
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None
        formatted = "".join(
            traceback.format_exception(
                excinfo.type, excinfo.value, excinfo.tb
            )
        )
        _assert_no_leak(formatted)
        _assert_no_leak(_formatted(_db_logs))
        assert "Database error (non-retryable)" in _formatted(_db_logs)

    @pytest.mark.asyncio
    async def test_transient_failure_then_success(self, monkeypatch, _db_logs):

        transient = OperationalError("connection refused", None, Exception("orig"))
        session = _FakeSession()
        session.commit = AsyncMock(side_effect=[transient, None])
        monkeypatch.setattr(
            base_module, "AsyncSessionLocal", lambda: _FakeSessionCM(session)
        )
        await _drain(base_module.get_db_with_retry(max_retries=3, retry_delay=0))
        assert session.commit.await_count == 2
        # Only the first attempt's warning; benign detail survives.
        text = _formatted(_db_logs)
        assert "Database connection attempt 1/3 failed" in text
        assert "connection refused" in text


# ══════════════════════════════════════════════════════════════════
# Section C — app.db.base: init_db / close_db
# ══════════════════════════════════════════════════════════════════


class TestInitAndCloseDbScrub:
    @pytest.mark.asyncio
    async def test_extension_failure_logged_scrubbed(self, monkeypatch, _db_logs):

        conn = SimpleNamespace(
            execute=AsyncMock(side_effect=RuntimeError(_FAKE_MSG)),
            run_sync=AsyncMock(),
        )
        engine = _FakeEngine(
            dialect_name="postgresql",
            begin_cm=[_FakeBeginCM(conn=conn), _FakeBeginCM(conn=conn)],
        )
        monkeypatch.setattr(base_module, "engine", engine)
        await base_module.init_db()  # extensions are non-fatal
        text = _formatted(_db_logs)
        _assert_no_leak(text)
        assert "Skipping PostgreSQL extensions (non-fatal)" in text
        assert "connection refused" in text

    @pytest.mark.asyncio
    async def test_create_all_failure_logged_scrubbed(self, monkeypatch, _db_logs):

        raw = RuntimeError(_FAKE_MSG)
        engine = _FakeEngine(
            dialect_name="sqlite",
            begin_cm=_FakeBeginCM(enter_side_effect=raw),
        )
        monkeypatch.setattr(base_module, "engine", engine)
        # Phase 37 (N-1): init_db no longer bare re-raises. A
        # non-SQLAlchemy failure collapses to the plain scrubbed
        # Exception (type NOT preserved — documented in base.py);
        # the message is scrubbed and the original is untouched.
        with pytest.raises(Exception) as excinfo:
            await base_module.init_db()
        assert type(excinfo.value) is Exception
        _assert_no_leak(str(excinfo.value))
        assert "connection refused" in str(excinfo.value)
        text = _formatted(_db_logs)
        _assert_no_leak(text)
        assert "Database initialization failed" in text
        assert "connection refused" in text
        # The original exception object is untouched (scrub builds
        # a copy).
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_close_db_error_scrubbed(self, monkeypatch, _db_logs):

        engine = _FakeEngine(dispose_side_effect=RuntimeError(_FAKE_MSG))
        monkeypatch.setattr(base_module, "engine", engine)
        await base_module.close_db()  # never raises
        text = _formatted(_db_logs)
        _assert_no_leak(text)
        assert "Error closing database" in text
        assert "connection refused" in text


# ══════════════════════════════════════════════════════════════════
# Section D — app.db.base: health / stats payloads
# ══════════════════════════════════════════════════════════════════


class TestHealthStatsScrub:
    @pytest.mark.asyncio
    async def test_health_check_returns_scrubbed_error(self, monkeypatch, _db_logs):

        engine = _FakeEngine(
            begin_cm=_FakeBeginCM(enter_side_effect=RuntimeError(_FAKE_MSG))
        )
        monkeypatch.setattr(base_module, "engine", engine)
        result = await base_module.check_db_health()
        assert result["status"] == "unhealthy"
        _assert_no_leak(result["error"], _formatted(_db_logs))
        assert "connection refused" in result["error"]

    @pytest.mark.asyncio
    async def test_db_stats_returns_scrubbed_error(self, monkeypatch, _db_logs):

        engine = _FakeEngine(
            begin_cm=_FakeBeginCM(enter_side_effect=RuntimeError(_FAKE_MSG))
        )
        monkeypatch.setattr(base_module, "engine", engine)
        result = await base_module.get_db_stats()
        _assert_no_leak(result["error"], _formatted(_db_logs))
        assert "connection refused" in result["error"]


# ══════════════════════════════════════════════════════════════════
# Section E — backend/scripts/init_db.py
# ══════════════════════════════════════════════════════════════════


class TestInitDbScriptScrub:
    @pytest.fixture
    def script(self):
        import sys

        if str(_BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(_BACKEND_DIR))
        import scripts.init_db as script_module

        return script_module

    @pytest.mark.asyncio
    async def test_extension_warning_scrubbed(self, script, monkeypatch):
        logger = _RecordingLogger()
        monkeypatch.setattr(script, "logger", logger)
        conn = SimpleNamespace(
            execute=AsyncMock(side_effect=RuntimeError(_FAKE_MSG))
        )
        engine = _FakeEngine(begin_cm=_FakeBeginCM(conn=conn))
        monkeypatch.setattr(script, "engine", engine)
        await script.create_extensions()
        _assert_no_leak(logger.text)
        assert "Extension uuid-ossp not available" in logger.text
        assert "connection refused" in logger.text

    @pytest.mark.asyncio
    async def test_index_warning_scrubbed(self, script, monkeypatch):
        logger = _RecordingLogger()
        monkeypatch.setattr(script, "logger", logger)
        conn = SimpleNamespace(
            execute=AsyncMock(side_effect=RuntimeError(_FAKE_MSG))
        )
        engine = _FakeEngine(begin_cm=_FakeBeginCM(conn=conn))
        monkeypatch.setattr(script, "engine", engine)
        await script.create_indexes()
        _assert_no_leak(logger.text)
        assert "Index creation warning" in logger.text
        assert "connection refused" in logger.text

    @pytest.mark.asyncio
    async def test_verify_connection_failure_scrubbed(self, script, monkeypatch):
        logger = _RecordingLogger()
        monkeypatch.setattr(script, "logger", logger)
        engine = _FakeEngine(
            begin_cm=_FakeBeginCM(enter_side_effect=RuntimeError(_FAKE_MSG))
        )
        monkeypatch.setattr(script, "engine", engine)
        assert await script.verify_connection() is False
        _assert_no_leak(logger.text)
        assert "Database connection failed" in logger.text
        assert "connection refused" in logger.text

    @pytest.mark.asyncio
    async def test_main_step_failure_scrubbed_and_exits(
        self, script, monkeypatch
    ):
        logger = _RecordingLogger()
        monkeypatch.setattr(script, "logger", logger)
        monkeypatch.setattr(
            script, "verify_connection", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            script,
            "create_extensions",
            AsyncMock(side_effect=RuntimeError(_FAKE_MSG)),
        )
        monkeypatch.setattr(script, "create_tables", AsyncMock())
        monkeypatch.setattr(script, "create_indexes", AsyncMock())
        engine = _FakeEngine()
        monkeypatch.setattr(script, "engine", engine)
        with pytest.raises(SystemExit) as excinfo:
            await script.main()
        assert excinfo.value.code == 1
        _assert_no_leak(logger.text)
        assert "Database initialization failed" in logger.text
        assert "connection refused" in logger.text
        engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_main_no_db_exits_before_steps(self, script, monkeypatch):
        logger = _RecordingLogger()
        monkeypatch.setattr(script, "logger", logger)
        monkeypatch.setattr(
            script, "verify_connection", AsyncMock(return_value=False)
        )
        create_extensions = AsyncMock()
        monkeypatch.setattr(script, "create_extensions", create_extensions)
        engine = _FakeEngine()
        monkeypatch.setattr(script, "engine", engine)
        with pytest.raises(SystemExit) as excinfo:
            await script.main()
        assert excinfo.value.code == 1
        create_extensions.assert_not_awaited()
        # Pre-existing script shape (NOT changed here): the no-DB
        # early exit happens BEFORE the try/finally, so engine.dispose()
        # is not reached on this path — only the step-failure path
        # (see the test above) runs the finally dispose.
        engine.dispose.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════
# Section F — app/main.py login helpers + corrected scope comment
# ══════════════════════════════════════════════════════════════════
# fpdf2 is absent in this VM (pre-existing gap, same as the Phase 33
# DB-boot tests). The fpdf stub comes from the Phase 33
# teardown-guarded context manager (imported, not re-implemented),
# exactly as the Phase 35 W4 file does.

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


class _BoomContext:
    async def __aenter__(self):
        raise RuntimeError(_FAKE_MSG)

    async def __aexit__(self, *exc):
        return False


class TestLoginHelperScrub:
    @pytest.mark.asyncio
    async def test_authenticate_db_user_warning_scrubbed(self, monkeypatch):
        main = _load_main_module()
        logger = _RecordingLogger()
        monkeypatch.setattr(main, "logger", logger)
        monkeypatch.setattr(main, "get_db_context", lambda: _BoomContext())
        result = await main._authenticate_db_user(
            "fakeuser@example.invalid", "irrelevant-password"
        )
        assert result is None  # fail closed, never raises
        _assert_no_leak(logger.text)
        assert "DB credential check unavailable" in logger.text
        assert "connection refused" in logger.text

    @pytest.mark.asyncio
    async def test_is_deactivated_warning_scrubbed(self, monkeypatch):
        main = _load_main_module()
        logger = _RecordingLogger()
        monkeypatch.setattr(main, "logger", logger)
        monkeypatch.setattr(main, "get_db_context", lambda: _BoomContext())
        result = await main._is_deactivated_db_user("fakeuser@example.invalid")
        assert result is False  # never raises
        _assert_no_leak(logger.text)
        assert "Deactivation check unavailable" in logger.text
        assert "connection refused" in logger.text

    def test_scope_comment_no_longer_claims_every_db_exception(self):
        source = (_BACKEND_DIR / "app" / "main.py").read_text()
        # The Phase 35 false claim is gone.
        assert "every DB exception logged/raised" not in source
        # The corrected comment names the exact scrubbed scope,
        # including both login helpers.
        assert "_authenticate_db_user, _is_deactivated_db_user" in source
        assert "That is the exact" in source
