from src.db import Database


def test_turso_health_distinguishes_unconfigured_from_reachable(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "local.db"))
    assert db.turso_health() == {"configured": False, "reachable": False}

    remote = Database(str(tmp_path / "remote.db"))
    remote._turso_endpoint = "https://example.turso.io/v2/pipeline"
    remote._turso_token = "test-token"
    monkeypatch.setattr(remote, "_turso_exec", lambda sql, params=(): [(1,)] if sql == "SELECT 1" else None)
    assert remote.turso_health() == {"configured": True, "reachable": True}

    monkeypatch.setattr(remote, "_turso_exec", lambda sql, params=(): None)
    assert remote.turso_health() == {"configured": True, "reachable": False}
