# backend/tests/unit/test_director_modes.py
"""Unit tests for the Phase 7 three-mode director (full_auto | semi_auto |
manual) and the AI suggestion lifecycle.

Covers:
  * mode validation: legacy "auto" normalises to "full_auto" and is never
    stored; invalid values rejected
  * suggestion lifecycle: propose -> approve applies the payload exactly
    like a manual operator write; propose -> reject drops; expiry drops
  * full_auto: the AI acts directly (system_publish writes) and never
    creates suggestions
  * manual: the AI is silent (system_publish and propose_suggestion are
    both no-ops)
  * semi_auto: the AI never writes (system_publish is a no-op) but may
    propose; nothing is fabricated (dupe / no-op suppression)
  * WS message shape: {"type": "director.suggestion", "data": {...}}
  * approve in FULL_AUTO is a mode conflict

No network, no DB, no real credentials.
"""

from __future__ import annotations

import pytest

from app.api.v1.director.schemas import ModeRequest
from app.services.director_service import (
    WS_MESSAGE_TYPE,
    WS_SUGGESTION_TYPE,
    DirectorModeError,
    DirectorService,
    SuggestionNotFoundError,
)


@pytest.fixture()
def director() -> DirectorService:
    """Fresh service per test — never the process singleton."""
    return DirectorService()


@pytest.fixture()
def messages():
    return []


@pytest.fixture()
def director_with_listener(director: DirectorService, messages: list):
    async def _listener(message: dict) -> None:
        messages.append(message)

    director.add_listener(_listener)
    return director


def _suggestion(
    suggestion_type: str = "scene",
    payload: dict | None = None,
    reason: str = "Fight detected — test; killfeed focus shows the action.",
    confidence: float = 0.8,
    ttl_seconds: float = 60.0,
) -> dict:
    payload = payload if payload is not None else {"scene": "killfeed_focus"}
    return {
        "suggestion_type": suggestion_type,
        "payload": payload,
        "reason": reason,
        "confidence": confidence,
        "ttl_seconds": ttl_seconds,
    }


