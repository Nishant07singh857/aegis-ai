# backend/tests/unit/test_phase35_w4_db_scrub_migration.py
"""Phase 35 Worker 4 — items 5 + 8: DB-error scrub (M-5) + migration 017 (M-8).

ITEM 5 — the Phase 33 boot gate embedded the RAW ``init_db`` exception
in a log line and in the raised ``RuntimeError``. Driver errors can
embed the configured DATABASE_URL (username + password), so the
verification report (M-5) required a STRUCTURAL guarantee, not a
probe-based one: every DB error string that reaches a log, a WS/HTTP
payload, or a raised exception must pass through the single scrubber
``app.db.error_scrub`` first. Tests below use FAKE placeholders only
(``fakeuser`` / ``fakepass`` / ``fakehost`` — nothing real, nothing
resembling a real credential) and assert they appear NOWHERE in the
scrubbed output: not in the log text, not in the raised message, and
not in a formatted traceback of the exception chain.

ITEM 8 — ``match_team_stats`` / ``match_player_stats`` were
``create_all``-only: no migration created them, so a fresh
``alembic upgrade head`` never created them. Migration 017 creates
them with the models' exact schema. The migration tests run the REAL
alembic CLI in subprocesses against SCRATCH sqlite DBs under pytest's
``tmp_path`` (never the repo tree, never the suite's test.db):

* a DB stamped at 016 + ``upgrade head`` applies 017 and the resulting
  schema is PRAGMA-identical to the models' ``create_all`` schema;
* a second ``upgrade head`` is a no-op;
* ``downgrade 016`` drops both tables and ``upgrade head`` recreates
  them identically (round trip);
* a DB where ``create_all`` already made the tables is left untouched
  by 017 (inspector guard).

Honest scope note: a full fresh-chain ``upgrade head`` on sqlite does
NOT run in this VM for a PRE-EXISTING reason unrelated to 017 —
migration 009's ``op.alter_column`` (DROP NOT NULL) is unsupported by
sqlite and the chain stops at 008. That is reported, not hidden: the
tests therefore stamp the version table at 016 (a version-only harness
step) and prove 017 itself + schema equivalence, which is exactly what
017 owns. No Postgres server exists in this VM to prove the full chain
there; 017 uses only portable DDL (no Postgres-only constructs).
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

# ── Fake placeholders ONLY (never real credentials, never real hosts) ──
_FAKE_USER = "fakeuser"
_FAKE_PASS = "fakepass-TEST-ONLY"
_FAKE_HOST = "fakehost.invalid"
_FAKE_DB = "fakedb"
_FAKE_URL = (
    f"postgresql+asyncpg://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:5432/{_FAKE_DB}"
)
_FAKE_TOKEN = "faketoken-TEST-ONLY-12345"
_FAKE_JWT = "eyJhbGciOiJmYWtlIn0.eyJmYWtlIjoxfQ.fakefakefake"

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


# ══════════════════════════════════════════════════════════════════
# Section A — scrub helper (no app.main import needed: stdlib-only)
# ══════════════════════════════════════════════════════════════════


class TestScrubDbErrorText:
    def test_url_with_embedded_credentials(self):
        from app.db.error_scrub import scrub_db_error_text

        text = (
            "could not translate host name: "
            f"connection to server at {_FAKE_HOST} failed "
            f"(postgresql://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:5432/{_FAKE_DB})"
        )
        out = scrub_db_error_text(text)
        assert _FAKE_USER not in out
        assert _FAKE_PASS not in out
        assert _FAKE_URL not in out
        # Non-credential detail survives: host, db name, driver wording.
        assert _FAKE_HOST in out
        assert _FAKE_DB in out
        assert "could not translate host name" in out

    def test_url_assignment_redacted_whole(self):
        from app.db.error_scrub import scrub_db_error_text

        out = scrub_db_error_text(f"DATABASE_URL={_FAKE_URL} is invalid")
        assert _FAKE_URL not in out
        assert _FAKE_PASS not in out
        assert "DATABASE_URL=<redacted>" in out

    def test_other_schemes_with_credentials(self):
        from app.db.error_scrub import scrub_db_error_text

        for url in (
            f"mysql://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:3306/{_FAKE_DB}",
            f"redis://:{_FAKE_PASS}@{_FAKE_HOST}:6379/0",
            f"postgresql://{_FAKE_USER}@{_FAKE_HOST}/{_FAKE_DB}",
        ):
            out = scrub_db_error_text(f"failed: {url}")
            assert _FAKE_PASS not in out
            assert _FAKE_USER not in out

    def test_credential_assignments(self):
        from app.db.error_scrub import scrub_db_error_text

        cases = [
            f"login failed password={_FAKE_PASS}",
            f"password: '{_FAKE_PASS}' rejected",
            f"db_password={_FAKE_PASS}",
            f"secret={_FAKE_TOKEN}",
            f"api_key={_FAKE_TOKEN}",
            f"token: '{_FAKE_TOKEN}'",
            f"access_token={_FAKE_TOKEN}",
        ]
        for text in cases:
            out = scrub_db_error_text(text)
            assert _FAKE_PASS not in out, text
            assert _FAKE_TOKEN not in out, text

    def test_token_shapes(self):
        from app.db.error_scrub import scrub_db_error_text

        out = scrub_db_error_text(f"Authorization: Bearer {_FAKE_JWT} denied")
        assert _FAKE_JWT not in out
        assert "Bearer <redacted-token>" in out
        out = scrub_db_error_text("key sk-fake1234567890abcdef leaked")
        assert "sk-fake1234567890abcdef" not in out

    def test_verbatim_known_secret(self):
        from app.db.error_scrub import scrub_db_error_text

        # A driver message echoing the configured URL in a shape no
        # pattern recognises must still be redacted via secrets=.
        out = scrub_db_error_text(
            f"echo [{_FAKE_URL}] end", secrets=[_FAKE_URL]
        )
        assert _FAKE_URL not in out
        assert _FAKE_PASS not in out

    def test_benign_driver_text_preserved_verbatim(self):
        from app.db.error_scrub import scrub_db_error_text

        text = 'connection refused (127.0.0.1:5432); password authentication failed for user "fakeuser"'
        assert scrub_db_error_text(text) == text

    def test_idempotent_and_never_raises(self):
        from app.db.error_scrub import scrub_db_error_text

        text = f"postgresql://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}/db password={_FAKE_PASS}"
        once = scrub_db_error_text(text)
        assert scrub_db_error_text(once) == once
        assert scrub_db_error_text(None) == ""
        assert scrub_db_error_text(12345) == "12345"

        class _BadStr(Exception):
            def __str__(self):  # noqa: D102
                raise RuntimeError("stringification boom")

        # Fail-closed: a placeholder, never a raise, never the raw text.
        assert scrub_db_error_text(_BadStr()) == "<redacted db error>"

    def test_scrubbed_exception_does_not_mutate_original(self):
        from app.db.error_scrub import scrubbed_exception

        raw = RuntimeError(f"connect failed: {_FAKE_URL}")
        surrogate = scrubbed_exception(raw)
        assert _FAKE_PASS not in str(surrogate)
        assert _FAKE_URL not in str(surrogate)
        # The original still carries its raw text: scrubbing never
        # rewrites the source exception, it only builds a safe copy.
        assert _FAKE_PASS in str(raw)


# ══════════════════════════════════════════════════════════════════
# Section B — boot path + other main.py DB-error sites (app.main)
# ══════════════════════════════════════════════════════════════════
# fpdf2 is absent in this VM (pre-existing gap, same as the Phase 33
# DB-boot tests). The fpdf stub comes from the Phase 33
# teardown-guarded context manager (imported, not re-implemented):
# on exit it restores sys.modules exactly and evicts stub-bound
# app.main / app.dossiers.* modules, so this file adds no stub leak
# of its own (N-2 follow-up). The stub is import-only; DB boot
# never generates a PDF.

# Reuse (N-2): _fpdf_stub_if_absent from the Phase 33 DB-boot suite —
# same teardown guarantee, no duplicated stub code. Bare-name import
# is the established pattern in this suite (cf.
# test_team_status_hp_bands importing from test_phase29_w1_pov_reader).
from test_phase33_db_boot_fail_fast import _fpdf_stub_if_absent


def _load_main_module():
    # Skip ONLY on a missing-module import failure. Every other
    # import failure propagates and FAILS loudly: a real
    # setup/configuration error must never be reported as a skip.
    with _fpdf_stub_if_absent():
        try:
            import app.main as main_module
        except ImportError as exc:
            pytest.skip(f"app.main not importable here (missing dependency): {exc}")
        return main_module


class _RecordingLogger:
    """Stand-in for app.main's structlog logger: records rendered text."""

    def __init__(self):
        self.calls = []

    def _record(self, *args, **kwargs):
        self.calls.append(" ".join(str(a) for a in args) + " " + " ".join(f"{k}={v}" for k, v in kwargs.items()))

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


