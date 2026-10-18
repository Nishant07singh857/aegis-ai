# backend/tests/unit/test_director_graphics.py
"""Unit tests for the Phase 8 graphics template selection on the director.

Covers:
  * Fresh director state carries the four graphics template defaults and
    GET /director/state (DirectorState schema) includes the graphics object.
  * set_graphics: valid ids update state (partial), stamp updated_by/at,
    and broadcast {"type": "director.state", "data": {...}}.
  * Unknown template ids and an empty body are rejected by the request
    schema (pydantic ValidationError -> HTTP 422 at the API boundary).
  * Mode rule: allowed in MANUAL and SEMI_AUTO; rejected in FULL_AUTO
    (DirectorModeError -> HTTP 409), with state left unchanged.
  * Template ids survive mode switches.

No network, no DB, no real credentials.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v1.director.schemas import (
    DirectorState,
    GraphicsRequest,
)
from app.services.director_service import (
    GRAPHICS_TEMPLATE_DEFAULTS,
    WS_MESSAGE_TYPE,
    DirectorModeError,
    DirectorService,
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


@pytest.mark.unit
class TestGraphicsDefaults:
    async def test_fresh_state_carries_four_defaults(
        self, director: DirectorService
    ):
        state = director.snapshot()
        assert state["graphics"] == {
            "scorebug_template_id": "scorebug-classic",
            "lowerthird_template_id": "lowerthird-slide",
            "standings_template_id": "standings-table",
            "killfeed_template_id": "killfeed-ticker",
        }
        # Defaults are exactly the contract ids with the frontend team.
        assert state["graphics"] == GRAPHICS_TEMPLATE_DEFAULTS

    async def test_state_schema_includes_graphics(
        self, director: DirectorService
    ):
        """GET /director/state returns a DirectorState that carries the
        graphics object."""
        model = DirectorState.model_validate(director.snapshot())
        assert model.graphics.scorebug_template_id == "scorebug-classic"
        assert model.graphics.lowerthird_template_id == "lowerthird-slide"
        assert model.graphics.standings_template_id == "standings-table"
        assert model.graphics.killfeed_template_id == "killfeed-ticker"


@pytest.mark.unit
class TestSetGraphics:
    async def test_set_valid_ids_updates_state_and_broadcasts(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("manual", updated_by="op1")
        messages.clear()

        state = await director_with_listener.set_graphics(
            scorebug_template_id="scorebug-modern",
            lowerthird_template_id="lowerthird-rise",
            standings_template_id="standings-cards",
            killfeed_template_id="killfeed-stack",
            updated_by="op1",
        )
        assert state["graphics"] == {
            "scorebug_template_id": "scorebug-modern",
            "lowerthird_template_id": "lowerthird-rise",
            "standings_template_id": "standings-cards",
            "killfeed_template_id": "killfeed-stack",
        }
        assert state["updated_by"] == "op1"
        assert state["updated_at"]

        assert len(messages) == 1
        assert messages[0]["type"] == WS_MESSAGE_TYPE
        assert messages[0]["data"]["graphics"] == state["graphics"]

    async def test_partial_update_leaves_other_templates(
        self, director: DirectorService
    ):
        await director.set_mode("manual", updated_by="op1")
        state = await director.set_graphics(
            killfeed_template_id="killfeed-stack", updated_by="op1"
        )
        assert state["graphics"]["killfeed_template_id"] == "killfeed-stack"
        assert state["graphics"]["scorebug_template_id"] == "scorebug-classic"
        assert state["graphics"]["lowerthird_template_id"] == "lowerthird-slide"
        assert state["graphics"]["standings_template_id"] == "standings-table"

    async def test_unknown_template_id_rejected_422(
        self, director: DirectorService
    ):
        """Unknown ids fail request validation (HTTP 422 at the API
        boundary) before the service is reached."""
        with pytest.raises(ValidationError, match="Invalid template id"):
            GraphicsRequest(scorebug_template_id="scorebug-nope")

    async def test_unknown_template_id_each_type(self):
        for field in (
            "scorebug_template_id",
            "lowerthird_template_id",
            "standings_template_id",
            "killfeed_template_id",
        ):
            with pytest.raises(ValidationError, match="Invalid template id"):
                GraphicsRequest(**{field: "bogus-template"})

    async def test_unknown_template_id_service_guard(self, director: DirectorService):
        """Defensive service-level guard (already-validated by the schema
        above, but the service never trusts its caller)."""
        await director.set_mode("manual", updated_by="op1")
        with pytest.raises(ValueError, match="Invalid template id"):
            await director.set_graphics(
                standings_template_id="standings-nope", updated_by="op1"
            )

    async def test_empty_body_rejected_422(self):
        """{} carries no template id -> schema ValidationError (HTTP 422)."""
        with pytest.raises(ValidationError, match="at least one"):
            GraphicsRequest()

    async def test_empty_set_graphics_rejected(self, director: DirectorService):
        await director.set_mode("manual", updated_by="op1")
        with pytest.raises(ValueError, match="at least one"):
            await director.set_graphics(updated_by="op1")

    async def test_template_id_from_wrong_type_rejected(self):
        """A valid id for one type is invalid for another."""
        with pytest.raises(ValidationError, match="Invalid template id"):
            GraphicsRequest(killfeed_template_id="scorebug-classic")

    async def test_template_id_whitespace_trimmed(self, director: DirectorService):
        request = GraphicsRequest(scorebug_template_id="  scorebug-minimal ")
        assert request.scorebug_template_id == "scorebug-minimal"


@pytest.mark.unit
class TestGraphicsModeRule:
    async def test_full_auto_rejects_with_409_and_state_unchanged(
        self, director_with_listener: DirectorService, messages: list
    ):
        assert director_with_listener.snapshot()["mode"] == "full_auto"
        before = director_with_listener.snapshot()
        messages.clear()

        with pytest.raises(DirectorModeError):
            await director_with_listener.set_graphics(
                scorebug_template_id="scorebug-modern", updated_by="op1"
            )

        # State (and the WS stream) are untouched.
        assert director_with_listener.snapshot()["graphics"] == before["graphics"]
        assert messages == []

    async def test_semi_auto_allows_operator_write(
        self, director: DirectorService
    ):
        """Unlike scene/overlay/lower-third, graphics selection is an
        operator write in BOTH manual and semi_auto."""
        await director.set_mode("semi_auto", updated_by="op1")
        # Scene writes are still gated here — graphics is the exception.
        with pytest.raises(DirectorModeError):
            await director.set_scene("standings", updated_by="op1")

        state = await director.set_graphics(
            lowerthird_template_id="lowerthird-banner", updated_by="op1"
        )
        assert state["graphics"]["lowerthird_template_id"] == "lowerthird-banner"
        assert state["updated_by"] == "op1"

    async def test_manual_allows_operator_write(self, director: DirectorService):
        await director.set_mode("manual", updated_by="op1")
        state = await director.set_graphics(
            standings_template_id="standings-cards", updated_by="op1"
        )
        assert state["graphics"]["standings_template_id"] == "standings-cards"

    async def test_template_ids_survive_mode_switches(
        self, director: DirectorService
    ):
        await director.set_mode("manual", updated_by="op1")
        await director.set_graphics(
            scorebug_template_id="scorebug-minimal",
            lowerthird_template_id="lowerthird-rise",
            updated_by="op1",
        )
        for mode in ("semi_auto", "full_auto", "manual"):
            await director.set_mode(mode, updated_by="op1")
            graphics = director.snapshot()["graphics"]
            assert graphics["scorebug_template_id"] == "scorebug-minimal"
            assert graphics["lowerthird_template_id"] == "lowerthird-rise"
            assert graphics["standings_template_id"] == "standings-table"
            assert graphics["killfeed_template_id"] == "killfeed-ticker"
