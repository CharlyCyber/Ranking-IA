"""Diff determinista entre snapshots (sin LLM)."""

import copy

from src.detector import MAX_EVENTS, change_summary, detect_changes, render_alert


def _source(models):
    return {"status": "ok", "models": models}


def _model(rank, model, score=1400, **extra):
    return {"rank": rank, "model": model, "score": score, "votes": 1000, **extra}


BASE = {
    "generated_at": "2026-10-21T10:00:00",
    "sources": {
        "arena": _source([_model(i + 1, f"model-{i}") for i in range(10)]),
        "design": _source([_model(i + 1, f"design-{i}") for i in range(8)]),
        "github": {"status": "ok", "items": [{"full_name": "a/b", "stars": 1000}]},
    },
}


def _mutate(fn):
    snap = copy.deepcopy(BASE)
    fn(snap["sources"])
    return snap


class TestSinCambios:
    def test_snapshot_identico(self):
        assert detect_changes(BASE, copy.deepcopy(BASE)) == []

    def test_legacy_no_genera_eventos(self):
        assert detect_changes({"arena": "texto crudo"}, BASE) == []
        assert detect_changes(None, BASE) == []


class TestCambios:
    def test_cambio_top1(self):
        def swap(sources):
            ms = sources["arena"]["models"]
            ms[0]["rank"], ms[1]["rank"] = 2, 1

        events = detect_changes(BASE, _mutate(swap))
        top1 = [e for e in events if e["kind"] == "top1"]
        assert len(top1) == 1
        assert "model-1" in top1[0]["text"] and "model-0" in top1[0]["text"]
        assert "🥇" in top1[0]["text"]

    def test_model_nuevo_en_top10(self):
        def add(sources):
            sources["design"]["models"].insert(2, _model(3, "brand-new"))

        events = detect_changes(BASE, _mutate(add))
        news = [e for e in events if e["kind"] == "new"]
        assert any("brand-new" in e["text"] and "puesto 3" in e["text"] for e in news)

    def test_movimiento_grande(self):
        def move(sources):
            ms = sources["arena"]["models"]
            ms[9]["rank"] = 3  # model-9 sube de10 a3

        events = detect_changes(BASE, _mutate(move))
        moves = [e for e in events if e["kind"] == "move"]
        assert any("model-9" in e["text"] and "📈" in e["text"] for e in moves)

    def test_movimiento_pequeno_no_alerta(self):
        def tiny(sources):
            sources["arena"]["models"][4]["rank"], sources["arena"]["models"][5]["rank"] = 6, 5

        events = detect_changes(BASE, _mutate(tiny))
        assert [e for e in events if e["kind"] == "move"] == []

    def test_salto_de_score(self):
        def jump(sources):
            sources["design"]["models"][1]["score"] += 50

        events = detect_changes(BASE, _mutate(jump))
        assert any(e["kind"] == "score" for e in events)

    def test_repo_nuevo_en_tendencias(self):
        def add_repo(sources):
            sources["github"]["items"].append({"full_name": "ai/new", "stars": 900})

        events = detect_changes(BASE, _mutate(add_repo))
        assert any(e["kind"] == "new" and "ai/new" in e["text"] for e in events)

    def test_repo_pequeno_no_alerta(self):
        def add_small(sources):
            sources["github"]["items"].append({"full_name": "small/repo", "stars": 50})

        assert detect_changes(BASE, _mutate(add_small)) == []

    def test_aceleracion_de_estrellas(self):
        def accel(sources):
            sources["github"]["items"][0]["stars"] += 400

        events = detect_changes(BASE, _mutate(accel))
        assert any(e["kind"] == "accel" and "+400" in e["text"] for e in events)

    def test_fuente_stale_no_compara(self):
        def stale(sources):
            sources["arena"]["status"] = "stale"

        assert detect_changes(BASE, _mutate(stale)) == []


class TestRender:
    def test_alerta_html_y_limitada(self):
        def flood(sources):
            # 30 modelos nuevos todos dentro del top-10 → más eventos que MAX_EVENTS
            for i in range(30):
                sources["arena"]["models"].append(_model(10, f"flood-{i}"))

        events = detect_changes(BASE, _mutate(flood))
        assert len(events) <= MAX_EVENTS
        alert = render_alert(events)
        assert "ALERTA DE CAMBIO" in alert
        assert "<b>" in alert  # HTML de Telegram, sin Markdown

    def test_summary_una_linea(self):
        def swap(sources):
            sources["arena"]["models"][0]["rank"], sources["arena"]["models"][1]["rank"] = 2, 1

        summary = change_summary(detect_changes(BASE, _mutate(swap)))
        assert "\n" not in summary
        assert len(summary) < 800
