from src.db import Database


def test_turso_health_checks_connection_and_remote_round_trip(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "local.db"))
    assert db.turso_health() == {
        "configured": False,
        "reachable": False,
        "readable": False,
        "writable": False,
    }

    remote = Database(str(tmp_path / "remote.db"))
    remote._turso_endpoint = "https://example.turso.io/v2/pipeline"
    remote._turso_token = "test-token"
    monkeypatch.setattr(remote, "_turso_exec", lambda sql, params=(): [(1,)] if sql == "SELECT 1" else None)

    def successful_round_trip(statements):
        assert statements[0][0].startswith("INSERT INTO app_state")
        assert statements[1][0].startswith("SELECT value FROM app_state")
        assert statements[2][0].startswith("DELETE FROM app_state")
        value = statements[0][1][1]
        return [[], [(value,)], []]

    monkeypatch.setattr(remote, "_turso_batch", successful_round_trip)
    assert remote.turso_health() == {
        "configured": True,
        "reachable": True,
        "readable": True,
        "writable": True,
    }

    monkeypatch.setattr(remote, "_turso_batch", lambda _statements: None)
    assert remote.turso_health() == {
        "configured": True,
        "reachable": True,
        "readable": False,
        "writable": False,
    }

    monkeypatch.setattr(remote, "_turso_exec", lambda _sql, params=(): None)
    assert remote.turso_health() == {
        "configured": True,
        "reachable": False,
        "readable": False,
        "writable": False,
    }
