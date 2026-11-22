from src.db import Database


def test_usage_limit_is_persistent_and_rolls_over(tmp_path):
    path = str(tmp_path / "limits.db")
    db1 = Database(path)
    assert db1.consume_limit("user:42", limit=2, window_seconds=3600, now=10_000)
    assert db1.consume_limit("user:42", limit=2, window_seconds=3600, now=10_001)
    assert not db1.consume_limit("user:42", limit=2, window_seconds=3600, now=10_002)

    # Un nuevo objeto simula un reinicio del proceso: el contador sigue en la BD.
    db2 = Database(path)
    assert db2.limit_used("user:42", window_seconds=3600, now=10_003) == 3
    assert db2.consume_limit("user:42", limit=2, window_seconds=3600, now=13_601)
    assert db2.limit_used("user:42", window_seconds=3600, now=13_601) == 1


def test_conversation_history_is_ordered_bounded_and_clearable(tmp_path):
    db = Database(str(tmp_path / "history.db"))
    for i in range(25):
        db.add_chat_message("chat-1", "user" if i % 2 == 0 else "assistant", f"message-{i}")

    history = db.get_chat_history("chat-1", limit=30)
    assert len(history) == 20
    assert history[0]["content"] == "message-5"
    assert history[-1]["content"] == "message-24"

    db.clear_chat_history("chat-1")
    assert db.get_chat_history("chat-1") == []


def test_chat_message_is_truncated_to_safe_storage_size(tmp_path):
    db = Database(str(tmp_path / "truncate.db"))
    db.add_chat_message(5, "user", "x" * 5000)
    assert len(db.get_chat_history(5)[0]["content"]) == 4000
