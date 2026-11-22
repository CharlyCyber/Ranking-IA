from datetime import datetime, timedelta, timezone

from src.brain import RankingBrain, _context_freshness_footer


def test_freshness_footer_uses_context_timestamp_and_detects_stale_source():
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
    footer = _context_freshness_footer(
        f"DATOS ACTUALIZADOS (UTC): {old}\n[ARENA (STALE - último dato válido)]"
    )
    assert "Arena" in footer
    assert "más de 5 min" in footer
    assert "fuentes sin actualización válida" in footer


def test_generate_response_uses_history_and_adds_deterministic_provenance(monkeypatch):
    brain = RankingBrain()
    calls = {}

    def fake_generate(prompt, max_tokens=None, system_instruction=None):
        calls["prompt"] = prompt
        calls["system"] = system_instruction
        return "<b>Claude lidera.</b>"

    monkeypatch.setattr(brain, "_generate_or_raise", fake_generate)
    context = "DATOS ACTUALIZADOS (UTC): 2026-11-22 12:00\n[ARENA]\nClaude #1"
    result = brain.generate_response(
        "¿Y para código?", context, [{"role": "user", "content": "¿Quién lidera?"}]
    )

    assert "¿Quién lidera?" in calls["prompt"]
    assert "¿Y para código?" in calls["prompt"]
    assert "Claude lidera" in result
    assert "Datos del snapshot: 2026-11-22 12:00 UTC" in result
    assert "Arena" in result


def test_live_citations_only_link_search_result_ids(monkeypatch):
    brain = RankingBrain()
    monkeypatch.setattr(brain, "_generate_or_raise", lambda *a, **k: "Hecho reciente [S1]. Fuente inventada [S9].")
    sources = [
        {
            "id": "S1",
            "title": "Fuente primaria",
            "domain": "example.org",
            "url": "https://example.org/news",
            "excerpt": "actualización",
        }
    ]
    result = brain.generate_response(
        "¿Qué pasó hoy?", "DATOS ACTUALIZADOS (UTC): 2026-11-22 12:00", live_sources=sources, live_search_status="ok"
    )
    assert '<a href="https://example.org/news">[S1]</a>' in result
    assert "[referencia no disponible]" in result
    assert "Fuente primaria" in result
    assert "Búsqueda web consultada" in result
