# backend/tests/unit/test_phase32_feed_token_stream.py
"""Phase 32 (K-C1 + K-C2) tests.

K-C1 — signed feed token (backend/app/api/v1/video/feed_token.py):
  * mint/verify round-trip; tamper / wrong secret / expiry / malformed /
    wrong-scope all fail closed; TTL bounds enforced.
  * Router level (TestClient, finite MJPEG stand-in like the Integrity
    H3 tests): unauthenticated feed is STILL 401 (H3 not weakened);
    operator-minted token opens the feed (feed-wide: one token works on
    any slot, so director slot switches do not 401); tampered/expired
    tokens are 401; a feed token resolves to a viewer principal, so it
    cannot pass an operator gate (mint endpoint itself: 401 unauthenticated).
K-C2 — API stream path deprecated:
  * POST /stream/start and /stream/stop return 410 Gone (operator auth
    still required first: unauthenticated is 401, not 410);
  * GET /stream/status returns state "deprecated", never idle/running.

No network, no real binaries, no real stream keys, no DB (the X-API-Key
path authenticates without a DB row, exactly as in the Integrity tests).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps as deps_module
from app.api.v1.video import feed_token as feed_token_module
from app.api.v1.video import router as video_router
from app.api.v1.video.feed_token import (
    FEED_TOKEN_PRINCIPAL,
    FeedTokenError,
    mint_feed_token,
    verify_feed_token,
)

pytestmark = pytest.mark.unit

_TEST_SECRET = "phase32-test-signing-secret"


# ── pure mint / verify ─────────────────────────────────────────────────


def test_mint_verify_roundtrip():
    token = mint_feed_token(secret=_TEST_SECRET, now=1_000_000)
    assert verify_feed_token(token, secret=_TEST_SECRET, now=1_000_000 + 599)
    assert not verify_feed_token(token, secret=_TEST_SECRET, now=1_000_000 + 600)


def test_tamper_and_wrong_secret_fail():
    token = mint_feed_token(secret=_TEST_SECRET, now=1_000_000)
    head, payload, sig = token.split(".")
    assert not verify_feed_token(f"{head}.{payload}X.{sig}", secret=_TEST_SECRET)
    assert not verify_feed_token(f"{head}.{payload}.{sig}X", secret=_TEST_SECRET)
    assert not verify_feed_token(token, secret="a-different-secret")


def test_malformed_and_wrong_scope_fail():
    assert not verify_feed_token(None, secret=_TEST_SECRET)
    assert not verify_feed_token("", secret=_TEST_SECRET)
    assert not verify_feed_token("not-a-token", secret=_TEST_SECRET)
    assert not verify_feed_token("ft1.only-two.parts", secret=_TEST_SECRET)
    assert not verify_feed_token("xx1.a.b", secret=_TEST_SECRET)
    # Re-signed payload with a different scope must still fail.
    import base64
    import hashlib
    import hmac
    import json

    payload = {"scope": "not-the-feed", "exp": 9_999_999_999, "nonce": "x"}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    signing_input = f"ft1.{encoded}".encode()
    sig = base64.urlsafe_b64encode(
        hmac.new(_TEST_SECRET.encode(), signing_input, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    assert not verify_feed_token(f"ft1.{encoded}.{sig}", secret=_TEST_SECRET)


def test_ttl_bounds_enforced():
    with pytest.raises(FeedTokenError):
        mint_feed_token(59, secret=_TEST_SECRET)
    with pytest.raises(FeedTokenError):
        mint_feed_token(3601, secret=_TEST_SECRET)
    assert verify_feed_token(mint_feed_token(60, secret=_TEST_SECRET, now=100),
                             secret=_TEST_SECRET, now=101)
    assert verify_feed_token(mint_feed_token(3600, secret=_TEST_SECRET, now=100),
                             secret=_TEST_SECRET, now=101)


def test_feed_token_principal_is_viewer_only():
    assert FEED_TOKEN_PRINCIPAL.role == "viewer"
    assert FEED_TOKEN_PRINCIPAL.is_superuser is False


# ── router level: video feed + mint endpoint ───────────────────────────


def _video_app(monkeypatch):
    async def _finite_stream(slot_number, sim, fps, quality):
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\nFAKE\r\n"

    monkeypatch.setattr(video_router, "_mjpeg_program_stream", _finite_stream)
    # Mint/verify against a fixed test secret (never the real SECRET_KEY).
    monkeypatch.setattr(
        feed_token_module, "_signing_secret", lambda: _TEST_SECRET
    )
    # Operator auth via the static X-API-Key path (no DB row needed).
    monkeypatch.setattr(deps_module.settings, "ADMIN_API_KEYS", ["phase32-op-key"])
    app = FastAPI()
    app.include_router(video_router.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _op_headers():
    return {"X-API-Key": "phase32-op-key"}


def test_feed_still_401_without_any_credential(monkeypatch):
    client = _video_app(monkeypatch)
    assert client.get("/api/v1/video/feed/1").status_code == 401


def test_mint_requires_operator_and_token_opens_feed(monkeypatch):
    client = _video_app(monkeypatch)
    # Unauthenticated mint: 401 (the mint endpoint is operator-gated).
    assert client.post("/api/v1/video/feed-token", json={}).status_code == 401
    resp = client.post("/api/v1/video/feed-token", json={}, headers=_op_headers())
    assert resp.status_code == 200
    body = resp.json()
    token = body["feed_token"]
    assert body["ttl_seconds"] == 600
    assert body["expires_at"] is not None
    assert "{slot}" in body["feed_url_template"]
    # Token opens the feed — and is feed-wide, so a director slot switch
    # (slot 1 → slot 2) does not 401 mid-broadcast.
    for slot in (1, 2):
        feed = client.get(f"/api/v1/video/feed/{slot}?feed_token={token}")
        assert feed.status_code == 200
        assert feed.headers["content-type"].startswith("multipart/x-mixed-replace")
    # Full header auth still works alongside the token path.
    feed = client.get("/api/v1/video/feed/1", headers=_op_headers())
    assert feed.status_code == 200


def test_tampered_and_expired_tokens_rejected(monkeypatch):
    client = _video_app(monkeypatch)
    resp = client.post("/api/v1/video/feed-token", json={}, headers=_op_headers())
    token = resp.json()["feed_token"]
    head, payload, sig = token.split(".")
    assert client.get(f"/api/v1/video/feed/1?feed_token={head}.{payload}X.{sig}").status_code == 401
    # Token minted in the past (expired long before "now").
    expired = mint_feed_token(secret=_TEST_SECRET, now=1000)
    assert client.get(f"/api/v1/video/feed/1?feed_token={expired}").status_code == 401


def test_mint_ttl_bounds_rejected_by_schema(monkeypatch):
    client = _video_app(monkeypatch)
    assert (
        client.post("/api/v1/video/feed-token", json={"ttl_seconds": 30},
                    headers=_op_headers()).status_code == 422
    )
    assert (
        client.post("/api/v1/video/feed-token", json={"ttl_seconds": 99999},
                    headers=_op_headers()).status_code == 422
    )


# ── router level: stream deprecation (K-C2) ─────────────────────────────


def _stream_app(monkeypatch):
    from app.api.v1.stream import router as stream_router

    monkeypatch.setattr(deps_module.settings, "ADMIN_API_KEYS", ["phase32-op-key"])
    app = FastAPI()
    app.include_router(stream_router.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_api_stream_start_stop_are_410(monkeypatch):
    client = _stream_app(monkeypatch)
    # Auth still gates first: unauthenticated is 401, not 410.
    assert client.post("/api/v1/stream/start", json={"destinations": ["youtube"]}).status_code == 401
    for path, body in (
        ("/api/v1/stream/start", {"destinations": ["youtube"]}),
        ("/api/v1/stream/stop", {}),
    ):
        resp = client.post(path, json=body, headers=_op_headers())
        assert resp.status_code == 410
        assert "deprecated" in resp.json()["detail"]
        assert "streaming" in resp.json()["detail"]


def test_api_stream_status_reports_deprecated(monkeypatch):
    client = _stream_app(monkeypatch)
    assert client.get("/api/v1/stream/status").status_code == 401
    resp = client.get("/api/v1/stream/status", headers=_op_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "deprecated"
    assert body["state"] not in ("idle", "running")
    assert "deprecated" in (body["last_error"] or "")