class TestBootPathScrub:
    @pytest.mark.asyncio
    async def test_dead_db_raises_scrubbed_and_traceback_clean(self, monkeypatch):
        main = _load_main_module()
        logger = _RecordingLogger()
        monkeypatch.setattr(main, "logger", logger)
        monkeypatch.setattr(main.settings, "DATABASE_URL", _FAKE_URL)
        raw = RuntimeError(
            f"connection refused: server at {_FAKE_HOST} rejected "
            f"auth for {_FAKE_USER} via {_FAKE_URL} password={_FAKE_PASS}"
        )
        failing = AsyncMock(side_effect=raw)
        monkeypatch.setattr(main, "init_db", failing)
        with pytest.raises(RuntimeError) as excinfo:
            await main._init_database_or_fail_fast()
        failing.assert_awaited_once()
        message = str(excinfo.value)
        # Behaviour preserved (Phase 33): abort wording + driver detail.
        assert "DATABASE_URL is configured" in message
        assert "will NOT silently fall back to in-memory state" in message
        assert "connection refused" in message
        # Scrub guarantee: the password and the full URL nowhere —
        # message, log, chain. The bare username in driver prose
        # ("auth for fakeuser") is retained BY DESIGN, exactly as
        # Postgres itself phrases "password authentication failed
        # for user ...": a username is not a credential, and the
        # credential PAIR inside the URL is gone.
        for secret in (_FAKE_PASS, _FAKE_URL, f"{_FAKE_USER}:{_FAKE_PASS}"):
            assert secret not in message, secret
            assert secret not in logger.text, secret
        formatted = "".join(
            traceback.format_exception(excinfo.type, excinfo.value, excinfo.tb)
        )
        assert _FAKE_PASS not in formatted
        assert _FAKE_URL not in formatted
        # The chain exists but carries only the scrubbed copy.
        cause = excinfo.value.__cause__
        assert cause is not None
        assert _FAKE_PASS not in str(cause)
        assert _FAKE_URL not in str(cause)
        # Original exception untouched (scrub builds a copy).
        assert _FAKE_PASS in str(raw)

    @pytest.mark.asyncio
    async def test_healthy_and_unconfigured_branches_unchanged(self, monkeypatch):
        main = _load_main_module()
        monkeypatch.setattr(main.settings, "DATABASE_URL", _FAKE_URL)
        healthy = AsyncMock(return_value=None)
        monkeypatch.setattr(main, "init_db", healthy)
        await main._init_database_or_fail_fast()  # must not raise
        healthy.assert_awaited_once()

        monkeypatch.setattr(main.settings, "DATABASE_URL", "")
        would_raise = AsyncMock(
            side_effect=AssertionError("init_db must not be called with no DB")
        )
        monkeypatch.setattr(main, "init_db", would_raise)
        await main._init_database_or_fail_fast()
        would_raise.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_action_error_choke_point_scrubs(self, monkeypatch):
        main = _load_main_module()
        broadcast = AsyncMock()
        monkeypatch.setattr(main.manager, "broadcast", broadcast)
        payload = await main._action_error(
            "create_tournament", f"Tournament was not created: {_FAKE_URL}"
        )
        assert _FAKE_PASS not in payload["message"]
        assert _FAKE_URL not in payload["message"]
        assert _FAKE_PASS not in payload["reason"]
        broadcast.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_seed_first_admin_warning_scrubbed(self, monkeypatch):
        from pydantic import SecretStr

        main = _load_main_module()
        logger = _RecordingLogger()
        monkeypatch.setattr(main, "logger", logger)
        monkeypatch.setattr(main.settings, "ADMIN_EMAIL", "fake-admin@example.invalid")
        monkeypatch.setattr(
            main.settings, "ADMIN_PASSWORD", SecretStr(_FAKE_PASS)
        )

        class _BoomContext:
            async def __aenter__(self):
                raise RuntimeError(f"db down: {_FAKE_URL}")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(main, "get_db_context", lambda: _BoomContext())
        await main._seed_first_admin()  # never raises; logs a warning
        assert _FAKE_PASS not in logger.text
        assert _FAKE_URL not in logger.text
        assert "First-admin seeding skipped" in logger.text


