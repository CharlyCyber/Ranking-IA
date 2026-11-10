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
    # El broadcast exige token; se fija uno falso para no depender del .env local.
    monkeypatch.setattr("src.updater.TELEGRAM_TOKEN", "test-token")

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


async def test_force_registra_cambios_en_historial_sin_perderlos(tmp_path, monkeypatch):
    """Aunque force=True no spamee broadcast, el cambio sí queda guardado en SQLite."""
    fetch_results = {"arena": {"status": "ok", "models": _models(swap_top=True)}}
    updater, bot = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)

    result = await updater.check_and_update(force=True)
    assert result["success"] is True
    assert bot.sent == []
    assert len(updater.db.get_changes(days=1)) == 1


async def test_broadcast_desactiva_suscriptor_que_bloqueo_el_bot(tmp_path, monkeypatch):
    class BlockingBot:
        def __init__(self):
            self.ok_sent = []

        async def send_message(self, chat_id, text, parse_mode=None):
            if chat_id == 777:
                raise RuntimeError("Forbidden: bot was blocked by the user")
            self.ok_sent.append(chat_id)

    fetch_results = {"arena": {"status": "ok", "models": _models(swap_top=True)}}
    updater, _ = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)
    updater.bot = BlockingBot()
    updater.db.add_subscriber("888")

    await updater.check_and_update(force=False)
    assert updater.db.is_subscriber("777") is False
    assert updater.db.is_subscriber("888") is True
    assert updater.bot.ok_sent == [888]


async def test_lock_impide_ejecuciones_concurrentes(tmp_path, monkeypatch):
    fetch_results = {"arena": {"status": "ok", "models": _models()}}
    updater, _ = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)

    async with updater._lock:
        res = await updater.check_and_update(force=True)
        assert res["success"] is False
        assert "en curso" in res["message"]


async def test_skip_telegram_broadcast_en_modo_respaldo(tmp_path, monkeypatch):
    """Cuando el bot 24/7 ya está vivo, el backup horario persiste datos sin duplicar mensajes."""
    monkeypatch.setattr("src.config.SKIP_TELEGRAM_BROADCAST", True)
    fetch_results = {"arena": {"status": "ok", "models": _models(swap_top=True)}}
    updater, bot = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)

    res = await updater.check_and_update(force=False)
    assert res["success"] is True
    assert res["changes_detected"] is False
    assert bot.sent == []
    assert len(updater.db.get_changes(days=1)) == 1


async def test_deduplicacion_evita_doble_alerta_del_mismo_cambio(tmp_path, monkeypatch):
    """Si otro proceso ya registró el mismo cambio hace pocos minutos, no se reenvía."""
    from src.detector import change_summary, detect_changes

    new_models = _models(swap_top=True)
    fetch_results = {"arena": {"status": "ok", "models": new_models}}
    updater, bot = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)

    expected_summary = change_summary(
        detect_changes(OLD_V3, {"generated_at": "2026-11-05T10:00:00", "sources": fetch_results})
    )
    updater.db.save_change(expected_summary)

    res = await updater.check_and_update(force=False)
    assert res["success"] is True
    assert res["changes_detected"] is False
    assert bot.sent == []


async def test_send_to_fallback_texto_plano_si_html_falla(tmp_path, monkeypatch):
    """Si Telegram rechaza HTML en una alerta, reintenta en texto plano sin perderla."""

    class HtmlRejectingBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, parse_mode=None):
            if parse_mode == "HTML":
                raise RuntimeError("Bad Request: can't parse entities")
            self.sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})

    fetch_results = {"arena": {"status": "ok", "models": _models(swap_top=True)}}
    updater, _ = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)
    updater.bot = HtmlRejectingBot()

    res = await updater.check_and_update(force=False)
    assert res["changes_detected"] is True
    assert len(updater.bot.sent) == 1
    assert updater.bot.sent[0]["parse_mode"] is None
    assert "ALERTA DE CAMBIO" in updater.bot.sent[0]["text"]


