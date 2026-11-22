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
    assert body["version"] == "4.4.0"
    from src.config import UPDATE_INTERVAL_MINUTES

    assert body["update_interval_minutes"] == UPDATE_INTERVAL_MINUTES
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
    assert b"simpleExplainBox" in resp.data
    assert b"guideBanner" in resp.data
    assert b"btnTryModel" in resp.data


def test_seed_actualizado_contiene_claude_opus_5_5(client):
    resp = client.get("/api/models/search?q=Claude+Opus+5.5")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] >= 1
    assert any("Claude Opus 5.5" in r["model"] for r in body["results"])


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


def test_elo_history_usa_tabla_compacta_sin_leer_snapshots(client, monkeypatch, tmp_path, v3_snapshot):
    from src.db import Database

    real_db = Database(str(tmp_path / "elo.db"))
    real_db.save_snapshot(v3_snapshot)

    # Simulamos que get_snapshots está vacío para demostrar que lee de elo_history
    monkeypatch.setattr(real_db, "get_snapshots", lambda days=30: [])
    monkeypatch.setattr("src.web.db", real_db)

    resp = client.get("/api/elo-history?source=arena&category=Text")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "public, max-age=60"
    series = resp.get_json()["series"]
    assert "claude-opus-4-6" in series
    assert series["claude-opus-4-6"][0][1] == 1496

def test_public_api_has_basic_per_ip_rate_limit(client, monkeypatch):
    import src.web as web_module

    monkeypatch.setattr(web_module, "API_RATE_LIMIT_PER_MINUTE", 2)
    web_module._API_REQUESTS.clear()
    headers = {"REMOTE_ADDR": "198.51.100.77"}
    assert client.get("/api/history", headers=headers).status_code == 200
    assert client.get("/api/history", headers=headers).status_code == 200
    limited = client.get("/api/history", headers=headers)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"


def test_health_in_web_role_reads_shared_worker_heartbeat(client, monkeypatch):
    from datetime import datetime, timezone

    import src.keepalive as keepalive

    class SharedDb:
        _turso_endpoint = "https://db.example"

        @staticmethod
        def get_state(_key):
            return datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr(keepalive, "APP_MODE", "web")
    monkeypatch.setattr(keepalive, "web_db", SharedDb())
    monkeypatch.setitem(keepalive._WORKER_STATUS_CACHE, "checked_at", 0.0)
    monkeypatch.setitem(keepalive.RUNTIME_STATE, "bot_polling_active", False)
    assert client.get("/health").get_json()["bot_polling_active"] is True
def test_api_freshness_reports_per_source_last_success(client, monkeypatch):
    import src.web as web_module

    class SnapshotDb:
        def get_or_seed_latest_snapshot(self):
            return {
                "created_at": "2026-11-22T04:33:00",
                "data": {
                    "generated_at": "2026-11-22T04:33:00",
                    "sources": {
                        "openrouter": {
                            "status": "stale",
                            "last_checked_at": "2026-11-22T04:33:00",
                            "last_successful_fetch_at": "2026-11-22T04:20:00",
                            "stale_since": "2026-11-22T04:33:00",
                            "error": "OpenRouter parser returned too few rows",
                        }
                    },
                },
            }

    monkeypatch.setattr(web_module, "db", SnapshotDb())
    response = client.get("/api/freshness")
    assert response.status_code == 200
    body = response.get_json()
    assert body["snapshot_generated_at"] == "2026-11-22T04:33:00"
    source = body["sources"]["openrouter"]
    assert source["status"] == "stale"
    assert source["last_successful_fetch_at"] == "2026-11-22T04:20:00"
    assert source["last_success_age_minutes"] is not None
    assert source["last_error"] == "OpenRouter parser returned too few rows"
