"""Parsers: deterministas y completos contra capturas reales (formatos clásicos y 2026+)."""

from pathlib import Path

import pytest

from src.parsers import (
    ParseError,
    enrich_models_with_catalog,
    parse_arena,
    parse_design_arena,
    parse_github_search,
    parse_hf_trending,
    parse_hn_stories,
    parse_openrouter_api,
    parse_openrouter_catalog,
    parse_openrouter_page,
)


class TestArena:
    def test_80_filas_8_categorias(self, raw_sources):
        models = parse_arena(raw_sources["arena"])
        assert len(models) == 80
        cats = {m["category"] for m in models}
        assert cats == {
            "Text",
            "Code",
            "Vision",
            "Text-to-Image",
            "Image Edit",
            "Search",
            "Text-to-Video",
            "Image-to-Video",
        }

    def test_formato_tarjetas_2026_12_categorias(self):
        raw_2026 = (Path(__file__).parent / "fixtures" / "arena_2026.txt").read_text(encoding="utf-8")
        models = parse_arena(raw_2026)
        assert len(models) == 120
        cats = {m["category"] for m in models}
        assert {"Agent", "Text", "WebDev", "Vision", "Document", "Search", "Video Edit"}.issubset(cats)
        top_text = next(m for m in models if m["category"] == "Text" and m["rank"] == 1)
        assert top_text["score"] >= 1400
        top_agent = next(m for m in models if m["category"] == "Agent" and m["rank"] == 1)
        assert isinstance(top_agent["win_rate_pct"], float)

    def test_top1_text(self, raw_sources):
        top = next(m for m in parse_arena(raw_sources["arena"]) if m["category"] == "Text" and m["rank"] == 1)
        assert top["model"] == "claude-opus-4-6"
        assert top["score"] == 1496
        assert top["votes"] == 2829

    def test_ranks_correlativos_por_categoria(self, raw_sources):
        by_cat: dict[str, list[int]] = {}
        for m in parse_arena(raw_sources["arena"]):
            by_cat.setdefault(m["category"], []).append(m["rank"])
        for ranks in by_cat.values():
            assert ranks == sorted(ranks)
            assert len(ranks) == 10

    def test_fila_con_scores_y_votes_separados_por_salto_linea(self, raw_sources):
        text = next(m for m in parse_arena(raw_sources["arena"]) if m["category"] == "Text" and m["rank"] == 10)
        assert text["model"] == "ernie-5.0-0110"
        assert text["votes"] == 10184

    def test_modelo_con_espacios_y_parentesis(self, raw_sources):
        models = [m for m in parse_arena(raw_sources["arena"]) if m["category"] == "Text"]
        assert any(m["model"] == "gemini-3-flash (thinking-minimal)" for m in models)

    def test_texto_vacio_y_basura(self):
        with pytest.raises(ParseError):
            parse_arena("")
        with pytest.raises(ParseError):
            parse_arena("página caída o menú de navegación sin tabla")


class TestOpenRouter:
    def test_top10_semanal(self, raw_sources):
        models = parse_openrouter_page(raw_sources["openrouter"])
        assert len(models) == 10
        assert models[0]["model"] == "Kimi K2.5"
        assert models[0]["author"] == "moonshotai"
        assert models[0]["tokens_week"] == 1_160_000_000_000  # 1.16T
        assert models[0]["growth_pct"] == 222

    def test_crecimiento_extremo_mayor_999_y_negativo(self):
        sample = (
            "1.\nGPT-5.6 Luna\nby\nopenai\n15.8T tokens\n12%\n"
            "2.\nDeepSeek V4.1 Flash\nby\ndeepseek\n11.8T tokens\n>999%\n"
            "3.\nLlama Next\nby\nmeta\n4.2T tokens\n-15%\n"
        )
        models = parse_openrouter_page(sample)
        assert [m["rank"] for m in models] == [1, 2, 3]
        assert models[1]["growth_pct"] == 999
        assert models[2]["growth_pct"] == -15

    def test_ranks_unicos(self, raw_sources):
        ranks = [m["rank"] for m in parse_openrouter_page(raw_sources["openrouter"])]
        assert ranks == sorted(set(ranks))

    def test_parse_api_variantes_de_esquema(self):
        rows = {
            "data": [
                {"model": "a/b", "tokens": 5_000_000, "position": 1, "growth": 10},
                {"model": "c/d", "tokens": 4_000_000, "position": 2, "growth": 5},
                {"model": "e/f", "tokens": 3_000_000, "position": 3, "growth": 2},
            ]
        }
        models = parse_openrouter_api(rows)
        assert models[0]["rank"] == 1 and models[0]["tokens_week"] == 5_000_000
        lista = [{"name": n, "token_count": "1234"} for n in ("x", "y", "z")]
        assert parse_openrouter_api(lista)[0]["tokens_week"] == 1234
        with pytest.raises(ParseError):
            parse_openrouter_api({"foo": 1})
        with pytest.raises(ParseError):
            parse_openrouter_api({"data": [{"model": "solo-uno"}]})

    def test_catalogo_precios_y_enriquecimiento(self):
        payload = {
            "data": [
                {
                    "id": "anthropic/claude-opus-4-6",
                    "name": "Claude Opus 4.6",
                    "pricing": {"prompt": "0.000005", "completion": "0.000025"},
                    "context_length": 200000,
                    "created": 1750000000,
                },
                {
                    "id": "nvidia/nemotron-ultra:free",
                    "name": "Nemotron Ultra (free)",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "context_length": 131072,
                    "created": 1750000100,
                },
            ]
        }
        cat = parse_openrouter_catalog(payload)
        assert len(cat["free_models"]) == 1
        assert cat["free_models"][0]["is_free"] is True

        models = [{"rank": 1, "model": "claude-opus-4-6", "score": 1496}]
        enrich_models_with_catalog(models, cat)
        assert models[0]["prompt_per_m"] == 5.0
        assert models[0]["completion_per_m"] == 25.0
        assert models[0]["context_length"] == 200000


