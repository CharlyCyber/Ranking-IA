"""Parsers: deterministas y completos contra capturas reales."""

import pytest

from src.parsers import (
    ParseError,
    parse_arena,
    parse_design_arena,
    parse_github_search,
    parse_hn_stories,
    parse_openrouter_api,
    parse_openrouter_page,
)


class TestArena:
    def test_80_filas_8_categorias(self, raw_sources):
        models = parse_arena(raw_sources["arena"])
        assert len(models) == 80
        cats = {m["category"] for m in models}
        assert cats == {"Text", "Code", "Vision", "Text-to-Image", "Image Edit", "Search", "Text-to-Video", "Image-to-Video"}

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
        """La fila10 usa '1452\\n\\t10,184' (salto de línea entre score y votes)."""
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

    def test_ranks_unicos(self, raw_sources):
        ranks = [m["rank"] for m in parse_openrouter_page(raw_sources["openrouter"])]
        assert ranks == sorted(set(ranks))

    def test_parse_api_variantes_de_esquema(self):
        rows = {"data": [
            {"model": "a/b", "tokens": 5_000_000, "position": 1, "growth": 10},
            {"model": "c/d", "tokens": 4_000_000, "position": 2, "growth": 5},
            {"model": "e/f", "tokens": 3_000_000, "position": 3, "growth": 2},
        ]}
        models = parse_openrouter_api(rows)
        assert models[0]["rank"] == 1 and models[0]["tokens_week"] == 5_000_000
        lista = [{"name": n, "token_count": "1234"} for n in ("x", "y", "z")]
        assert parse_openrouter_api(lista)[0]["tokens_week"] == 1234
        with pytest.raises(ParseError):
            parse_openrouter_api({"foo": 1})
        with pytest.raises(ParseError):
            parse_openrouter_api({"data": [{"model": "solo-uno"}]})  # mínimo3 filas


class TestDesignArena:
    def test_tabla_completa_con_winrate(self, raw_sources):
        models = parse_design_arena(raw_sources["design"])
        assert len(models) >= 10
        assert models[0]["model"] == "Claude Opus 4.6"
        assert models[0]["score"] == 1397
        assert models[0]["win_rate_pct"] == 69.2

    def test_scores_repetidos_no_dedupean(self, raw_sources):
        """ELOs duplicados son legítimos (varios modelos empatados)."""
        models = parse_design_arena(raw_sources["design"])
        scores = [m["score"] for m in models]
        assert len(scores) > len(set(scores))  # hay empates reales

    def test_fallback_tarjeta_top20(self):
        card = "Top 20\nAll Models\nWin Rate\nElo Rating\n1397\n\nClaude\nOpus 4.6\n\n1375\n\nClaude\nOpus 4.6 (Thinking)\n\n1344\n\nKimi\nK2.5\n\n"
        models = parse_design_arena(card)
        assert [m["model"] for m in models] == ["Claude Opus 4.6", "Claude Opus 4.6 (Thinking)", "Kimi K2.5"]

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
        assert "item?id=1" not in stories[0]["url"]  # usa la url original

    def test_hn_sin_url_usa_item(self):
        payload = {"hits": [{"title": "T", "points": 9, "objectID": "42"}]}
        assert "item?id=42" in parse_hn_stories(payload)[0]["url"]

    def test_hn_vacio(self):
        with pytest.raises(ParseError):
            parse_hn_stories({"hits": []})
