"""Dashboard Web Público v4.3 (Obsidian + Neon Lime + White Split Sheet · UX Sencilla) + API + RSS."""

import time
from datetime import datetime, timezone
from email.utils import format_datetime

from flask import Blueprint, Response, jsonify, render_template, request

from src.db import Database
from src.html_utils import escape_html
from src.insights import compute_value_leaders, fmt_compact_number, render_elo_svg, search_models

web = Blueprint("web", __name__, template_folder="templates")
db = Database()
db.get_or_seed_latest_snapshot()

_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 128
_API_CACHE: dict[tuple, tuple[float, object]] = {}


def _cache_get(key: tuple):
    item = _API_CACHE.get(key)
    if not item:
        return None
    expires_at, value = item
    if time.monotonic() > expires_at:
        _API_CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: tuple, value, ttl: float = _CACHE_TTL_SECONDS) -> None:
    if len(_API_CACHE) >= _CACHE_MAX_ENTRIES:
        now = time.monotonic()
        expired = [k for k, (exp, _v) in _API_CACHE.items() if now > exp]
        for k in expired:
            _API_CACHE.pop(k, None)
        if len(_API_CACHE) >= _CACHE_MAX_ENTRIES:
            _API_CACHE.clear()
    _API_CACHE[key] = (time.monotonic() + ttl, value)


def _cached_json(payload, max_age: int = 60):
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = f"public, max-age={max_age}"
    return resp


def _safe_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        val = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        val = default
    return max(minimum, min(maximum, val))


def _latest_snapshot() -> dict | None:
    cache_key = ("latest_snapshot", id(db))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    loader = getattr(db, "get_or_seed_latest_snapshot", db.get_latest_snapshot)
    snap = loader()
    if snap is not None:
        _cache_set(cache_key, snap)
    return snap


def _build_elo_series_from_snapshots(source: str, category: str | None, days: int, limit: int) -> dict[str, list]:
    get_snaps = getattr(db, "get_snapshots", None)
    snapshots = list(reversed(get_snaps(days=days))) if get_snaps else []
    series: dict[str, list] = {}

    for snap in snapshots:
        data = snap.get("data") or {}
        src = (data.get("sources") or {}).get(source)
        if not isinstance(src, dict) or src.get("status") not in ("ok", "stale"):
            continue
        for m in src.get("models") or []:
            if not isinstance(m, dict):
                continue
            if category and (m.get("category") or "default") != category:
                continue
            value = m.get("score", m.get("tokens_week"))
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            series.setdefault(m.get("model", "?"), []).append([snap["created_at"], value])

    if not series:
        return {}
    ranked = sorted(series.items(), key=lambda kv: kv[1][-1][1], reverse=True)[:limit]
    return {name: points for name, points in ranked}


