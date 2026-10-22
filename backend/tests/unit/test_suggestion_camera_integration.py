"""Phase 25 integration: scene suggestions can carry a camera slot.

Coordinator integration fix — previously DirectorService suggestion
payloads validated to {"scene"} only, so the live director's semi_auto
proposals could name a slot in prose but approving one never moved the
camera. Now the payload may carry active_camera (+ team_id) and
approval applies scene AND camera as one write.
"""
import pytest

from app.services.director_service import DirectorService


@pytest.fixture()
def svc():
    return DirectorService()


async def _to_semi_auto(svc):
    svc._state["mode"] = "semi_auto"


@pytest.mark.asyncio
async def test_suggestion_with_camera_applies_camera_on_approve(svc):
    await _to_semi_auto(svc)
    before = svc.snapshot()
    assert before["scene"] == "main" or before["active_camera"] != 7
    s = await svc.propose_suggestion(
        suggestion_type="scene",
        payload={"scene": "main", "active_camera": 7, "team_id": "team-b"},
        reason="Live director: team-b wins. Approve to put slot 7 on program.",
        confidence=0.85,
        created_by="live_director",
    )
    assert s is not None
    assert s["payload"]["active_camera"] == 7
    assert s["payload"]["team_id"] == "team-b"
    await svc.approve_suggestion(s["id"], updated_by="operator")
    after = svc.snapshot()
    assert after["active_camera"] == 7
    assert after["scene"] == "main"


@pytest.mark.asyncio
async def test_same_scene_different_camera_is_not_a_noop(svc):
    await _to_semi_auto(svc)
    # current state: scene "main", some camera != 5
    s = await svc.propose_suggestion(
        suggestion_type="scene",
        payload={"scene": svc.snapshot()["scene"], "active_camera": 5},
        reason="Move camera only.",
        confidence=0.5,
        created_by="live_director",
    )
    assert s is not None, "camera-only change must not be pruned as a no-op"


@pytest.mark.asyncio
async def test_same_scene_same_camera_is_a_noop(svc):
    await _to_semi_auto(svc)
    snap = svc.snapshot()
    s = await svc.propose_suggestion(
        suggestion_type="scene",
        payload={"scene": snap["scene"], "active_camera": snap["active_camera"]},
        reason="Nothing would change.",
        confidence=0.5,
        created_by="live_director",
    )
    assert s is None


@pytest.mark.asyncio
async def test_scene_only_suggestion_behaves_as_before(svc):
    await _to_semi_auto(svc)
    cam_before = svc.snapshot()["active_camera"]
    s = await svc.propose_suggestion(
        suggestion_type="scene",
        payload={"scene": "map_cam"},
        reason="Show the map.",
        confidence=0.5,
        created_by="director_ai",
    )
    assert s is not None
    assert "active_camera" not in s["payload"]
    await svc.approve_suggestion(s["id"], updated_by="operator")
    after = svc.snapshot()
    assert after["scene"] == "map_cam"
    assert after["active_camera"] == cam_before


@pytest.mark.asyncio
async def test_invalid_camera_in_suggestion_rejected(svc):
    await _to_semi_auto(svc)
    with pytest.raises(ValueError):
        await svc.propose_suggestion(
            suggestion_type="scene",
            payload={"scene": "main", "active_camera": 99},
            reason="Bad slot.",
            confidence=0.5,
            created_by="live_director",
        )
