# backend/tests/unit/test_director.py
"""Unit tests for the Phase 6 director service + role-based auth deps.

Covers:
  * DirectorService state machine (auto<->manual, mode-gated operator
    writes, system_publish rules)
  * WS broadcast shape: every change publishes
    {"type": "director.state", "data": {<full state>}}
  * Role deps: require_role / require_admin / require_operator, principal
    role resolution, 401 for unauthenticated, 403 for insufficient role.

No network, no DB, no real credentials.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.deps import (
    ADMIN_PRINCIPAL,
    get_current_user,
    principal_role,
    require_admin,
    require_operator,
    require_role,
)
from app.core.security import TokenPayload
from app.services.director_service import (
    WS_MESSAGE_TYPE,
    DirectorModeError,
    DirectorService,
)


def _principal(role: str = "viewer", **kwargs) -> TokenPayload:
    kwargs.setdefault("user_id", "u-1")
    kwargs.setdefault("username", f"test-{role}")
    return TokenPayload(role=role, **kwargs)


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


# --------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestDirectorStateMachine:
    async def test_defaults(self, director: DirectorService):
        state = director.snapshot()
        # Phase 28: fresh-show default is semi_auto (was full_auto).
        assert state["mode"] == "semi_auto"
        assert state["scene"] == "main"
        assert state["overlays"] == {"scorebug": True, "killfeed": True}
        assert state["lower_third"] == {
            "visible": False,
            "title": "",
            "subtitle": "",
        }
        assert state["updated_by"] is None
        assert state["updated_at"]

    async def test_mode_full_auto_to_manual_and_back(
        self, director: DirectorService
    ):
        state = await director.set_mode("manual", updated_by="op1")
        assert state["mode"] == "manual"
        assert state["updated_by"] == "op1"
        state = await director.set_mode("full_auto", updated_by="op1")
        assert state["mode"] == "full_auto"

    async def test_set_mode_invalid(self, director: DirectorService):
        with pytest.raises(ValueError, match="Invalid director mode"):
            await director.set_mode("turbo", updated_by="op1")

    async def test_set_scene_rejected_in_full_auto(self, director: DirectorService):
        await director.set_mode("full_auto", updated_by="op1")
        assert director.snapshot()["mode"] == "full_auto"
        with pytest.raises(DirectorModeError):
            await director.set_scene("standings", updated_by="op1")

    async def test_set_scene_manual_ok(self, director: DirectorService):
        await director.set_mode("manual", updated_by="op1")
        state = await director.set_scene("standings", updated_by="op1")
        assert state["scene"] == "standings"
        assert state["updated_by"] == "op1"

    async def test_set_scene_invalid(self, director: DirectorService):
        await director.set_mode("manual", updated_by="op1")
        with pytest.raises(ValueError, match="Invalid director scene"):
            await director.set_scene("replay_cam", updated_by="op1")

    async def test_set_overlays_partial(self, director: DirectorService):
        await director.set_mode("manual", updated_by="op1")
        state = await director.set_overlays(killfeed=False, updated_by="op1")
        assert state["overlays"] == {"scorebug": True, "killfeed": False}

    async def test_set_overlays_empty_rejected(self, director: DirectorService):
        await director.set_mode("manual", updated_by="op1")
        with pytest.raises(ValueError, match="at least one"):
            await director.set_overlays(updated_by="op1")

    async def test_set_overlays_rejected_in_full_auto(self, director: DirectorService):
        await director.set_mode("full_auto", updated_by="op1")
        with pytest.raises(DirectorModeError):
            await director.set_overlays(scorebug=False, updated_by="op1")

    async def test_set_lower_third(self, director: DirectorService):
        await director.set_mode("manual", updated_by="op1")
        state = await director.set_lower_third(
            visible=True, title="Finals", subtitle="Map 3", updated_by="op1"
        )
        assert state["lower_third"] == {
            "visible": True,
            "title": "Finals",
            "subtitle": "Map 3",
        }
        state = await director.set_lower_third(visible=False, updated_by="op1")
        assert state["lower_third"]["visible"] is False

    async def test_set_lower_third_rejected_in_full_auto(self, director: DirectorService):
        await director.set_mode("full_auto", updated_by="op1")
        with pytest.raises(DirectorModeError):
            await director.set_lower_third(visible=True, updated_by="op1")

    async def test_system_publish_applies_in_full_auto(
        self, director_with_listener: DirectorService, messages: list
    ):
        # Drive a visible change: manual -> standings -> auto, then the
        # system pulls the program feed back to main.
        await director_with_listener.set_mode("manual", updated_by="op1")
        await director_with_listener.set_scene("standings", updated_by="op1")
        await director_with_listener.set_mode("full_auto", updated_by="op1")
        messages.clear()

        result = await director_with_listener.system_publish(
            scene="main", updated_by="director_ai:t1"
        )
        assert result["changed"] is True
        assert result["state"]["scene"] == "main"
        assert result["state"]["updated_by"] == "director_ai:t1"
        assert len(messages) == 1
        assert messages[0]["type"] == WS_MESSAGE_TYPE

    async def test_system_publish_noop_in_manual(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("manual", updated_by="op1")
        await director_with_listener.set_scene("standings", updated_by="op1")
        messages.clear()

        result = await director_with_listener.system_publish(
            scene="main", updated_by="director_ai:t1"
        )
        # Manual mode: the simulator must never override the operator.
        assert result["changed"] is False
        assert result["state"]["scene"] == "standings"
        assert messages == []

    async def test_system_publish_no_visible_change_no_broadcast(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("full_auto", updated_by="op1")
        messages.clear()
        result = await director_with_listener.system_publish(
            scene="main", updated_by="director_ai:t1"
        )
        assert result["changed"] is False
        assert messages == []


# --------------------------------------------------------------------- #
# WS broadcast shape
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestDirectorBroadcast:
    async def test_operator_change_publishes_full_state(
        self, director_with_listener: DirectorService, messages: list
    ):
        await director_with_listener.set_mode("manual", updated_by="op1")
        messages.clear()
        await director_with_listener.set_scene("killfeed_focus", updated_by="op1")

        assert len(messages) == 1
        message = messages[0]
        assert message["type"] == "director.state"
        data = message["data"]
        assert data["mode"] == "manual"
        assert data["scene"] == "killfeed_focus"
        assert data["overlays"] == {"scorebug": True, "killfeed": True}
        assert data["lower_third"] == {
            "visible": False,
            "title": "",
            "subtitle": "",
        }
        assert data["updated_by"] == "op1"
        assert data["updated_at"]

    async def test_remove_listener(self, director: DirectorService, messages: list):
        async def _listener(message: dict) -> None:
            messages.append(message)

        director.add_listener(_listener)
        director.remove_listener(_listener)
        await director.set_mode("manual", updated_by="op1")
        assert messages == []


# --------------------------------------------------------------------- #
# Role dependencies
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestPrincipalRole:
    def test_explicit_roles(self):
        assert principal_role(_principal("admin")) == "admin"
        assert principal_role(_principal("operator")) == "operator"
        assert principal_role(_principal("viewer")) == "viewer"

    def test_role_case_insensitive(self):
        assert principal_role(_principal("Admin")) == "admin"

    def test_unknown_role_degrades_to_viewer(self):
        assert principal_role(_principal("root")) == "viewer"

    def test_legacy_token_without_role_claim(self):
        """Pre-Phase-6 tokens carry no role claim; decode derives it."""
        import jwt as pyjwt
        from datetime import datetime, timedelta

        from app.config import settings
        from app.core.security import security_manager

        secret = settings.SECRET_KEY.get_secret_value()

        def _legacy_token(**claims):
            payload = {
                "sub": "op",
                "username": "op",
                "is_superuser": False,
                "scopes": [],
                "exp": datetime.utcnow() + timedelta(minutes=5),
                "iat": datetime.utcnow(),
                "type": "access",
            }
            payload.update(claims)
            return pyjwt.encode(payload, secret, algorithm=settings.JWT_ALGORITHM)

        assert (
            security_manager.decode_token(
                _legacy_token(is_superuser=True)
            ).role
            == "admin"
        )
        assert (
            security_manager.decode_token(_legacy_token()).role == "viewer"
        )
        assert (
            security_manager.decode_token(
                _legacy_token(scopes=["operator"])
            ).role
            == "operator"
        )

    def test_static_api_key_principal_is_admin(self):
        # The X-API-Key principal keeps its Phase 1 admin privileges.
        assert principal_role(ADMIN_PRINCIPAL) == "admin"

    def test_default_principal_is_viewer(self):
        assert principal_role(TokenPayload(user_id="x", username="x")) == "viewer"


@pytest.mark.unit
class TestRequireRole:
    async def test_unauthenticated_401(self):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(bearer=None, api_key=None)
        assert exc.value.status_code == 401

    async def test_viewer_blocked_from_operator_endpoints(self):
        dep = require_operator
        with pytest.raises(HTTPException) as exc:
            await dep(user=_principal("viewer"))
        assert exc.value.status_code == 403

    async def test_operator_allowed(self):
        dep = require_operator
        user = await dep(user=_principal("operator"))
        assert user.username == "test-operator"

    async def test_admin_passes_require_operator(self):
        """Admins can do everything operators can."""
        dep = require_operator
        user = await dep(user=_principal("admin"))
        assert principal_role(user) == "admin"

    async def test_operator_blocked_from_require_admin(self):
        with pytest.raises(HTTPException) as exc:
            await require_admin(user=_principal("operator"))
        assert exc.value.status_code == 403

    async def test_admin_allowed_require_admin(self):
        user = await require_admin(user=_principal("admin"))
        assert principal_role(user) == "admin"

    async def test_require_role_factory_multiple(self):
        dep = require_role("operator", "viewer")
        assert (await dep(user=_principal("viewer"))).role == "viewer"
        with pytest.raises(HTTPException) as exc:
            await dep(user=_principal("admin"))
        assert exc.value.status_code == 403

    def test_require_role_rejects_unknown_role(self):
        with pytest.raises(ValueError, match="Unknown role"):
            require_role("superadmin")