# ══════════════════════════════════════════════════════════════════
# Section C — migration 017 (subprocess alembic, scratch DBs only)
# ══════════════════════════════════════════════════════════════════


def _run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SECRET_KEY"] = "test-secret-key-for-testing-only"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["ENVIRONMENT"] = "development"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _pragma_snapshot(db_path: Path, table: str):
    con = sqlite3.connect(str(db_path))
    try:
        rows = list(con.execute(f"PRAGMA table_info({table})"))
        indexes = list(con.execute(f"PRAGMA index_list({table})"))
        fks = list(con.execute(f"PRAGMA foreign_key_list({table})"))
        sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return rows, indexes, fks, (sql[0] if sql else None)
    finally:
        con.close()


def _version(db_path: Path):
    con = sqlite3.connect(str(db_path))
    try:
        return [r[0] for r in con.execute("SELECT version_num FROM alembic_version")]
    finally:
        con.close()


def _create_all_schema(tmp_path: Path, name: str):
    """Reference schema: the models' create_all on a scratch DB."""
    db_path = tmp_path / name
    code = (
        "import asyncio, sys\n"
        "sys.path.insert(0, '.')\n"
        "from app.db.base import Base, engine\n"
        "from app.services import match_service\n"
        "async def _m():\n"
        "    async with engine.begin() as c:\n"
        "        await c.run_sync(Base.metadata.create_all)\n"
        "asyncio.run(_m())\n"
    )
    env = dict(os.environ)
    env["SECRET_KEY"] = "test-secret-key-for-testing-only"
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["ENVIRONMENT"] = "development"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    return {
        t: _pragma_snapshot(db_path, t)
        for t in ("match_team_stats", "match_player_stats")
    }


