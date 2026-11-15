# backend/tests/unit/test_phase38_w4_m3_scrub.py
"""Phase 38 Worker 4 — M-3: raw ``exc_info=True`` scrub (async_utils +
repo-wide sweep).

Gate M-3 listed five raw ``exc_info=True`` sites: four in
``app.core.event_bus`` (another worker — NOT touched here) and one in
``app.utils.async_utils.create_task_safe`` (this worker). The sweep
below proves the production tree now holds NO other raw site: the
only remaining ``exc_info=True`` occurrences in ``backend/app/`` are
the four event_bus sites plus comments/docstrings that merely NAME
the pattern.

The sweep ALSO checked the equivalent channel — ``traceback.format_exc()``
logged verbatim — and found four sites in ``app.agents.base_agent``
(``start`` failure, tick-error handling incl. the stored
``metrics.last_error`` / ``_error_history`` text, recovery failure).
A tick/setup/recovery failure raised by a DB-backed call can embed
DATABASE_URL in its message, so those four sites are scrubbed here
with the same ``app.db.error_scrub`` choke point (patterns plus the
configured URL as a verbatim secret). Scrubbing is applied to the
formatted traceback BEFORE truncating it — truncating first could
cut a credential shape mid-pattern.

Proof style (as in the Phase 36 scrub suite): FAKE placeholders only
(``fakeuser`` / ``fakepass-TEST-ONLY`` / ``fakehost.invalid`` — nothing
real). Every test asserts the fake password and the fake full URL
appear NOWHERE: not in the log message, not in a formatted traceback
of the logged record, and not in stored error text — while benign
driver detail ("connection refused") survives. ``exc_info`` sites are
proven via the FORMATTED record text (``logging.Formatter().format``),
because scrubbing only the message f-string would still leak through
the traceback's final line.

Non-vacuity: ``test_harness_would_catch_a_raw_leak`` logs the same
fake exception the OLD way (raw ``{e}`` + ``exc_info=True``) and
asserts the secret DOES appear — so a test that passed while the
fix was absent would be visible, not silent.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import Mock

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
_FAKE_MSG = (
    f"connection refused: server at {_FAKE_HOST} rejected auth for "
    f"{_FAKE_USER} via {_FAKE_URL} password={_FAKE_PASS}"
)
_SECRETS = (_FAKE_PASS, _FAKE_URL, f"{_FAKE_USER}:{_FAKE_PASS}")

# Imported at module top ON PURPOSE (not lazily inside the tests):
# importing app modules configures logging, which would detach
# pytest's caplog handler if it happened after a caplog fixture was
# already set up. Importing here — at collection time, as the other
# scrub suites do — keeps caplog capture intact.
from app.config import settings  # noqa: E402
from app.utils.async_utils import AsyncUtils  # noqa: E402
from app.agents.base_agent import AgentConfig, AgentState, BaseAgent  # noqa: E402


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


class _FailingAgent(BaseAgent):
    """Minimal concrete agent whose setup/tick fail with a given error."""

    def __init__(self, config, event_bus, fail_with=None):
        super().__init__(config, event_bus)
        self._fail_with = fail_with

    async def setup(self) -> None:
        if self._fail_with is not None:
            raise self._fail_with

    async def tick(self) -> None:
        if self._fail_with is not None:
            raise self._fail_with

    async def handle_event(self, event) -> None:
        return None

    async def cleanup(self) -> None:
        return None


def _agent(fail_with=None):
    """Agent with no cooldown sleep so tick-error tests stay fast."""
    config = AgentConfig(error_cooldown_ms=0)
    return _FailingAgent(config, Mock(), fail_with=fail_with)


# ── A. async_utils.create_task_safe ────────────────────────────────────


async def test_create_task_safe_scrubs_db_url_from_message_and_traceback(caplog):
    """Fake-URL exception: patterns alone must redact it (no settings
    monkeypatch — the fake URL is NOT the configured DATABASE_URL)."""
    caplog.set_level(logging.ERROR)

    async def _boom():
        raise Exception(_FAKE_MSG)

    task = AsyncUtils.create_task_safe(_boom(), name="m3-probe")
    await task  # wrapper swallows the failure; the task completes cleanly
    assert task.done() and not task.cancelled()

    text = _formatted(caplog)
    _assert_no_leak(text)
    # Benign driver detail survives scrubbing.
    assert "connection refused" in text
    assert "Task 'm3-probe' failed" in text


async def test_create_task_safe_scrubs_verbatim_configured_secret(
    caplog, monkeypatch
):
    """Bare fake password with NO credential key/context around it
    matches no scrubber pattern — only the verbatim-secrets list
    (configured DATABASE_URL) can redact it. Proves that path."""
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(settings, "DATABASE_URL", _FAKE_PASS)

    async def _boom():
        raise Exception(f"login failed: {_FAKE_PASS}")

    task = AsyncUtils.create_task_safe(_boom(), name="m3-verbatim")
    await task
    assert task.done() and not task.cancelled()

    _assert_no_leak(_formatted(caplog))


async def test_create_task_safe_success_still_completes(caplog):
    """Scrub change must not alter the happy path or swallow CancelledError."""
    caplog.set_level(logging.ERROR)

    async def _ok():
        return 42

    task = AsyncUtils.create_task_safe(_ok(), name="m3-ok")
    assert await task is None  # wrapper returns None; value is not propagated
    assert "m3-ok" not in _formatted(caplog)


async def test_create_task_safe_cancellation_is_swallowed():
    """CancelledError stays swallowed (pre-existing behaviour): the
    wrapper catches it and returns normally, so awaiting the task
    must not raise and the task finishes (not cancelled) with None."""
    task = AsyncUtils.create_task_safe(asyncio.sleep(30), name="m3-cancel")
    await asyncio.sleep(0)
    task.cancel()
    assert await task is None  # must not raise
    assert task.done() and not task.cancelled()


# ── B. base_agent format_exc sites ─────────────────────────────────────


async def test_tick_error_scrubs_log_metrics_and_history(caplog):
    """Tick error carrying the fake URL: log message, formatted
    traceback, stored metrics.last_error and _error_history must all
    be scrubbed. Called from inside the except block so format_exc()
    carries the real traceback (its final line is the raw message)."""
    caplog.set_level(logging.ERROR)
    agent = _agent()

    try:
        raise Exception(_FAKE_MSG)
    except Exception as exc:
        await agent._handle_tick_error(exc)

    _assert_no_leak(
        _formatted(caplog),
        agent.metrics.last_error_message,
        *[e["error"] for e in agent._error_history],
        *[e["traceback"] for e in agent._error_history],
    )
    assert "connection refused" in _formatted(caplog)
    assert agent.metrics.total_errors == 1
    assert agent._error_count == 1


async def test_tick_error_verbatim_secret_scrubbed(caplog, monkeypatch):
    """Same as above, but the leak is a bare fake password that only
    the verbatim-secrets list can catch."""
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(settings, "DATABASE_URL", _FAKE_PASS)
    agent = _agent()

    try:
        raise Exception(f"login failed: {_FAKE_PASS}")
    except Exception as exc:
        await agent._handle_tick_error(exc)

    _assert_no_leak(
        _formatted(caplog),
        agent.metrics.last_error_message,
        agent.metrics.get_summary()["errors"]["last_error"],
    )


async def test_start_failure_scrubs_and_still_raises(caplog):
    """setup() raising a fake-URL error: start() must log scrubbed
    text, move to ERROR, and still re-raise the original."""
    caplog.set_level(logging.ERROR)
    agent = _agent(fail_with=Exception(_FAKE_MSG))

    with pytest.raises(Exception, match="connection refused"):
        await agent.start()

    assert agent.state == AgentState.ERROR
    _assert_no_leak(_formatted(caplog))
    assert "connection refused" in _formatted(caplog)


async def test_recovery_failure_scrubs(caplog):
    """Recovery failing with a fake-URL error: log message and
    traceback scrubbed, state ERROR. (The 1s sleep inside
    _attempt_recovery is production behaviour; this test takes ~1s.)"""
    caplog.set_level(logging.ERROR)
    agent = _agent(fail_with=Exception(_FAKE_MSG))

    await agent._attempt_recovery()

    assert agent.state == AgentState.ERROR
    _assert_no_leak(_formatted(caplog))
    assert "connection refused" in _formatted(caplog)


# ── C. Non-vacuity: the harness WOULD see a raw leak ──────────────────


def test_harness_would_catch_a_raw_leak(caplog):
    """The OLD shape (raw ``{e}`` + ``exc_info=True`` on a plain
    stdlib logger) MUST leak into the formatted record text. If this
    test ever goes green-with-no-leak, the harness is blind and the
    scrub tests above prove nothing."""
    caplog.set_level(logging.ERROR)
    raw_logger = logging.getLogger("aegis.m3.nonvacuity.probe")

    try:
        raise Exception(_FAKE_MSG)
    except Exception as e:
        raw_logger.error(f"Task 'old-shape' failed: {e}", exc_info=True)

    text = _formatted(caplog)
    assert _FAKE_PASS in text, "harness is blind to raw leaks"
    assert _FAKE_URL in text, "harness is blind to raw leaks"
