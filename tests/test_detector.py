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

    def test_entrada_al_podio_top3_notifica(self):
        def climb(sources):
            ms = sources["arena"]["models"]
            ms[2]["rank"], ms[3]["rank"] = 4, 3

        events = detect_changes(BASE, _mutate(climb))
        assert any(e["kind"] == "podium" and "model-3" in e["text"] and "4 → 3" in e["text"] for e in events)

    def test_entrada_al_top10_desde_11_notifica(self):
        old = _mutate(lambda s: s["arena"]["models"].append(_model(11, "model-10")))
        new = _mutate(lambda s: s["arena"]["models"].append(_model(10, "model-10")))
        events = detect_changes(old, new)
        assert any(e["kind"] == "new" and "model-10" in e["text"] and "11 → 10" in e["text"] for e in events)

    def test_recuperacion_desde_stale_compara_con_ultimo_valido(self):
        old = _mutate(lambda s: s["arena"].__setitem__("status", "stale"))
        new = _mutate(lambda s: s["arena"]["models"].__setitem__(0, _model(2, "model-0")) or s["arena"]["models"].__setitem__(1, _model(1, "model-1")))
        events = detect_changes(old, new)
        assert any(e["kind"] == "top1" and "model-1" in e["text"] for e in events)

    def test_huggingface_y_openrouter_gratis_notifican(self):
        old = _mutate(
            lambda s: s.update(
                {
                    "huggingface": {"status": "ok", "items": [{"id": "a/old", "likes": 200}]},
                    "openrouter": {
                        "status": "ok",
                        "models": [_model(1, "m1"), _model(2, "m2"), _model(3, "m3")],
                        "free_models": [{"id": "x/y:free", "name": "Old Free", "context_length": 131072}],
                    },
                }
            )
        )
        new = _mutate(
            lambda s: s.update(
                {
                    "huggingface": {"status": "ok", "items": [{"id": "qwen/new-model", "likes": 450}]},
                    "openrouter": {
                        "status": "ok",
                        "models": [_model(1, "m1"), _model(2, "m2"), _model(3, "m3")],
                        "free_models": [
                            {"id": "new/model:free", "name": "New Free 128K", "context_length": 131072},
                            {"id": "x/y:free", "name": "Old Free", "context_length": 131072},
                        ],
                    },
                }
            )
        )
        events = detect_changes(old, new)
        assert any("qwen/new-model" in e["text"] for e in events)
        assert any("New Free 128K" in e["text"] for e in events)


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


class TestCanonicalYDeriva:
    def test_canonical_model_key_ignora_cambio_de_metodo_sin_perder_variantes(self):
        from src.detector import canonical_model_key, filter_events_for_preference

        assert canonical_model_key("claude-opus-4-6") == canonical_model_key("Claude Opus 4.6")
        assert canonical_model_key("anthropic/claude-opus-4-6") == canonical_model_key("Claude Opus 4.6")
        assert canonical_model_key("Claude Opus 5 (High)") != canonical_model_key("Claude Opus 5 (Max)")

        old = {
            "generated_at": "2026-11-01T10:00:00",
            "sources": {
                "arena": _source(
                    [
                        _model(1, "claude-opus-4-6", score=1510),
                        _model(2, "Claude Opus 5 (High)", score=1500),
                        _model(3, "Claude Opus 5 (Max)", score=1490),
                    ]
                )
            },
        }
        new = {
            "generated_at": "2026-11-01T11:00:00",
            "sources": {
                "arena": _source(
                    [
                        _model(1, "Claude Opus 4.6", score=1510),
                        _model(2, "Claude Opus 5 (High)", score=1500),
                        _model(3, "Claude Opus 5 (Max)", score=1490),
                    ]
                )
            },
        }
        # El cambio de formato (HF dataset -> página/mirror) no debe disparar falsas alertas
        assert detect_changes(old, new) == []

        # Cambio real entre variantes (Max) y (High) sí dispara alerta
        new["sources"]["arena"]["models"][1]["rank"] = 3
        new["sources"]["arena"]["models"][2]["rank"] = 2
        ev = detect_changes(old, new)
        assert any(e["kind"] == "podium" and "Max" in e["text"] for e in ev)

        # Watchlist: modo watched y combinación con top1
        events = [
            {"kind": "top1", "source": "arena", "category": "Text", "models": ["GPT-6"], "text": "top1"},
            {"kind": "move", "source": "arena", "category": "Text", "models": ["DeepSeek V4"], "text": "ds"},
        ]
        assert len(filter_events_for_preference(events, "watched", watch_models=["deepseek"])) == 1
        assert len(filter_events_for_preference(events, "top1", watch_models=["deepseek"])) == 2

    def test_deriva_gradual_acumulada_contra_baseline(self):
        baseline = copy.deepcopy(BASE)
        step1 = _mutate(lambda s: s["arena"]["models"][7].__setitem__("rank", 7))
        step2 = _mutate(lambda s: s["arena"]["models"][7].__setitem__("rank", 6))
        step3 = _mutate(lambda s: s["arena"]["models"][7].__setitem__("rank", 5))

        assert [e for e in detect_changes(baseline, step1, baseline_snapshot=baseline) if e["kind"] == "move"] == []
        assert [e for e in detect_changes(step1, step2, baseline_snapshot=baseline) if e["kind"] == "move"] == []
        ev = detect_changes(step2, step3, baseline_snapshot=baseline)
        moves = [e for e in ev if e["kind"] == "move"]
        assert len(moves) == 1
        assert "model-7" in moves[0]["text"]
        assert "8 → 5" in moves[0]["text"]

