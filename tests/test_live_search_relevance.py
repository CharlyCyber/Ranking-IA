from src import live_search


class _Response:
    def raise_for_status(self):
        return None

    @staticmethod
    def json():
        return {
            "results": [
                {
                    "title": "Boeing deliveries decline in 2024",
                    "url": "https://news.example.org/boeing",
                    "content": "A report about aircraft deliveries.",
                    "raw_content": "Boeing reported fewer aircraft deliveries.",
                },
                {
                    "title": "Firelight launches a staking protocol",
                    "url": "https://crypto.example.org/firelight",
                    "content": "A report about crypto staking.",
                    "raw_content": "Firelight announced a new protocol.",
                },
            ]
        }


class _Client:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, *args, **kwargs):
        return _Response()


def test_unrelated_tavily_hits_are_not_returned_as_evidence(monkeypatch):
    class FakeDb:
        def consume_limit(self, *args):
            return True

    monkeypatch.setattr(live_search, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(live_search.httpx, "Client", _Client)
    live_search._SEARCH_CACHE.clear()

    result = live_search.search_live_web(
        "¿Qué modelo lidera Arena, categoría Text? Cita solo fuentes que lo confirmen.",
        FakeDb(),
    )

    assert result["status"] == "no_results"
    assert result["sources"] == []
