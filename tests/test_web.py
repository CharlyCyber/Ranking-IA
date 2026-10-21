"""Dashboard Flask: /health, API de rankings y evolución de ELO."""

import pytest

from src.keepalive import flask_app


@pytest.fixture
def client():
    flask_app.config["testing"] = True
    return flask_app.test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_dashboard_renderiza(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert b"chartElo" in resp.data
    assert b"selSource" in resp.data


def test_elo_history_series_temporales(client, monkeypatch, v3_snapshot):
    import copy

    snap1 = v3_snapshot  #10:00
    snap2 = copy.deepcopy(snap1)  # copia profunda: los modelos no deben compartirse
    snap2["generated_at"] = "2026-10-21T10:30:00"
    # segunda pasada: sube el score del nº1 de Text
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

    monkeypatch.setattr("src.web.db", FakeDb())

    resp = client.get("/api/elo-history?source=arena&category=Text")
    assert resp.status_code == 200
    payload = resp.get_json()
    serie = payload["series"]["claude-opus-4-6"]
    assert [p[1] for p in serie] == [1496, 1510]

    # todas las series deben respetar el límite
    resp2 = client.get("/api/elo-history?source=arena&limit=3")
    assert len(resp2.get_json()["series"]) <= 3


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
