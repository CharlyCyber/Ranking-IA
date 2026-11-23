"""Respaldo determinista cuando los proveedores LLM están ocupados o sin cuota."""

from src import brain as brain_mod
from src.insights import render_offline_answer


def _snapshot():
    return {
        "generated_at": "2026-11-23T00:00:00",
        "sources": {
            "arena": {
                "status": "ok",
                "models": [
                    {"model": "TestModel General", "rank": 1, "score": 1500, "category": "Text"},
                    {"model": "TestModel Code", "rank": 1, "score": 1480, "category": "Code"},
                ],
            },
            "openrouter": {
                "status": "ok",
                "models": [
                    {"model": "TestModel OR", "rank": 1, "tokens_week": 123456, "growth_pct": 5, "author": "lab"},
                ],
            },
        },
    }


class TestRenderOfflineAnswer:
    def test_enruta_a_coding(self):
        out = render_offline_answer(_snapshot(), "¿qué modelo uso para coding?")
        assert "Programación" in out
        assert "TestModel Code" in out

    def test_por_defecto_general(self):
        out = render_offline_answer(_snapshot(), "hola, ¿qué tal?")
        assert "General" in out
        assert "TestModel General" in out

    def test_enruta_a_gratis(self):
        out = render_offline_answer(_snapshot(), "¿hay modelos gratis?")
        assert "Gratis" in out

    def test_sin_snapshot_devuelve_guia(self):
        out = render_offline_answer(None, "precio de los modelos")
        assert "/menu" in out
        assert "snapshot" in out


class TestGenerateResponseFallback:
    def _brain_sin_proveedores(self, monkeypatch):
        monkeypatch.setattr(brain_mod, "GEMINI_API_KEY", None)
        monkeypatch.setattr(brain_mod, "GROQ_API_KEY", None)
        monkeypatch.setattr(brain_mod, "OPENROUTER_API_KEY", None)
        return brain_mod.RankingBrain()

    def test_sin_proveedores_devuelve_datos_no_error_mudo(self, monkeypatch):
        b = self._brain_sin_proveedores(monkeypatch)
        out = b.generate_response("¿cuál es el top 1?", "CTX", snapshot=_snapshot())
        assert "TestModel General" in out
        assert "ocupados" in out  # explica por qué no hay redacción IA

    def test_cuota_agotada_devuelve_datos(self, monkeypatch):
        class FakeBudget:
            def take(self):
                return False

            @property
            def remaining(self):
                return 0

        monkeypatch.setattr(brain_mod, "_budget", FakeBudget())
        b = self._brain_sin_proveedores(monkeypatch)
        b.openrouter_key = "clave-falsa"  # supera has_any_provider para llegar al check de cuota
        out = b.generate_response("¿cuál es el top 1?", "CTX", snapshot=_snapshot())
        assert "TestModel General" in out