class TestMigration017:
    def test_upgrade_from_016_creates_exact_model_schema(self, tmp_path):
        db_path = tmp_path / "from016.db"
        proc = _run_alembic(db_path, "stamp", "016_pov_pilot")
        assert proc.returncode == 0, proc.stderr[-1000:]
        proc = _run_alembic(db_path, "upgrade", "head")
        assert proc.returncode == 0, proc.stderr[-1000:]
        assert _version(db_path) == ["017_match_stats_tables"]

        migrated = {
            t: _pragma_snapshot(db_path, t)
            for t in ("match_team_stats", "match_player_stats")
        }
        reference = _create_all_schema(tmp_path, "reference.db")
        for table in ("match_team_stats", "match_player_stats"):
            assert migrated[table] == reference[table], table
            rows, indexes, fks, _sql = migrated[table]
            # No FKs and no secondary indexes — exactly as the models
            # declare (only the PK autoindex).
            assert fks == [], table
            assert len(indexes) == 1 and indexes[0][2] == 1, (table, indexes)

    def test_second_upgrade_is_noop(self, tmp_path):
        db_path = tmp_path / "noop.db"
        assert _run_alembic(db_path, "stamp", "016_pov_pilot").returncode == 0
        assert _run_alembic(db_path, "upgrade", "head").returncode == 0
        before = {
            t: _pragma_snapshot(db_path, t)
            for t in ("match_team_stats", "match_player_stats")
        }
        proc = _run_alembic(db_path, "upgrade", "head")
        assert proc.returncode == 0, proc.stderr[-1000:]
        assert _version(db_path) == ["017_match_stats_tables"]
        after = {
            t: _pragma_snapshot(db_path, t)
            for t in ("match_team_stats", "match_player_stats")
        }
        assert before == after

    def test_downgrade_roundtrip(self, tmp_path):
        db_path = tmp_path / "roundtrip.db"
        assert _run_alembic(db_path, "stamp", "016_pov_pilot").returncode == 0
        assert _run_alembic(db_path, "upgrade", "head").returncode == 0
        before = {
            t: _pragma_snapshot(db_path, t)
            for t in ("match_team_stats", "match_player_stats")
        }
        proc = _run_alembic(db_path, "downgrade", "016_pov_pilot")
        assert proc.returncode == 0, proc.stderr[-1000:]
        assert _version(db_path) == ["016_pov_pilot"]
        con = sqlite3.connect(str(db_path))
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert "match_team_stats" not in tables
        assert "match_player_stats" not in tables
        proc = _run_alembic(db_path, "upgrade", "head")
        assert proc.returncode == 0, proc.stderr[-1000:]
        after = {
            t: _pragma_snapshot(db_path, t)
            for t in ("match_team_stats", "match_player_stats")
        }
        assert before == after

    def test_createall_existing_tables_left_untouched(self, tmp_path):
        reference = _create_all_schema(tmp_path, "preexisting.db")
        db_path = tmp_path / "preexisting.db"
        proc = _run_alembic(db_path, "stamp", "016_pov_pilot")
        assert proc.returncode == 0, proc.stderr[-1000:]
        proc = _run_alembic(db_path, "upgrade", "head")
        assert proc.returncode == 0, proc.stderr[-1000:]
        assert _version(db_path) == ["017_match_stats_tables"]
        after = {
            t: _pragma_snapshot(db_path, t)
            for t in ("match_team_stats", "match_player_stats")
        }
        assert after == reference
