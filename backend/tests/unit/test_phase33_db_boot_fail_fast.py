# backend/tests/unit/test_phase33_db_boot_fail_fast.py
"""Phase 33 Worker 3 — DB boot failure loud fail-fast (K-H6).

The old lifespan code in ``app/main.py`` was::

    try:
        await init_db()
    except Exception as e:
        logger.warning("... continuing with in-memory state")

so an app configured with a dead Postgres silently booted in-memory
and lost data without telling anyone. The fix (same spirit as the
existing production fail-fast for unset secrets):

* DATABASE_URL SET + ``init_db`` fails -> ``RuntimeError``, boot
  ABORTS (``app.main._init_database_or_fail_fast`` is deliberately
  unwrapped by the lifespan). The configured URL is never logged:
  it embeds the DB password, so tests assert the password appears
  nowhere in the raised message.
* DATABASE_URL UNSET/blank (non-production) -> intentional dev/test
  no-DB mode: ``init_db`` is NOT called at all (the test proves
  this with a would-raise stub — a skipped call cannot be a
  swallowed failure), boot continues without persistence, and the
  log says so. This is the documented ``.env.development``
  "empty = in-memory mode" path. Production + unset never reaches
  this branch: Settings validation already refuses it.
* The distinction itself is explicit and single-sourced:
  ``settings.database_configured`` (``app/config.py``) — empty or
  whitespace-only is unconfigured, anything else is configured.
  No caller guesses, and no new operator flag is needed: the
  presence of the URL *is* the flag.

No real Postgres exists in this VM, so a dead DB is simulated two
ways, and the report must say which is which:

1. Unit level: ``init_db`` monkeypatched to raise (the exact shape
   of a refused Postgres connection) — fast and deterministic.
2. Probe level (subprocess tests at the end): the REAL ``init_db``
   against ``postgresql+asyncpg://...@127.0.0.1:59999/...`` — a port
   nothing listens on, so the driver fails with connection-refused
   immediately (no timeout wait, no server needed). That probe
   exercises the real engine + real ``init_db`` + real helper.

The test suite's normal path is unchanged: ``conftest`` sets a
sqlite-file DATABASE_URL, so those tests take the "configured"
branch and ``init_db`` succeeds exactly as before.

Environment note: ``app.main`` needs ``fpdf2`` (pre-existing gap —
the Phase 26 lifespan test skips for the same reason). Where
``fpdf`` is absent, these tests install a minimal stub module
before importing ``app.main`` so the *DB boot logic* can still be
exercised; the stub only stands in for the PDF class, which DB
boot never touches. If ``app.main`` still cannot be imported for
another reason, the main-helper tests skip honestly instead of
failing on an unrelated dependency.
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

_DEAD_PG_URL = "postgresql+asyncpg://aegis:probe-password-not-real@127.0.0.1:59999/aegis"
_DEAD_PG_PASSWORD = "probe-password-not-real"


def _install_fpdf_stub_if_absent():
    """Install a minimal fpdf package stub only if fpdf2 is absent.

    ``app.dossiers.pdf`` imports ``FPDF`` and ``fpdf.enums``
    (``MethodReturnValue``, ``XPos``, ``YPos``) at module level, so
    the stub must be a package with an ``enums`` submodule. It is
    only a stand-in for import: DB boot never generates a PDF.
    """
    try:
        import fpdf  # noqa: F401
        return
    except ImportError:
        pass
    import enum

    stub = ModuleType("fpdf")
    stub.__path__ = []  # mark as a package so fpdf.enums can load

    class _FPDF:  # minimal stand-in; DB boot never touches PDF code
        def __init__(self, *args, **kwargs):
            pass

    stub.FPDF = _FPDF
    enums = ModuleType("fpdf.enums")

    class XPos(enum.Enum):
        LEFT = "LEFT"
        CENTER = "CENTER"
        RIGHT = "RIGHT"
        LMARGIN = "LMARGIN"
        RMARGIN = "RMARGIN"

    class YPos(enum.Enum):
        TOP = "TOP"
        NEXT = "NEXT"
        LAST = "LAST"
        TMARGIN = "TMARGIN"
        BMARGIN = "BMARGIN"

    class MethodReturnValue(enum.Enum):
        VALUE = "VALUE"

    enums.XPos = XPos
    enums.YPos = YPos
    enums.MethodReturnValue = MethodReturnValue
    sys.modules["fpdf"] = stub
    sys.modules["fpdf.enums"] = enums


def _load_main_module():
    """Import app.main, stubbing fpdf only if it is really absent.

    Returns the module, or raises pytest.skip() when app.main cannot
    be imported for an unrelated reason (the skip message names it).
    """
    _install_fpdf_stub_if_absent()
    try:
        import app.main as main_module
    except Exception as exc:  # noqa: BLE001 - any import failure is a skip reason
        pytest.skip(f"app.main not importable here: {exc}")
    return main_module


# ══════════════════════════════════════════════════════════════════
# Section A — the explicit distinction (config only, no main import)
# ══════════════════════════════════════════════════════════════════


class TestDatabaseConfigured:
    def test_empty_is_unconfigured(self):
        from app.config import Settings

        assert Settings(DATABASE_URL="").database_configured is False

    def test_whitespace_only_is_unconfigured(self):
        from app.config import Settings

        assert Settings(DATABASE_URL="   ").database_configured is False

    def test_sqlite_url_is_configured(self):
        from app.config import Settings

        assert (
            Settings(DATABASE_URL="sqlite+aiosqlite:///./test.db").database_configured
            is True
        )

    def test_postgres_url_is_configured(self):
        from app.config import Settings

        assert Settings(DATABASE_URL=_DEAD_PG_URL).database_configured is True

    def test_property_tracks_monkeypatched_field(self):
        # The lifespan helper reads settings.database_configured at call
        # time; a test that monkeypatches settings.DATABASE_URL must
        # see the new value without rebuilding Settings.
        from app.config import settings

        original = settings.DATABASE_URL
        try:
            settings.DATABASE_URL = ""
            assert settings.database_configured is False
            settings.DATABASE_URL = _DEAD_PG_URL
            assert settings.database_configured is True
        finally:
            settings.DATABASE_URL = original


# ══════════════════════════════════════════════════════════════════
# Section B — no-DB placeholder engine (base only, no main import)
# ══════════════════════════════════════════════════════════════════


class TestNoDbPlaceholderEngine:
    @pytest.mark.asyncio
    async def test_empty_url_builds_in_memory_sqlite_engine(self, monkeypatch):
        """Previously create_async_engine("") raised ArgumentError at
        import, so documented empty-URL dev boot could never run.
        Now it yields a sqlite :memory: engine (StaticPool) instead
        of crashing — and it is NOT a postgres engine in disguise."""
        from app.db import base as base_module

        monkeypatch.setattr(base_module.settings, "DATABASE_URL", "")
        engine = base_module.create_engine()
        try:
            assert engine.dialect.name == "sqlite"
            assert engine.url.database == ":memory:"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_configured_url_engine_is_untouched(self, monkeypatch):
        """The placeholder branch must not leak into the configured
        path: a sqlite-file URL still builds a file engine."""
        from app.db import base as base_module

        monkeypatch.setattr(
            base_module.settings, "DATABASE_URL", "sqlite+aiosqlite:///./test.db"
        )
        engine = base_module.create_engine()
        try:
            assert engine.dialect.name == "sqlite"
            assert engine.url.database == "./test.db"
        finally:
            await engine.dispose()


# ══════════════════════════════════════════════════════════════════
# Section C — lifespan helper, init_db stubbed (simulated dead DB)
# ══════════════════════════════════════════════════════════════════


class TestInitDatabaseOrFailFast:
    @pytest.mark.asyncio
    async def test_configured_dead_db_raises_and_never_logs_password(
        self, monkeypatch
    ):
        main = _load_main_module()
        monkeypatch.setattr(main.settings, "DATABASE_URL", _DEAD_PG_URL)
        failing = AsyncMock(side_effect=ConnectionRefusedError("connection refused"))
        monkeypatch.setattr(main, "init_db", failing)
        with pytest.raises(RuntimeError) as excinfo:
            await main._init_database_or_fail_fast()
        failing.assert_awaited_once()
        message = str(excinfo.value)
        assert "DATABASE_URL is configured" in message
        assert "will NOT silently fall back to in-memory state" in message
        assert "connection refused" in message
        # The URL embeds the password: it must appear nowhere.
        assert _DEAD_PG_PASSWORD not in message
        assert _DEAD_PG_URL not in message

    @pytest.mark.asyncio
    async def test_configured_healthy_db_boots(self, monkeypatch):
        main = _load_main_module()
        monkeypatch.setattr(
            main.settings, "DATABASE_URL", "sqlite+aiosqlite:///./test.db"
        )
        healthy = AsyncMock(return_value=None)
        monkeypatch.setattr(main, "init_db", healthy)
        await main._init_database_or_fail_fast()  # must not raise
        healthy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unconfigured_skips_init_db_entirely(self, monkeypatch):
        main = _load_main_module()
        monkeypatch.setattr(main.settings, "DATABASE_URL", "")
        would_raise = AsyncMock(
            side_effect=AssertionError("init_db must not be called with no DB")
        )
        monkeypatch.setattr(main, "init_db", would_raise)
        await main._init_database_or_fail_fast()  # must not raise
        would_raise.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_whitespace_url_skips_init_db_entirely(self, monkeypatch):
        main = _load_main_module()
        monkeypatch.setattr(main.settings, "DATABASE_URL", "   ")
        would_raise = AsyncMock(
            side_effect=AssertionError("init_db must not be called with no DB")
        )
        monkeypatch.setattr(main, "init_db", would_raise)
        await main._init_database_or_fail_fast()
        would_raise.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════
# Section D — subprocess probes with the REAL init_db (no stubbing)
# ══════════════════════════════════════════════════════════════════


def _run_probe_snippet(code: str, database_url: str) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter with a controlled env.

    The backend directory is the working directory so ``app`` is
    importable; DATABASE_URL/SECRET_KEY/ENVIRONMENT are the only
    AEGIS settings forced (everything else inherits the ambient env,
    which in this VM already carries no DATABASE_URL).
    """
    backend_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["SECRET_KEY"] = "test-secret-key-for-testing-only"
    env["ENVIRONMENT"] = "development"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


