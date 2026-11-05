"""Analítica determinista (0 tokens), preferencias de suscriptor y Modo Inline."""

from types import SimpleNamespace

import src.main as main_mod
from src.db import Database
from src.detector import filter_events_for_preference
from src.insights import (
    build_quickchart_url,
    compute_value_leaders,
    render_category_card,
    render_elo_svg,
    search_models,
)


def _sample_snapshot():
    return {
        "generated_at": "2026-11-05T12:00:00",
        "sources": {
            "arena": {
                "status": "ok",
                "models": [
                    {
                        "rank": 1,
                        "model": "claude-opus-4-6",
                        "score": 1510,
                        "category": "Text",
                        "prompt_per_m": 5.0,
                        "completion_per_m": 25.0,
                    },
                    {
                        "rank": 2,
                        "model": "deepseek-v4",
                        "score": 1495,
                        "category": "Text",
                        "prompt_per_m": 0.25,
                        "completion_per_m": 1.0,
                    },
                    {
                        "rank": 1,
                        "model": "gpt-6-astra",
                        "score": 1800,
                        "category": "WebDev",
                    },
                ],
            },
            "openrouter": {
                "status": "ok",
                "models": [
                    {"rank": 1, "model": "Nemotron 3 Ultra (free)", "author": "nvidia", "tokens_week": 3_600_000_000, "growth_pct": 40}
                ],
                "free_models": [
                    {"id": "qwen/qwen3:free", "name": "Qwen 3 (free)", "context_length": 131072, "is_free": True}
                ],
            },
            "design": {
                "status": "ok",
                "models": [{"rank": 1, "model": "Kimi K3", "score": 1387}],
            },
        },
    }


def test_search_models_y_value_leaders():
    snap = _sample_snapshot()
    hits = search_models(snap, "deepseek")
    assert len(hits) == 1
    assert hits[0]["best_rank"] == 2

    leaders = compute_value_leaders(snap)
    assert len(leaders["free_models"]) >= 2
    # DeepSeek tiene casi el mismo ELO que Opus a una fracción del precio → lidera Calidad/Precio
    assert leaders["value_models"][0]["model"] == "deepseek-v4"


def test_tarjetas_cero_tokens_y_graficas():
    snap = _sample_snapshot()
    for view in ("general", "coding", "openrouter", "free"):
        html = render_category_card(snap, view)
        assert "<b>" in html

    svg = render_elo_svg({"claude-opus-4-6": [["2026-11-01", 1490], ["2026-11-05", 1510]]})
    assert "<svg" in svg and "<polyline" in svg

    qc = build_quickchart_url(snap["sources"]["arena"]["models"])
    assert qc and qc.startswith("https://quickchart.io/chart")


def test_preferencias_suscriptor_y_filtrado_alertas(tmp_path):
    db = Database(str(tmp_path / "prefs.db"))
    db.add_subscriber("1001")
    assert db.get_subscriber_prefs("1001")["alerts"] == "all"

    db.set_subscriber_prefs("1001", {"alerts": "coding", "daily_brief": False})
    # Re-suscribir no debe borrar las preferencias elegidas
    db.add_subscriber("1001")
    prefs = db.get_subscriber_prefs("1001")
    assert prefs == {"alerts": "coding", "daily_brief": False}

    events = [
        {"kind": "top1", "source": "arena", "category": "Text", "text": "a"},
        {"kind": "move", "source": "arena", "category": "WebDev", "text": "b"},
    ]
    assert len(filter_events_for_preference(events, "top1")) == 1
    assert len(filter_events_for_preference(events, "coding")) == 1
    assert filter_events_for_preference(events, "none") == []


async def test_inline_query_modo_viral(monkeypatch):
    async def fake_load():
        return _sample_snapshot()

    monkeypatch.setattr(main_mod, "load_snapshot", fake_load)
    answered = []

    async def fake_answer(results, cache_time=0, is_personal=False):
        answered.append(results)

    update_empty = SimpleNamespace(inline_query=SimpleNamespace(query="", answer=fake_answer))
    await main_mod.handle_inline_query(update_empty, SimpleNamespace())
    assert len(answered[0]) == 4  # 4 tarjetas preset cuando la búsqueda está vacía

    update_q = SimpleNamespace(inline_query=SimpleNamespace(query="claude", answer=fake_answer))
    await main_mod.handle_inline_query(update_q, SimpleNamespace())
    assert len(answered[1]) == 1
    assert "claude-opus-4-6" in answered[1][0].title