# --------------------------------------------------------------------- #
# Mode validation / legacy "auto"
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestThreeModes:
    async def test_default_mode_is_semi_auto(self, director: DirectorService):
        # Phase 28: fresh-show default is semi_auto (was full_auto).
        assert director.snapshot()["mode"] == "semi_auto"

    async def test_legacy_auto_normalises_to_full_auto(
        self, director: DirectorService
    ):
        await director.set_mode("manual", updated_by="op1")
        state = await director.set_mode("auto", updated_by="op1")
        # Normalised — "auto" is never stored.
        assert state["mode"] == "full_auto"
        assert director.snapshot()["mode"] == "full_auto"

    async def test_legacy_auto_noop_when_already_full_auto(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("full_auto", updated_by="op1")
        messages.clear()
        state = await director_with_listener.set_mode("auto",
                                                      updated_by="op1")
        assert state["mode"] == "full_auto"
        # No state change -> no WS broadcast.
        assert messages == []

    async def test_semi_auto_transition(self, director: DirectorService):
        # Phase 28: semi_auto is the fresh default, so move away first —
        # set_mode to the already-active mode is an honest no-op.
        await director.set_mode("manual", updated_by="op1")
        state = await director.set_mode("semi_auto", updated_by="op1")
        assert state["mode"] == "semi_auto"
        assert state["updated_by"] == "op1"

    async def test_invalid_mode_rejected(self, director: DirectorService):
        with pytest.raises(ValueError, match="Invalid director mode"):
            await director.set_mode("turbo", updated_by="op1")

    def test_mode_request_schema_normalises_legacy_auto(self):
        assert ModeRequest(mode="auto").mode == "full_auto"
        assert ModeRequest(mode="semi_auto").mode == "semi_auto"
        assert ModeRequest(mode="manual").mode == "manual"

    def test_mode_request_schema_rejects_invalid(self):
        with pytest.raises(Exception):
            ModeRequest(mode="turbo")


# --------------------------------------------------------------------- #
# Suggestion lifecycle
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestSuggestionLifecycle:
    async def test_propose_in_semi_auto(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("semi_auto", updated_by="op1")
        messages.clear()
        suggestion = await director_with_listener.propose_suggestion(
            **_suggestion()
        )
        assert suggestion is not None
        assert suggestion["type"] == "scene"
        assert suggestion["payload"] == {"scene": "killfeed_focus"}
        assert suggestion["reason"]
        assert 0.0 <= suggestion["confidence"] <= 1.0
        assert suggestion["created_at"]
        assert suggestion["expires_at"]
        assert suggestion["status"] == "pending"
        assert suggestion["id"]
        # The suggestion is queued, the state is untouched.
        pending = director_with_listener.list_suggestions()
        assert [s["id"] for s in pending] == [suggestion["id"]]
        assert director_with_listener.snapshot()["scene"] == "main"
        # One WS frame with the suggestion shape.
        assert len(messages) == 1
        assert messages[0]["type"] == WS_SUGGESTION_TYPE
        assert messages[0]["data"]["id"] == suggestion["id"]

    async def test_propose_then_approve_applies_payload(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("semi_auto", updated_by="op1")
        suggestion = await director_with_listener.propose_suggestion(
            **_suggestion()
        )
        messages.clear()

        applied = await director_with_listener.approve_suggestion(
            suggestion["id"], updated_by="op2"
        )
        assert applied["status"] == "applied"
        # Applied exactly as a manual operator write would have.
        state = director_with_listener.snapshot()
        assert state["scene"] == "killfeed_focus"
        assert state["updated_by"] == "op2"
        # Dropped from the queue.
        assert director_with_listener.list_suggestions() == []
        # State broadcast followed by the suggestion-status broadcast.
        types = [m["type"] for m in messages]
        assert types == [WS_MESSAGE_TYPE, WS_SUGGESTION_TYPE]
        assert messages[0]["data"]["scene"] == "killfeed_focus"

    async def test_approve_overlay_payload(self, director: DirectorService):
        await director.set_mode("semi_auto", updated_by="op1")
        suggestion = await director.propose_suggestion(
            **_suggestion(
                suggestion_type="overlay",
                payload={"overlay": "killfeed", "visible": False},
                reason="Killfeed hidden during replay segment.",
            )
        )
        assert suggestion is not None
        await director.approve_suggestion(suggestion["id"],
                                          updated_by="op2")
        assert director.snapshot()["overlays"] == {
            "scorebug": True,
            "killfeed": False,
        }

    async def test_approve_lower_third_payload(self, director: DirectorService):
        await director.set_mode("semi_auto", updated_by="op1")
        suggestion = await director.propose_suggestion(
            **_suggestion(
                suggestion_type="lower_third",
                payload={
                    "visible": True,
                    "title": "MATCH POINT",
                    "subtitle": "One more round",
                },
                reason="Match point reached — lower-third carries the call.",
            )
        )
        assert suggestion is not None
        await director.approve_suggestion(suggestion["id"],
                                          updated_by="op2")
        assert director.snapshot()["lower_third"] == {
            "visible": True,
            "title": "MATCH POINT",
            "subtitle": "One more round",
        }

    async def test_propose_then_reject_drops(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("semi_auto", updated_by="op1")
        suggestion = await director_with_listener.propose_suggestion(
            **_suggestion()
        )
        messages.clear()

        rejected = await director_with_listener.reject_suggestion(
            suggestion["id"], updated_by="op2"
        )
        assert rejected["status"] == "rejected"
        assert director_with_listener.list_suggestions() == []
        # State untouched; one suggestion-status broadcast.
        assert director_with_listener.snapshot()["scene"] == "main"
        assert len(messages) == 1
        assert messages[0]["type"] == WS_SUGGESTION_TYPE
        assert messages[0]["data"]["status"] == "rejected"

    async def test_expiry_drops_silently(self, director: DirectorService):
        await director.set_mode("semi_auto", updated_by="op1")
        suggestion = await director.propose_suggestion(
            **_suggestion(ttl_seconds=1.0)
        )
        assert suggestion is not None
        # Force-expiry without sleeping: rewrite expires_at to the past.
        record = director._suggestions[suggestion["id"]]
        record["expires_at"] = "2000-01-01T00:00:00+00:00"
        assert director.list_suggestions() == []
        # Approving an expired suggestion is a 404-style miss.
        with pytest.raises(SuggestionNotFoundError):
            await director.approve_suggestion(suggestion["id"],
                                              updated_by="op2")

    async def test_unknown_id_raises_not_found(self, director: DirectorService):
        await director.set_mode("semi_auto", updated_by="op1")
        with pytest.raises(SuggestionNotFoundError):
            await director.approve_suggestion("nope", updated_by="op2")
        with pytest.raises(SuggestionNotFoundError):
            await director.reject_suggestion("nope", updated_by="op2")

    async def test_double_decide_raises_not_found(
        self, director: DirectorService
    ):
        await director.set_mode("semi_auto", updated_by="op1")
        suggestion = await director.propose_suggestion(**_suggestion())
        await director.reject_suggestion(suggestion["id"], updated_by="op2")
        with pytest.raises(SuggestionNotFoundError):
            await director.approve_suggestion(suggestion["id"],
                                              updated_by="op2")

    async def test_confidence_clamped(self, director: DirectorService):
        await director.set_mode("semi_auto", updated_by="op1")
        suggestion = await director.propose_suggestion(
            **_suggestion(confidence=9.9)
        )
        assert suggestion["confidence"] == 1.0
        suggestion = await director.propose_suggestion(
            **_suggestion(
                suggestion_type="overlay",
                payload={"overlay": "scorebug", "visible": False},
                confidence=-2.0,
            )
        )
        assert suggestion["confidence"] == 0.0

    async def test_empty_reason_rejected(self, director: DirectorService):
        await director.set_mode("semi_auto", updated_by="op1")
        with pytest.raises(ValueError, match="reason"):
            await director.propose_suggestion(**_suggestion(reason="  "))


# --------------------------------------------------------------------- #
# Mode-specific AI behaviour
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestModeGatedAI:
    async def test_full_auto_never_creates_suggestions(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("full_auto", updated_by="op1")
        messages.clear()
        suggestion = await director_with_listener.propose_suggestion(
            **_suggestion()
        )
        assert suggestion is None
        assert director_with_listener.list_suggestions() == []
        assert messages == []

    async def test_full_auto_system_publish_still_writes(
        self, director_with_listener: DirectorService
    ):
        await director_with_listener.set_mode("full_auto", updated_by="op1")
        result = await director_with_listener.system_publish(
            scene="standings", updated_by="director_ai:t1"
        )
        assert result["changed"] is True
        assert result["state"]["scene"] == "standings"

    async def test_semi_auto_system_publish_never_writes(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("semi_auto", updated_by="op1")
        messages.clear()
        result = await director_with_listener.system_publish(
            scene="standings", updated_by="director_ai:t1"
        )
        assert result["changed"] is False
        assert result["state"]["scene"] == "main"
        assert messages == []

    async def test_manual_ai_is_silent(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("manual", updated_by="op1")
        messages.clear()
        result = await director_with_listener.system_publish(
            scene="standings", updated_by="director_ai:t1"
        )
        assert result["changed"] is False
        suggestion = await director_with_listener.propose_suggestion(
            **_suggestion()
        )
        assert suggestion is None
        assert messages == []

    async def test_operator_direct_writes_rejected_in_semi_auto(
        self, director: DirectorService
    ):
        await director.set_mode("semi_auto", updated_by="op1")
        with pytest.raises(DirectorModeError):
            await director.set_scene("standings", updated_by="op1")
        with pytest.raises(DirectorModeError):
            await director.set_overlays(scorebug=False, updated_by="op1")

    async def test_approve_rejected_in_full_auto(
        self, director: DirectorService
    ):
        await director.set_mode("semi_auto", updated_by="op1")
        suggestion = await director.propose_suggestion(**_suggestion())
        await director.set_mode("full_auto", updated_by="op1")
        with pytest.raises(DirectorModeError):
            await director.approve_suggestion(suggestion["id"],
                                              updated_by="op2")

    async def test_approve_allowed_in_manual(self, director: DirectorService):
        await director.set_mode("semi_auto", updated_by="op1")
        suggestion = await director.propose_suggestion(**_suggestion())
        await director.set_mode("manual", updated_by="op1")
        applied = await director.approve_suggestion(suggestion["id"],
                                                    updated_by="op2")
        assert applied["status"] == "applied"
        assert director.snapshot()["scene"] == "killfeed_focus"


# --------------------------------------------------------------------- #
# Honesty: nothing fabricated
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestNoFabricatedSuggestions:
    async def test_duplicate_pending_suggestion_deduped(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("semi_auto", updated_by="op1")
        first = await director_with_listener.propose_suggestion(
            **_suggestion()
        )
        messages.clear()
        second = await director_with_listener.propose_suggestion(
            **_suggestion()
        )
        assert second["id"] == first["id"]
        assert len(director_with_listener.list_suggestions()) == 1
        # No second WS frame for the duplicate.
        assert messages == []

    async def test_noop_suggestion_skipped(self, director: DirectorService):
        await director.set_mode("semi_auto", updated_by="op1")
        # The program feed is already on "main" — suggesting it is a no-op.
        assert await director.propose_suggestion(
            **_suggestion(payload={"scene": "main"})
        ) is None
        assert director.list_suggestions() == []

    async def test_queue_empty_when_ai_has_nothing(
        self, director: DirectorService
    ):
        await director.set_mode("semi_auto", updated_by="op1")
        assert director.list_suggestions() == []


# --------------------------------------------------------------------- #
# WS suggestion message shape
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestSuggestionBroadcastShape:
    async def test_suggestion_message_shape(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("semi_auto", updated_by="op1")
        messages.clear()
        await director_with_listener.propose_suggestion(**_suggestion())

        assert len(messages) == 1
        message = messages[0]
        assert message["type"] == "director.suggestion"
        data = message["data"]
        for key in (
            "id",
            "type",
            "payload",
            "reason",
            "confidence",
            "created_at",
            "expires_at",
        ):
            assert key in data, f"missing key {key!r}"
        assert data["type"] == "scene"
        assert data["payload"] == {"scene": "killfeed_focus"}
        assert isinstance(data["reason"], str) and data["reason"]
        assert 0.0 <= data["confidence"] <= 1.0