_PROBE_PREAMBLE = (
    "import sys, types, enum\n"
    "sys.path.insert(0, '.')\n"
    "try:\n"
    "    import fpdf\n"
    "except ImportError:\n"
    "    stub = types.ModuleType('fpdf')\n"
    "    stub.__path__ = []\n"
    "    class FPDF:\n"
    "        def __init__(self, *a, **k): pass\n"
    "    stub.FPDF = FPDF\n"
    "    enums = types.ModuleType('fpdf.enums')\n"
    "    class XPos(enum.Enum):\n"
    "        LEFT='LEFT'; CENTER='CENTER'; RIGHT='RIGHT'; LMARGIN='LMARGIN'; RMARGIN='RMARGIN'\n"
    "    class YPos(enum.Enum):\n"
    "        TOP='TOP'; NEXT='NEXT'; LAST='LAST'; TMARGIN='TMARGIN'; BMARGIN='BMARGIN'\n"
    "    class MethodReturnValue(enum.Enum):\n"
    "        VALUE='VALUE'\n"
    "    enums.XPos=XPos; enums.YPos=YPos; enums.MethodReturnValue=MethodReturnValue\n"
    "    sys.modules['fpdf'] = stub\n"
    "    sys.modules['fpdf.enums'] = enums\n"
)


