"""Phase 35 W5 (item 9, test pollution) — proof tests.

Proves, in an automated and repeatable way, the two pollution fixes
whose scope is this worker:

1. ``db_session`` isolation (``tests/conftest.py``): every test gets
   a fresh in-memory database. The committed-write test below runs
   BEFORE the empty-at-start test in file order, so if the fixture
   still shared one database the later test would see the earlier
   test's row. The two ``test_tournament``-fixture tests both insert
   the SAME slug (``test-tournament``, unique) — on the old shared
   ``./test.db`` fixture the second flush would raise
   ``IntegrityError``.
2. fpdf stub teardown (``test_phase33_db_boot_fail_fast.py``): in a
   FRESH interpreter, entering ``_fpdf_stub_if_absent``, importing
   ``app.main`` and leaving the context must restore
   ``sys.modules`` exactly (``fpdf`` / ``fpdf.enums`` absent again,
   ``app.main`` evicted), and a later bare import must fail
   honestly with ``ModuleNotFoundError`` instead of reusing a
   stub-bound module.

What this file does NOT prove (stated, not hidden):

* Suite-wide DB isolation. Tests that reach the DB through the app
  lifespan / ``TestClient`` / ``get_db`` still share ``./test.db``
  via the module-global engine — see the ``db_session`` docstring.
* That dossier-PDF generation works. ``fpdf2`` is absent in this
  environment and the minimal stub has no PDF methods, so the five
  PDF-generation tests in ``test_dossier_pdf.py`` cannot pass here
  under any stub. Their outcome (collection error standalone, 5
  failures when a collection-time stub from another file is
  present) is order-dependent on OTHER files' stubs, which are
  outside this worker's file scope. This file proves only that the
  Phase 33 file adds no leak of its own.
* The narrowed skip in ``test_phase26_lifespan_monitor.py``: that
  file is skipped at collection here (``fakeredis`` absent), so its
  narrowed branch never executes in this environment. The narrowed
  skip in the Phase 33 file IS proved below, by the same
  mechanism (a non-``ImportError`` import failure must error,
  never skip).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.unit


# ── 1. db_session isolation ───────────────────────────────────────


@pytest.mark.asyncio
async def test_db_session_committed_write_is_isolated(db_session):
    """A COMMITTED write in this test must not be visible elsewhere.

    Runs first in file order on purpose: the next test asserts the
    database is empty, which only holds if this test's committed
    row lived in a per-test database that was disposed.
    """
    from app.db.models import Tournament

    tournament = Tournament(
        id="phase35-proof-tournament",
        name="Phase 35 Proof Tournament",
        slug="test-tournament",
        description="isolation proof",
        status="draft",
        points_per_kill=1,
        organizer="Proof",
        is_public=True,
    )
    db_session.add(tournament)
    await db_session.commit()
    count = await db_session.scalar(
        select(func.count()).select_from(Tournament)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_db_session_starts_empty(db_session):
    """The previous test committed a row; this test must see none."""
    from app.db.models import Team, Tournament

    assert (
        await db_session.scalar(select(func.count()).select_from(Tournament))
        == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(Team)) == 0


@pytest.mark.asyncio
async def test_tournament_fixture_slug_reusable_first(
    test_tournament, db_session
):
    """First use of the fixed-slug ``test_tournament`` fixture."""
    from app.db.models import Tournament

    assert test_tournament.slug == "test-tournament"
    count = await db_session.scalar(
        select(func.count()).select_from(Tournament)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_tournament_fixture_slug_reusable_second(
    test_tournament, db_session
):
    """Same slug again: on the old shared-DB fixture this flush
    would raise IntegrityError (unique slug). Per-test isolation
    makes it a fresh, legal insert."""
    from app.db.models import Tournament

    assert test_tournament.slug == "test-tournament"
    count = await db_session.scalar(
        select(func.count()).select_from(Tournament)
    )
    assert count == 1


# ── 2. fpdf stub teardown (fresh interpreter) ─────────────────────


def _backend_dir() -> str:
    return str(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_fpdf_stub_teardown_in_fresh_interpreter():
    """The Phase 33 stub context leaves no trace in sys.modules."""
    code = (
        "import importlib.util, sys\n"
        "assert 'fpdf' not in sys.modules\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'dbboot', 'tests/unit/test_phase33_db_boot_fail_fast.py')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "with m._fpdf_stub_if_absent():\n"
        "    assert 'fpdf' in sys.modules\n"
        "    import app.main\n"
        "    assert 'app.main' in sys.modules\n"
        "assert 'fpdf' not in sys.modules, 'fpdf leak'\n"
        "assert 'fpdf.enums' not in sys.modules, 'fpdf.enums leak'\n"
        "assert 'app.main' not in sys.modules, 'app.main not evicted'\n"
        "try:\n"
        "    import app.main\n"
        "except ModuleNotFoundError as exc:\n"
        "    assert 'fpdf' in str(exc)\n"
        "    print('PHASE35_W5_STUB_TEARDOWN_OK')\n"
        "else:\n"
        "    raise AssertionError('bare app.main import unexpectedly succeeded')\n"
    )
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
    env["SECRET_KEY"] = "test-secret-key-for-testing-only"
    env["ENVIRONMENT"] = "development"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_backend_dir(),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = proc.stdout + proc.stderr
    if "PHASE35_W5_STUB_TEARDOWN_OK" not in proc.stdout:
        pytest.fail(
            f"stub teardown proof failed (rc={proc.returncode}): "
            f"{combined[-1500:]}"
        )


# ── 3. narrowed skip: non-ImportError must fail, never skip ───────


def test_non_importerror_import_failure_is_not_skipped():
    """Run one Section C test with an env that makes app.main import
    raise a VALIDATION error (production + unset secrets), not an
    ImportError. The old blanket ``except Exception -> skip`` would
    report this as a skip; the narrowed fixture must surface it as
    an error. If the import succeeds in this env instead (config
    behaviour changed), the narrowing is unproven here — skip
    honestly rather than claiming a proof that did not happen."""
    env = dict(os.environ)
    env["ENVIRONMENT"] = "production"
    env["SECRET_KEY"] = ""
    env["DATABASE_URL"] = ""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_phase33_db_boot_fail_fast.py::"
            "TestInitDatabaseOrFailFast::test_configured_healthy_db_boots",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-q",
        ],
        cwd=_backend_dir(),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = (proc.stdout + proc.stderr)[-800:]
    if "1 skipped" in tail or "skipped" in tail.splitlines()[-1:]:
        pytest.fail(f"import failure was reported as a skip: {tail}")
    if proc.returncode == 0:
        pytest.skip(
            "app.main import no longer fails in the production/empty "
            "env; narrowing unproven in this environment."
        )
    assert "error" in tail.lower() or proc.returncode != 0
