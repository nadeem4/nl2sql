"""The page, its assets, the clips and the social card (docs/architecture/wave_d.md, "nl2sql-api serve", A4b)."""
import re

import pytest
from fastapi.testclient import TestClient

from nl2sql_api.main import create_app
from nl2sql_api.routes import web

PAGE = '<!doctype html><html><head><title>nl2sql</title></head><body><div id="root"></div></body></html>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    static, assets = tmp_path / "static", tmp_path / "assets"
    (static / "assets").mkdir(parents=True)
    (assets / "clips").mkdir(parents=True)
    (static / "index.html").write_text(PAGE, encoding="utf-8")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (static / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (assets / "clips" / "ask.jpg").write_bytes(b"\xff\xd8\xff poster")
    (assets / "clips" / "ask.webm").write_bytes(b"\x1a\x45\xdf\xa3 clip")
    (assets / "social-card.png").write_bytes(b"\x89PNG\r\n\x1a\n card")
    monkeypatch.setattr(web, "STATIC_DIR", static)
    monkeypatch.setattr(web, "ASSETS_DIR", assets)
    return TestClient(create_app())


def test_page_and_clips_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in page.text
    assert client.get("/assets/index-abc123.js").text == "console.log(1)"
    assert client.get("/favicon.svg").text == "<svg/>"
    poster, video = client.get("/clips/ask.jpg"), client.get("/clips/ask.webm")
    assert poster.status_code == 200 and poster.headers["content-type"] == "image/jpeg"
    assert video.status_code == 200 and video.headers["content-type"] == "video/webm"
    card = client.get("/social-card.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"


def test_unknown_path_serves_the_page(client):
    for path in ("/chat/abc", "/dashboards", "/some/deep/path"):
        response = client.get(path)
        assert response.status_code == 200 and '<div id="root">' in response.text, path


def test_a_missing_asset_or_clip_is_a_404_not_the_page(client):
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/clips/pipeline.webm").status_code == 404
    for bad in ("ask.py", "ASK.jpg"):
        assert client.get(f"/clips/{bad}").status_code == 404, bad


def test_no_path_leads_outside_the_page_folder(client):
    for bad in ("/assets/..%2F..%2Fsecret.txt", "/..%2Fsecret.txt", "/%2e%2e/secret.txt"):
        assert "secret" not in client.get(bad).text, bad


def test_an_unknown_api_path_is_the_envelope_not_the_page(client):
    response = client.get("/api/v1/no-such-route")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_an_unbuilt_page_says_how_to_build_it(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path / "nothing")
    response = TestClient(create_app()).get("/")
    assert response.status_code == 503 and "npm run build" in response.text


def test_the_page_and_assets_are_not_in_the_openapi_document():
    paths = create_app().openapi()["paths"]
    assert not [p for p in paths if not p.startswith("/api/v1/")]


def test_the_shipped_clips_are_small_and_come_in_pairs():
    """Each feature has a light and a dark poster; a recorded clip stays under 2 MB."""
    for feature in ("ask", "pipeline", "retrieval"):
        for suffix in ("", "-dark"):
            assert (web.ASSETS_DIR / "clips" / f"{feature}{suffix}.jpg").is_file(), f"{feature}{suffix}.jpg"
    assert (web.ASSETS_DIR / "social-card.png").is_file()
    for clip in (web.ASSETS_DIR / "clips").glob("*.webm"):
        assert clip.stat().st_size < 2 * 1024 * 1024, clip.name
    assert re.fullmatch(r"[a-z]+(-dark)?\.(webm|jpg)", "ask-dark.webm")
