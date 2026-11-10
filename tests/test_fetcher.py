"""Captura ligera HTTP-first y modo sin Playwright."""

from src.fetcher import DataFetcher, _html_to_text


def test_html_to_text_limpia_tags_y_scripts():
    html_doc = """
    <html><head><style>body{color:red}</style><script>alert(1)</script></head>
    <body>
      <table>
        <tr><td>#1</td><td>Kimi K3</td><td>1420</td><td>+5</td><td>62.4%</td></tr>
        <tr><td>#2</td><td>Muse Spark</td><td>1401</td><td>+3</td><td>59.1%</td></tr>
        <tr><td>#3</td><td>GPT-6 Astra</td><td>1395</td><td>+2</td><td>58.0%</td></tr>
        <tr><td>#4</td><td>Claude Opus</td><td>1388</td><td>+1</td><td>56.5%</td></tr>
        <tr><td>#5</td><td>Gemini 4</td><td>1380</td><td>0</td><td>55.0%</td></tr>
      </table>
    </body></html>
    """
    text = _html_to_text(html_doc)
    assert "alert(1)" not in text
    assert "#1\nKimi K3\n1420" in text


async def test_fetch_all_http_ssr_evita_playwright(monkeypatch):
    fetcher = DataFetcher()

    async def fake_arena_api():
        return [{"rank": i + 1, "model": f"model-{i}", "score": 1500 - i, "category": "Text"} for i in range(10)]

    async def fake_or_api():
        return {"data": [{"rank": i + 1, "model": f"or-{i}", "tokens": 1000 * (10 - i)} for i in range(5)]}

    async def fake_catalog():
        return None

    async def fake_http(url):
        return "\n".join(f"#{i}\nDesignModel-{i}\n{1450 - i}\n100\n{60 - i}.0%" for i in range(1, 7))

    browser_called = []

    async def fake_load_pages(urls):
        browser_called.append(dict(urls))
        return {}, {}

    monkeypatch.setattr(fetcher, "_fetch_arena_api", fake_arena_api)
    monkeypatch.setattr(fetcher, "_fetch_openrouter_api", fake_or_api)
    monkeypatch.setattr(fetcher, "_fetch_openrouter_catalog", fake_catalog)
    monkeypatch.setattr(fetcher, "_fetch_page_http", fake_http)
    monkeypatch.setattr(fetcher, "_load_pages", fake_load_pages)

    res = await fetcher.fetch_all()
    assert res["design"]["status"] == "ok"
    assert res["design"]["method"] == "http"
    assert browser_called == [{}]


async def test_disable_playwright_omite_navegador(monkeypatch):
    monkeypatch.setattr("src.fetcher.DISABLE_PLAYWRIGHT", True)
    fetcher = DataFetcher()
    texts, errors = await fetcher._load_pages({"design": "https://example.com"})
    assert texts == {}
    assert "DISABLE_PLAYWRIGHT" in errors["design"]