def _build_elo_series(source: str, category: str | None, days: int, limit: int) -> dict[str, list]:
    if source == "arena" and not category:
        category = "Text"
    cache_key = ("elo_series", id(db), source, category or "", days, limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    get_series = getattr(db, "get_elo_series", None)
    series = get_series(source=source, category=category, days=days, limit=limit) if callable(get_series) else {}
    if not series:
        series = _build_elo_series_from_snapshots(source=source, category=category, days=days, limit=limit)

    _cache_set(cache_key, series)
    return series


@web.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@web.route("/api/rankings")
def api_rankings():
    snapshot = _latest_snapshot()
    if snapshot:
        return _cached_json(snapshot)
    return jsonify({"error": "Aún no hay datos"}), 404


@web.route("/api/history")
def api_history():
    days = _safe_int(request.args.get("days"), 30, 1, 90)
    cache_key = ("history", id(db), days)
    snapshots = _cache_get(cache_key)
    if snapshots is None:
        snapshots = db.get_snapshots(days=days)
        _cache_set(cache_key, snapshots)
    return _cached_json(snapshots)


@web.route("/api/changes")
def api_changes():
    days = _safe_int(request.args.get("days"), 30, 1, 90)
    cache_key = ("changes", id(db), days)
    changes = _cache_get(cache_key)
    if changes is None:
        changes = db.get_changes(days=days)
        _cache_set(cache_key, changes)
    return _cached_json(changes)


@web.route("/api/elo-history")
def api_elo_history():
    """Series temporales de puntuación por modelo con validación segura de parámetros."""
    source = (request.args.get("source") or "arena").strip().lower()
    days = _safe_int(request.args.get("days"), 30, 1, 90)
    limit = _safe_int(request.args.get("limit"), 8, 1, 20)
    category = request.args.get("category")
    if source == "arena" and not category:
        category = "Text"

    series = _build_elo_series(source=source, category=category, days=days, limit=limit)
    if not series:
        return _cached_json(
            {"source": source, "category": category, "series": {}, "note": "Sin snapshots estructurados en el rango"}
        )

    return _cached_json({"source": source, "category": category, "series": series})


@web.route("/api/models/search")
def api_models_search():
    """Buscador público instantáneo de modelos en todos los rankings."""
    q = (request.args.get("q") or "").strip()[:80]
    limit = _safe_int(request.args.get("limit"), 12, 1, 30)
    snap_wrap = _latest_snapshot()
    data = (snap_wrap or {}).get("data")
    results = search_models(data, q, limit=limit)
    return _cached_json({"query": q, "count": len(results), "results": results})


@web.route("/api/value-leaders")
def api_value_leaders():
    """Radar Calidad/Precio (ELO vs $/1M tokens) y modelos :free disponibles."""
    limit = _safe_int(request.args.get("limit"), 10, 1, 25)
    snap_wrap = _latest_snapshot()
    data = (snap_wrap or {}).get("data")
    return _cached_json(compute_value_leaders(data, limit=limit))


@web.route("/api/badge/<source>")
def api_shields_badge(source: str):
    """Endpoint JSON compatible con Shields.io para incrustar el #1 actual en cualquier README."""
    source = source.strip().lower()
    category = request.args.get("category")
    snap_wrap = _latest_snapshot()
    sources = (((snap_wrap or {}).get("data") or {}).get("sources")) or {}
    payload = sources.get(source) or {}
    models = payload.get("models") or []

    if source == "arena" and not category:
        category = "Text"
    if category:
        models = [m for m in models if m.get("category") == category]

    models = sorted(models, key=lambda m: m.get("rank", 999))
    label = f"{source} · {category}" if category else source
    if not models:
        return _cached_json({"schemaVersion": 1, "label": label, "message": "sin datos", "color": "lightgrey"})

    top = models[0]
    if top.get("score") is not None:
        metric = f" ({top['score']} elo)"
    elif top.get("tokens_week") is not None:
        metric = f" ({fmt_compact_number(top['tokens_week'])} tok)"
    elif top.get("win_rate_pct") is not None:
        metric = f" ({top['win_rate_pct']}%)"
    else:
        metric = ""

    return _cached_json(
        {
            "schemaVersion": 1,
            "label": f"🏆 {label} #1",
            "message": f"{top.get('model')}{metric}",
            "color": "84cc16",
        }
    )


@web.route("/api/chart.svg")
def api_chart_svg():
    """Gráfico SVG dinámico sin dependencias pesadas (incrustable en web, README o Telegram)."""
    source = (request.args.get("source") or "arena").strip().lower()
    category = request.args.get("category")
    if source == "arena" and not category:
        category = "Text"
    days = _safe_int(request.args.get("days"), 30, 1, 90)
    limit = _safe_int(request.args.get("limit"), 6, 1, 10)
    series = _build_elo_series(source=source, category=category, days=days, limit=limit)
    title = f"Ranking IA · {source.upper()}" + (f" ({category})" if category else "")
    svg = render_elo_svg(series, title=title)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=300"})


@web.route("/feed.xml")
def rss_feed():
    """Feed RSS 2.0 público y gratuito de cambios en rankings de IA."""
    changes = getattr(db, "get_changes", lambda days=30: [])(days=30)[:30]
    base_url = request.url_root.rstrip("/")
    items_xml = []
    for idx, ch in enumerate(changes):
        raw_dt = str(ch.get("detected_at") or "")
        try:
            dt = datetime.fromisoformat(raw_dt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            pub_date = format_datetime(dt)
        except ValueError:
            pub_date = format_datetime(datetime.now(timezone.utc))
        summary = escape_html(ch.get("summary") or "")
        guid = escape_html(f"{raw_dt}-{idx}")
        items_xml.append(
            f"<item>"
            f"<title>{summary[:110]}</title>"
            f"<link>{escape_html(base_url)}/dashboard</link>"
            f'<guid isPermaLink="false">{guid}</guid>'
            f"<pubDate>{pub_date}</pubDate>"
            f"<description>{summary}</description>"
            f"</item>"
        )

    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>Ranking AI Monitor — Alertas de Modelos IA</title>"
        f"<link>{escape_html(base_url)}/dashboard</link>"
        "<description>Cambios verificados en LMSYS Arena, OpenRouter, Design Arena y GitHub</description>"
        "<language>es</language>"
        + "".join(items_xml)
        + "</channel></rss>"
    )
    return Response(
        rss,
        mimetype="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=60"},
    )
