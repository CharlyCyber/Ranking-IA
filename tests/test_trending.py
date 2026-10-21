"""Tendencias: plantillas HTML y fallback de HN a la API oficial (Firebase)."""

import httpx
import pytest

from src import trending as trending_mod
from src.trending import TrendingError, fetch_hackernews, render_trending_brief

GH_OK = {
    "status": "ok",
    "ai": [{"full_name": "ai/repo", "stars": 1234, "description": "Un agente top", "url": "https://github.com/ai/repo"}],
    "general": [{"full_name": "dev/tool", "stars": 700, "description": "", "url": "https://github.com/dev/tool"}],
    "items": [],
}
HN_OK = {
    "status": "ok",
    "items": [{"title": "Show HN: algo", "points": 300, "comments": 42, "url": "https://example.com/x"}],
}


class TestRender:
    def test_brief_completo(self):
        text = render_trending_brief(GH_OK, HN_OK)
        assert "Tendencias en GitHub" in text
        assert "ai/repo" in text
        assert "Hacker News" in text
        assert '<a href="https://example.com/x">' in text
        assert "\\n" not in text  # sin escapes literales rotos

    def test_brief_sin_datos(self):
        text = render_trending_brief({"status": "error"}, {"status": "error"})
        assert "no están disponibles" in text


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._payload


class _FakeFirebaseClient:
    """Sirve topstories.json y item/{id}.json de la API oficial de HN."""

    ITEMS = {
        "111": {"type": "story", "title": "Gran historia", "score": 500, "descendants": 90, "url": "https://a"},
        "222": {"type": "story", "title": "Historia floja", "score": 5, "descendants": 1, "url": "https://b"},
        "333": {"type": "story", "title": "Otra buena", "score": 200, "descendants": 30},  # sin url
    }

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        if "topstories" in url:
            return _FakeResp([111, 222, 333])
        if "/item/" in url:
            import re

            item_id = re.search(r"item/(\d+)", url).group(1)
            return _FakeResp(self.ITEMS.get(item_id, {}))
        return _FakeResp({}, status=404)


async def test_hn_fallback_a_firebase_si_algolia_falla(monkeypatch):
    async def blocked_algolia(*args, **kwargs):
        raise TrendingError("HN API no disponible:403")

    monkeypatch.setattr(trending_mod, "_fetch_hn_algolia", blocked_algolia)
    monkeypatch.setattr(trending_mod.httpx, "AsyncClient", _FakeFirebaseClient)

    result = await fetch_hackernews(top_n=5, min_points=100)

    assert result["status"] == "ok"
    titles = [s["title"] for s in result["items"]]
    assert titles == ["Gran historia", "Otra buena"]  # la floja se filtra por puntos
    # la historia sin url usa el enlace de HN
    assert any("item?id=333" in s["url"] for s in result["items"])


async def test_hn_lanza_error_si_ambas_fuentes_fallan(monkeypatch):
    async def blocked(*args, **kwargs):
        raise TrendingError("bloqueada")

    class DeadClient(_FakeFirebaseClient):
        async def get(self, url, params=None):
            return _FakeResp({}, status=403)

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403", request=None, response=None)

    monkeypatch.setattr(trending_mod, "_fetch_hn_algolia", blocked)
    monkeypatch.setattr(trending_mod.httpx, "AsyncClient", lambda *a, **k: DeadClient())

    with pytest.raises(TrendingError):
        await fetch_hackernews()