def test_probe_dead_postgres_fails_loudly_with_real_init_db():
    """Real engine + real init_db against a refused port: boot must
    die with the fail-fast RuntimeError, never with a warning + a
    running app. Skipped (not failed) if app.main cannot be imported
    here for an unrelated dependency reason."""
    code = (
        _PROBE_PREAMBLE
        + "import asyncio, app.main as m\n"
        + "asyncio.run(m._init_database_or_fail_fast())\n"
        + "print('UNEXPECTED_BOOT_OK')\n"
    )
    proc = _run_probe_snippet(code, _DEAD_PG_URL)
    combined = proc.stdout + proc.stderr
    if "ModuleNotFoundError" in combined or "ImportError" in combined:
        pytest.skip(f"app.main not importable in probe subprocess: {combined[-500:]}")
    assert proc.returncode != 0, f"dead DB must abort boot, got stdout={proc.stdout!r}"
    assert "Refusing to boot" in combined
    assert "UNEXPECTED_BOOT_OK" not in proc.stdout
    assert _DEAD_PG_PASSWORD not in combined
    assert _DEAD_PG_URL not in combined


def test_probe_dead_postgres_aborts_real_lifespan_entry():
    """Stronger than the helper probe above: enter the REAL lifespan
    context manager with a dead configured DB. Startup must raise
    before the yield (i.e. before the event bus / monitor / any
    service starts) — this is what "fail-fast at boot" means, not
    just "the helper raises when called in isolation"."""
    code = (
        _PROBE_PREAMBLE
        + "import asyncio, app.main as m\n"
        + "from fastapi import FastAPI\n"
        + "async def _enter():\n"
        + "    async with m.lifespan(FastAPI()):\n"
        + "        print('UNEXPECTED_LIFESPAN_OK')\n"
        + "asyncio.run(_enter())\n"
    )
    proc = _run_probe_snippet(code, _DEAD_PG_URL)
    combined = proc.stdout + proc.stderr
    if "ModuleNotFoundError" in combined or "ImportError" in combined:
        pytest.skip(f"app.main not importable in probe subprocess: {combined[-500:]}")
    assert proc.returncode != 0, f"dead DB must abort lifespan, got stdout={proc.stdout!r}"
    assert "Refusing to boot" in combined
    assert "UNEXPECTED_LIFESPAN_OK" not in proc.stdout
    assert _DEAD_PG_PASSWORD not in combined
    assert _DEAD_PG_URL not in combined


def test_probe_no_db_configured_boots_without_init_db():
    """The dev path that used to crash at import (ArgumentError on
    the empty URL): importing app.main and running the DB gate must
    now succeed with no database configured."""
    code = (
        _PROBE_PREAMBLE
        + "import asyncio, app.main as m\n"
        + "from app.config import settings\n"
        + "assert settings.database_configured is False\n"
        + "asyncio.run(m._init_database_or_fail_fast())\n"
        + "print('NO_DB_BOOT_OK')\n"
    )
    proc = _run_probe_snippet(code, "")
    combined = proc.stdout + proc.stderr
    if "ModuleNotFoundError" in combined or "ImportError" in combined:
        pytest.skip(f"app.main not importable in probe subprocess: {combined[-500:]}")
    assert proc.returncode == 0, f"no-DB boot must succeed, stderr={proc.stderr[-1000:]}"
    assert "NO_DB_BOOT_OK" in proc.stdout
