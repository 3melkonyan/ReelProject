"""The library player embeds YouTube, which refuses to play without the embedding origin."""

from fastapi.testclient import TestClient


def test_pages_send_origin_only_referrer_cross_origin():
    from reel.web.app import app

    response = TestClient(app).get("/healthz")
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"
