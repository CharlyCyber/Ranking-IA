"""Dashboard Flask v4: /health, API abierta, RSS, SVG, buscador y validación de parámetros."""

import copy

import pytest

from src.keepalive import flask_app


@pytest.fixture
def client():
    flask_app.config["testing"] = True
    return flask_app.test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["update_interval_minutes"] == 60
    assert "chromium_ready" in body


def test_root_redirige_navegador_y_responde_json_a_api(client):
    browser_resp = client.get("/", headers={"Accept": "text/html"})
    assert browser_resp.status_code == 302
    assert "/dashboard" in browser_resp.headers["Location"]

    api_resp = client.get("/", headers={"Accept": "application/json"})
    assert api_resp.status_code == 200
    assert api_resp.get_json()["status"] == "ok"


def test_dashboard_renderiza(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert b"chartElo" in resp.data
    assert b"selSource" in resp.data
    assert b"modelSearch" in resp.data
    assert b"white-sheet" in resp.data
    assert b"sheet-notch" in resp.data
    assert b"detail-dark-card" in resp.data


def test_elo_history_series_temporales(client, monkeypatch, v3_snapshot):
    snap1 = v3_snapshot
    snap2 = copy.deepcopy(snap1)
    snap2["generated_at"] = "2026-10-21T10:30:00"
    for m in snap2["sources"]["arena"]["models"]:
        if m["category"] == "Text" and m["rank"] == 1:
            m["score"] = 1510

    class FakeDb:
        def get_snapshots(self, days=30):
            return [
                {"created_at": "2026-10-21T10:30:00", "data": snap2},
                {"created_at": "2026-10-21T10:00:00", "data": snap1},
            ]

        def get_latest_snapshot(self):
            return {"created_at": "2026-10-21T10:30:00", "data": snap2}

        def get_changes(self, days=30):
            return [{"detected_at": "2026-10-21T10:30:00", "summary": "claude-opus-4-6 sube a 1510"}]

    monkeypatch.setattr("src.web.db", FakeDb())

    resp = client.get("/api/elo-history?source=arena&category=Text")
    assert resp.status_code == 200
    payload = resp.get_json()
    serie = payload["series"]["claude-opus-4-6"]
    assert [p[1] for p in serie] == [1496, 1510]

    resp2 = client.get("/api/elo-history?source=arena&limit=3")
    assert len(resp2.get_json()["series"]) <= 3

    # Parámetros malformados o fuera de rango no deben causar HTTP 500
    resp_bad = client.get("/api/elo-history?days=basura&limit=-999")
    assert resp_bad.status_code == 200

    # Endpoints públicos extra: búsqueda, badge, SVG y RSS
    search_resp = client.get("/api/models/search?q=claude")
    assert search_resp.status_code == 200
    assert search_resp.get_json()["count"] >= 1

    badge_resp = client.get("/api/badge/arena")
    assert badge_resp.status_code == 200
    assert badge_resp.get_json()["schemaVersion"] == 1
    assert "claude-opus-4-6" in badge_resp.get_json()["message"]

    svg_resp = client.get("/api/chart.svg?source=arena&category=Text")
    assert svg_resp.status_code == 200
    assert b"<svg" in svg_resp.data

    rss_resp = client.get("/feed.xml")
    assert rss_resp.status_code == 200
    assert b"<rss" in rss_resp.data
    assert b"claude-opus-4-6" in rss_resp.data


def test_elo_history_vacio_con_snapshots_legacy(client, monkeypatch):
    class FakeDb:
        def get_snapshots(self, days=30):
            return [{"created_at": "2026-10-01T10:00:00", "data": {"arena": "texto crudo"}}]

    monkeypatch.setattr("src.web.db", FakeDb())
    resp = client.get("/api/elo-history?source=arena")
    assert resp.status_code == 200
    assert resp.get_json()["series"] == {}


def test_api_rankings_y_404_sin_datos(client, monkeypatch):
    class EmptyDb:
        def get_latest_snapshot(self):
            return None

    monkeypatch.setattr("src.web.db", EmptyDb())
    assert client.get("/api/rankings").status_code == 404