async def test_broadcast_fallback_a_admin_si_canal_publico_falla(tmp_path, monkeypatch):
    """Si PUBLIC_CHANNEL_ID falla y no hay suscriptores, notifica al ADMIN_CHAT_ID."""

    class SelectiveFailBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, parse_mode=None):
            if chat_id == "@canal_roto":
                raise RuntimeError("Forbidden: bot is not a member of the channel")
            self.sent.append({"chat_id": chat_id, "text": text})

    monkeypatch.setattr("src.updater.PUBLIC_CHANNEL_ID", "@canal_roto")
    monkeypatch.setattr("src.updater.ADMIN_CHAT_ID", "99999")
    fetch_results = {"arena": {"status": "ok", "models": _models(swap_top=True)}}
    updater, _ = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)
    updater.db.remove_subscriber("777")
    updater.bot = SelectiveFailBot()

    res = await updater.check_and_update(force=False)
    assert res["changes_detected"] is True
    assert any(m["chat_id"] == 99999 for m in updater.bot.sent)


def test_ensure_subscriber_respeta_baja_previa(tmp_path):
    db = Database(str(tmp_path / "ensure.db"))
    db.ensure_subscriber("555")
    assert db.is_subscriber("555") is True
    db.remove_subscriber("555")
    assert db.is_subscriber("555") is False
    # Volver a llamar ensure_subscriber no debe reactivar a quien pidió /unsubscribe
    db.ensure_subscriber("555")
    assert db.is_subscriber("555") is False


def test_turso_error_json_dispara_fallback_sqlite(tmp_path, monkeypatch):
    """Un error SQL dentro de HTTP 200 de Turso debe caer a SQLite local, no devolver []."""

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"type": "error", "error": {"message": "no such column: preferences"}}]}

    db = Database(str(tmp_path / "turso_err.db"))
    db.add_subscriber("12345")

    db._turso_endpoint = "https://example.turso.io/v2/pipeline"
    db._turso_token = "tok"
    monkeypatch.setattr("src.db.httpx.post", lambda *a, **k: FakeResp())

    subs = db.list_subscribers_with_prefs()
    assert len(subs) == 1
    assert subs[0]["chat_id"] == "12345"


async def test_deriva_gradual_multi_check_dispara_alerta_una_sola_vez(tmp_path, monkeypatch):
    """Subidas graduales de 1 puesto se acumulan contra notified_snapshot y alertan una sola vez."""

    class StepFetcher:
        def __init__(self):
            self.ranks = [8, 7, 6, 5, 5]
            self.idx = 0

        async def fetch_all(self):
            r = self.ranks[min(self.idx, len(self.ranks) - 1)]
            self.idx += 1
            models = _models(10)
            # Movemos model-7 gradualmente (sin alterar top-3 ni score)
            for m in models:
                if m["model"] == "model-7":
                    m["rank"] = r
            return {"arena": {"status": "ok", "models": models}}

    fetcher = StepFetcher()
    monkeypatch.setattr("src.updater.Database", lambda: Database(str(tmp_path / "drift.db")))
    monkeypatch.setattr("src.updater.DataFetcher", lambda: fetcher)
    monkeypatch.setattr("src.updater.TELEGRAM_TOKEN", "test-token")

    async def fake_trending():
        return _trending_ok()

    monkeypatch.setattr("src.updater.fetch_trending", fake_trending)

    bot = FakeBot()
    updater = DataUpdater(bot=bot, brain=FakeBrain())
    updater.output_path = str(tmp_path / "rankings.json")
    updater.db.add_subscriber("777")

    res0 = await updater.check_and_update()  # baseline (rank 8)
    assert res0["changes_detected"] is False
    res1 = await updater.check_and_update()  # rank 7
    assert res1["changes_detected"] is False
    res2 = await updater.check_and_update()  # rank 6
    assert res2["changes_detected"] is False
    res3 = await updater.check_and_update()  # rank 5 → acumula +3 desde baseline 8
    assert res3["changes_detected"] is True
    assert len(bot.sent) == 1
    assert "8 → 5" in bot.sent[0]["text"]
    res4 = await updater.check_and_update()  # se mantiene en 5 → sin spam
    assert res4["changes_detected"] is False
    assert len(bot.sent) == 1


async def test_broadcast_respeta_modelos_seguidos_y_modo_watched(tmp_path, monkeypatch):
    fetch_results = {"arena": {"status": "ok", "models": _models(swap_top=True)}}
    updater, bot = _make_updater(tmp_path, monkeypatch, fetch_results, old_snapshot=OLD_V3)
    updater.db.set_subscriber_prefs("777", {"alerts": "watched", "watch_models": ["model-1"]})
    updater.db.set_subscriber_prefs("888", {"alerts": "watched", "watch_models": ["llama"]})

    res = await updater.check_and_update()
    assert res["changes_detected"] is True
    assert [m["chat_id"] for m in bot.sent] == [777]



