"""Updater v3: safety net, detección, broadcast y manejo de errores (con fakes)."""

import json

import pytest

from src.db import Database
from src.updater import DataUpdater


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})


class FakeBrain:
    """Sin Gemini: draft_newsletter devuelve None → se usa la plantilla."""

    def draft_newsletter(self, template, summary):
        return None

    def generate_response(self, query, context):
        return "respuesta fake"


class FakeFetcher:
    def __init__(self, results):
        self.results = results

    async def fetch_all(self):
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in self.results.items()}


def _models(n=10, swap_top=False):
    models = [{"rank": i + 1, "model": f"model-{i}", "score": 1400 - i, "votes": 1000} for i in range(n)]
    if swap_top:
        models[0]["rank"], models[1]["rank"] = 2, 1
    return models


def _trending_ok():
    return {
        "github": {"status": "ok", "ai": [], "general": [], "items": [{"full_name": "a/b", "stars": 100}]},
        "hackernews": {"status": "ok", "items": [{"title": "t", "points": 100, "comments": 1, "url": "https://x"}]},
    }


def _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=None, brain=None):
    monkeypatch.setattr("src.updater.Database", lambda: Database(str(tmp_path / "test.db")))
    monkeypatch.setattr("src.updater.DataFetcher", lambda: FakeFetcher(fetch_results))

    async def fake_trending():
        return _trending_ok()

    monkeypatch.setattr("src.updater.fetch_trending", fake_trending)

    bot = FakeBot()
    updater = DataUpdater(bot=bot, brain=brain or FakeBrain())
    updater.output_path = str(tmp_path / "rankings.json")
    updater.db.add_subscriber("777")

    if old_snapshot is not None:
        with open(updater.output_path, "w", encoding="utf-8") as f:
            json.dump(old_snapshot, f, ensure_ascii=False)

    return updater, bot


OLD_V3 = {
    "generated_at": "2026-10-21T08:00:00",
    "sources": {"arena": {"status": "ok", "models": _models()}},
}


async def test_safety_net_conserva_datos_previos_si_scrape_falla(tmp_path, monkeypatch):
    """Un fetch fallido NO sobrescribe los datos buenos: se conservan como stale."""
    fetch_results = {
        "arena": {"status": "error", "error": "No se pudo obtener datos de Arena."},
        "openrouter": {"status": "ok", "models": _models()},
    }
    updater, bot = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)

    result = await updater.check_and_update()

    assert result["success"] is True
    assert "stale" in result["message"]

    with open(updater.output_path, encoding="utf-8") as f:
        saved = json.load(f)

    arena = saved["sources"]["arena"]
    assert arena["status"] == "stale"
    assert arena["models"] == _models(), "los modelos previos deben sobrevivir intactos"
    assert "No se pudo obtener datos" not in json.dumps(arena["models"])
    assert bot.sent == [], "un fetch fallido solo alerta al admin, no emite boletín de cambios"


async def test_safety_net_fuente_nueva_falla_sin_previos(tmp_path, monkeypatch):
    """Fuente que falla y no tenía datos previos queda como error, sin romper el resto."""
    fetch_results = {"arena": {"status": "error", "error": "boom"}, "openrouter": {"status": "ok", "models": _models()}}
    updater, _ = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=None)

    result = await updater.check_and_update()

    assert result["success"] is True
    with open(updater.output_path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["sources"]["arena"]["status"] == "error"
    assert saved["sources"]["openrouter"]["status"] == "ok"


async def test_cambio_detectado_genera_broadcast(tmp_path, monkeypatch):
    """Cambio de top-1 → boletín determinista enviado a todos los suscriptores."""
    fetch_results = {"arena": {"status": "ok", "models": _models(swap_top=True)}}
    updater, bot = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)

    result = await updater.check_and_update()

    assert result["changes_detected"] is True
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 777  # _send_to convierte a int para la API de Telegram
    assert bot.sent[0]["parse_mode"] == "HTML"
    assert "ALERTA DE CAMBIO" in bot.sent[0]["text"]
    assert updater.db.get_changes(days=1), "el cambio debe registrarse en SQLite"


async def test_snapshot_legacy_es_linea_base_sin_alertas(tmp_path, monkeypatch):
    """El primer snapshot estructurado a partir de texto crudo NO dispara alertas."""
    legacy = {"arena": "texto crudo de la web con Rank etc", "openrouter": "otro texto"}
    fetch_results = {"arena": {"status": "ok", "models": _models()}}
    updater, bot = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=legacy)

    result = await updater.check_and_update()

    assert result["success"] is True
    assert result["changes_detected"] is False
    assert bot.sent == []
    assert "línea base" in result["message"].lower()


async def test_sin_cambios_no_notifica(tmp_path, monkeypatch):
    fetch_results = {"arena": {"status": "ok", "models": _models()}}
    updater, bot = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)

    result = await updater.check_and_update()

    assert result["success"] is True
    assert result["changes_detected"] is False
    assert bot.sent == []


async def test_excepcion_en_fetch_reporta_error(tmp_path, monkeypatch):
    class ExplodingFetcher:
        async def fetch_all(self):
            raise RuntimeError("Playwright explotó")

    monkeypatch.setattr("src.updater.Database", lambda: Database(str(tmp_path / "test.db")))
    monkeypatch.setattr("src.updater.DataFetcher", ExplodingFetcher)

    async def fake_trending():
        return {}

    monkeypatch.setattr("src.updater.fetch_trending", fake_trending)

    updater = DataUpdater(bot=FakeBot(), brain=FakeBrain())
    updater.output_path = str(tmp_path / "rankings.json")

    result = await updater.check_and_update()

    assert result["success"] is False
    assert "Playwright explotó" in result["message"]


async def test_historial_de_estrellas_se_registra(tmp_path, monkeypatch):
    fetch_results = {"arena": {"status": "ok", "models": _models()}}
    updater, _ = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=None)

    await updater.check_and_update()

    stars = updater.db.get_latest_repo_stars()
    assert stars == {"a/b": 100}


async def test_get_status_incluye_suscriptores(tmp_path, monkeypatch):
    fetch_results = {"arena": {"status": "ok", "models": _models()}}
    updater, _ = _make_updater(tmp_path, monkeypatch, fetch_results)
    status = updater.get_status()
    assert status["subscribers"] == 1
    assert status["output_path"] == updater.output_path


@pytest.mark.parametrize("force", [True, False])
async def test_update_data_compatibilidad(tmp_path, monkeypatch, force):
    """La entrada usada por GitHub Actions (update_data) sigue existiendo y funciona."""
    from src import updater as updater_mod

    fetch_results = {"arena": {"status": "ok", "models": _models()}}
    u, _ = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=None)

    monkeypatch.setattr(updater_mod, "DataUpdater", lambda bot=None: u)
    result = await updater_mod.update_data(bot=None, force=force)
    assert result["success"] is True
    if force:
        # force no emite boletín de cambios, solo actualiza
        assert result["changes_detected"] is False
