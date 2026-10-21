"""Fixtures compartidas: textos reales de captura (tests/fixtures) y snapshots v3."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def raw_sources() -> dict[str, str]:
    """Textos crudos reales capturados por Playwright (arena, openrouter, design)."""
    return {name: (FIXTURES / f"{name}.txt").read_text(encoding="utf-8") for name in ("arena", "openrouter", "design")}


def make_snapshot(sources: dict) -> dict:
    return {"generated_at": "2026-10-21T10:00:00", "sources": sources}


def models_source(models: list[dict], status: str = "ok", **extra) -> dict:
    return {"status": status, "models": models, **extra}


@pytest.fixture
def v3_snapshot(raw_sources) -> dict:
    """Snapshot estructurado construido desde las capturas reales."""
    from src.parsers import parse_arena, parse_design_arena, parse_openrouter_page

    return make_snapshot(
        {
            "arena": models_source(parse_arena(raw_sources["arena"])),
            "openrouter": models_source(parse_openrouter_page(raw_sources["openrouter"])),
            "design": models_source(parse_design_arena(raw_sources["design"])),
        },
    )
