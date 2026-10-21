"""Safety net: las fuentes inválidas deben ser detectadas ANTES de persistir."""

from src.validator import (
    is_legacy_snapshot,
    validate_models,
    validate_snapshot,
    validate_source,
)


def _good_arena(n=10):
    return [{"rank": i + 1, "model": f"model-{i}", "score": 1400 - i, "votes": 1000} for i in range(n)]


class TestValidateModels:
    def test_fuente_sana(self):
        assert validate_models("arena", _good_arena()) == []

    def test_demasiados_pocos_modelos(self):
        errors = validate_models("arena", _good_arena(3))
        assert any("mínimo 10" in e for e in errors)

    def test_no_lista(self):
        errors = validate_models("arena", "texto crudo de web")
        assert errors

    def test_falta_clave_model(self):
        bad = [{"rank": 1, "score": 1400}] * 10
        errors = validate_models("arena", bad)
        assert any("'model'" in e for e in errors)

    def test_score_no_numerico(self):
        bad = _good_arena()
        bad[0]["score"] = "mucha basura"
        errors = validate_models("arena", bad)
        assert any("score inválido" in e for e in errors)

    def test_rank_invalido(self):
        bad = _good_arena()
        bad[0]["rank"] = 0
        errors = validate_models("arena", bad)
        assert any("rank inválido" in e for e in errors)


class TestValidateSource:
    def test_status_error(self):
        errors = validate_source("arena", {"status": "error", "error": "timeout"})
        assert any("status=error" in e for e in errors)

    def test_fuentes_trending_por_items(self):
        ok = {"status": "ok", "items": [{"full_name": "a/b", "stars": 1}]}
        assert validate_source("github", ok) == []
        assert validate_source("hackernews", {"status": "error", "error": "403"})
        assert validate_source("hackernews", {"status": "ok", "items": []})  # sin errores: vacío no rompe


class TestValidateSnapshot:
    def test_snapshot_sano(self, v3_snapshot):
        assert validate_snapshot(v3_snapshot) == {}

    def test_snapshot_con_fuente_stale(self, v3_snapshot):
        v3_snapshot["sources"]["arena"] = {"status": "stale", "error": "x", "models": []}
        problems = validate_snapshot(v3_snapshot)
        assert "arena" in problems

    def test_snapshot_legacy(self):
        assert validate_snapshot({"arena": "texto crudo"})["sources"]


class TestLegacy:
    def test_legacy_texto_crudo(self):
        assert is_legacy_snapshot({"arena": "raw text"})
        assert is_legacy_snapshot({})
        assert is_legacy_snapshot(None)

    def test_v3_no_es_legacy(self, v3_snapshot):
        assert not is_legacy_snapshot(v3_snapshot)
