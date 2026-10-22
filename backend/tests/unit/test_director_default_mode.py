# backend/tests/unit/test_director_default_mode.py
"""Phase 28 — fresh-show director default is semi_auto.

Covers:
  * a fresh DirectorService reports semi_auto (canonical default)
  * AEGIS_DIRECTOR_DEFAULT_MODE env override is honoured by a fresh
    service (valid value used; legacy "auto" normalised; invalid
    value ignored in favour of the canonical default)
  * an explicitly set mode is never overridden by the default — it
    survives subsequent default resolution / a "restart" (a new
    service instance getting the default does not touch the explicit
    choice on the existing one), and the replay-queue fallback only
    uses the default when no mode is present at all.

No network, no DB, no real credentials.
"""

from __future__ import annotations

import pytest

from app.services.director_service import (
    DEFAULT_DIRECTOR_MODE,
    DirectorService,
    default_director_mode,
)


@pytest.mark.unit
class TestFreshDefaultIsSemiAuto:
    def test_canonical_default_constant(self):
        assert DEFAULT_DIRECTOR_MODE == "semi_auto"

    def test_fresh_service_reports_semi_auto(self, monkeypatch):
        monkeypatch.delenv("AEGIS_DIRECTOR_DEFAULT_MODE", raising=False)
        assert DirectorService().snapshot()["mode"] == "semi_auto"
        assert default_director_mode() == "semi_auto"

    def test_fresh_service_default_is_not_full_auto(self, monkeypatch):
        monkeypatch.delenv("AEGIS_DIRECTOR_DEFAULT_MODE", raising=False)
        assert DirectorService().snapshot()["mode"] != "full_auto"


@pytest.mark.unit
class TestEnvOverride:
    def test_env_override_full_auto(self, monkeypatch):
        monkeypatch.setenv("AEGIS_DIRECTOR_DEFAULT_MODE", "full_auto")
        assert default_director_mode() == "full_auto"
        assert DirectorService().snapshot()["mode"] == "full_auto"

    def test_env_override_manual(self, monkeypatch):
        monkeypatch.setenv("AEGIS_DIRECTOR_DEFAULT_MODE", "manual")
        assert DirectorService().snapshot()["mode"] == "manual"

    def test_env_override_legacy_auto_normalises(self, monkeypatch):
        monkeypatch.setenv("AEGIS_DIRECTOR_DEFAULT_MODE", "auto")
        assert DirectorService().snapshot()["mode"] == "full_auto"

    def test_env_override_invalid_falls_back_to_semi_auto(self, monkeypatch):
        monkeypatch.setenv("AEGIS_DIRECTOR_DEFAULT_MODE", "turbo")
        assert default_director_mode() == "semi_auto"
        assert DirectorService().snapshot()["mode"] == "semi_auto"


@pytest.mark.unit
class TestExplicitModeSurvives:
    async def test_explicit_full_auto_survives_default_resolution(
        self, monkeypatch
    ):
        monkeypatch.delenv("AEGIS_DIRECTOR_DEFAULT_MODE", raising=False)
        director = DirectorService()
        await director.set_mode("full_auto", updated_by="op1")
        # Resolving the default again (e.g. another fresh service on
        # "restart") must not clobber the explicit choice.
        _fresh = DirectorService()
        assert _fresh.snapshot()["mode"] == "semi_auto"
        assert director.snapshot()["mode"] == "full_auto"

    async def test_explicit_mode_survives_env_override(self, monkeypatch):
        monkeypatch.setenv("AEGIS_DIRECTOR_DEFAULT_MODE", "manual")
        director = DirectorService()
        assert director.snapshot()["mode"] == "manual"
        await director.set_mode("full_auto", updated_by="op1")
        assert director.snapshot()["mode"] == "full_auto"

    async def test_replay_queue_fallback_uses_default_only_when_unset(
        self, monkeypatch
    ):
        from app.services.replay.queue import ReplayQueue

        monkeypatch.delenv("AEGIS_DIRECTOR_DEFAULT_MODE", raising=False)
        # No replay service is needed for the mode read alone.
        queue = ReplayQueue(
            replay_service=None, director_provider=lambda: {}
        )
        assert queue.director_mode() == "semi_auto"
        queue = ReplayQueue(
            replay_service=None,
            director_provider=lambda: {"mode": "full_auto"},
        )
        assert queue.director_mode() == "full_auto"