class TestDesignArena:
    def test_tabla_completa_con_winrate(self, raw_sources):
        models = parse_design_arena(raw_sources["design"])
        assert len(models) >= 10
        assert models[0]["model"] == "Claude Opus 4.6"
        assert models[0]["score"] == 1397
        assert models[0]["win_rate_pct"] == 69.2

    def test_scores_repetidos_no_dedupean(self, raw_sources):
        models = parse_design_arena(raw_sources["design"])
        scores = [m["score"] for m in models]
        assert len(scores) > len(set(scores))

    def test_fallback_tarjeta_top20_corta_cola_faq(self):
        card = (
            "Top 20\n1397\n\nClaude\nOpus 4.6\n\n1375\n\nClaude\nOpus 4.6 (Thinking)\n\n"
            "1305\n\nGemini\n3.6 Flash\n\n#166\n805\n\nLlama\n4 Scout\n\nFAQ\nHow do I evaluate?"
        )
        models = parse_design_arena(card)
        assert [m["model"] for m in models] == [
            "Claude Opus 4.6",
            "Claude Opus 4.6 (Thinking)",
            "Gemini 3.6 Flash",
        ]

    def test_basura_lanza_error(self):
        with pytest.raises(ParseError):
            parse_design_arena("Leaderboards\nBlog\nMethodology")


class TestTrendingParsers:
    def test_github_search(self):
        payload = {"items": [{"full_name": "a/b", "stargazers_count": 100, "language": "Python", "description": "d"}]}
        repos = parse_github_search(payload)
        assert repos[0]["stars"] == 100 and repos[0]["url"].endswith("a/b")

    def test_github_search_vacio(self):
        with pytest.raises(ParseError):
            parse_github_search({"items": []})
        with pytest.raises(ParseError):
            parse_github_search({})

    def test_hn_stories(self):
        payload = {"hits": [{"title": "T", "points": 100, "num_comments": 5, "objectID": "1", "url": "https://x"}]}
        stories = parse_hn_stories(payload)
        assert stories[0]["comments"] == 5
        assert "item?id=1" not in stories[0]["url"]

    def test_hn_sin_url_usa_item(self):
        payload = {"hits": [{"title": "T", "points": 9, "objectID": "42"}]}
        assert "item?id=42" in parse_hn_stories(payload)[0]["url"]

    def test_hn_vacio(self):
        with pytest.raises(ParseError):
            parse_hn_stories({"hits": []})

    def test_hf_trending(self):
        payload = {
            "recentlyTrending": [
                {"repoData": {"id": "Qwen/Qwen3-32B", "likes": 850, "downloads": 120000, "pipeline_tag": "text-generation"}}
            ]
        }
        models = parse_hf_trending(payload)
        assert len(models) == 1
        assert models[0]["id"] == "Qwen/Qwen3-32B"
        assert models[0]["likes"] == 850

    def test_arena_hf_y_mirror_json_incluyen_claude_opus_5_5(self):
        from src.parsers import parse_arena_hf_dataset, parse_arena_mirror_json

        hf_rows = {
            "Text": [
                {"row": {"rank": i, "model_name": f"Model-{i}", "rating": 1545 - i * 3, "vote_count": 5000, "organization": "lab"}}
                for i in range(1, 12)
            ]
        }
        hf_rows["Text"][3]["row"]["model_name"] = "Claude Opus 5.5 (High)"
        parsed_hf = parse_arena_hf_dataset(hf_rows)
        assert any(m["model"] == "Claude Opus 5.5 (High)" and m["category"] == "Text" for m in parsed_hf)

        mirror_payload = {
            "rows": [
                {"arenaName": f"Model-{i}", "vendor": "lab", "rankO": i, "elo": 1545.0 - i * 3, "votes": 4000}
                for i in range(1, 12)
            ]
        }
        mirror_payload["rows"][3]["arenaName"] = "Claude Opus 5.5 (High)"
        mirror_payload["rows"][3]["rankC"] = 2
        mirror_payload["rows"][3]["eloC"] = 1545.5
        parsed_mirror = parse_arena_mirror_json(mirror_payload)
        assert any(m["model"] == "Claude Opus 5.5 (High)" and m["category"] == "Text" for m in parsed_mirror)
        assert any(m["model"] == "Claude Opus 5.5 (High)" and m["category"] == "Code" for m in parsed_mirror)

