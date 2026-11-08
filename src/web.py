"""Dashboard Web Público v4.3 (Obsidian + Neon Lime + White Split Sheet · UX Sencilla) + API + RSS."""

from datetime import datetime, timezone
from email.utils import format_datetime

from flask import Blueprint, Response, jsonify, render_template_string, request

from src.db import Database
from src.html_utils import escape_html
from src.insights import compute_value_leaders, fmt_compact_number, render_elo_svg, search_models

web = Blueprint("web", __name__)
db = Database()
db.get_or_seed_latest_snapshot()


def _safe_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        val = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        val = default
    return max(minimum, min(maximum, val))


def _latest_snapshot() -> dict | None:
    loader = getattr(db, "get_or_seed_latest_snapshot", db.get_latest_snapshot)
    return loader()


def _build_elo_series(source: str, category: str | None, days: int, limit: int) -> dict[str, list]:
    if source == "arena" and not category:
        category = "Text"
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


@web.route("/dashboard")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@web.route("/api/rankings")
def api_rankings():
    snapshot = _latest_snapshot()
    if snapshot:
        return jsonify(snapshot)
    return jsonify({"error": "Aún no hay datos"}), 404


@web.route("/api/history")
def api_history():
    days = _safe_int(request.args.get("days"), 30, 1, 90)
    snapshots = db.get_snapshots(days=days)
    return jsonify(snapshots)


@web.route("/api/changes")
def api_changes():
    days = _safe_int(request.args.get("days"), 30, 1, 90)
    changes = db.get_changes(days=days)
    return jsonify(changes)


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
        return jsonify(
            {"source": source, "category": category, "series": {}, "note": "Sin snapshots estructurados en el rango"}
        )

    return jsonify({"source": source, "category": category, "series": series})


@web.route("/api/models/search")
def api_models_search():
    """Buscador público instantáneo de modelos en todos los rankings."""
    q = (request.args.get("q") or "").strip()[:80]
    limit = _safe_int(request.args.get("limit"), 12, 1, 30)
    snap_wrap = _latest_snapshot()
    data = (snap_wrap or {}).get("data")
    results = search_models(data, q, limit=limit)
    return jsonify({"query": q, "count": len(results), "results": results})


@web.route("/api/value-leaders")
def api_value_leaders():
    """Radar Calidad/Precio (ELO vs $/1M tokens) y modelos :free disponibles."""
    limit = _safe_int(request.args.get("limit"), 10, 1, 25)
    snap_wrap = _latest_snapshot()
    data = (snap_wrap or {}).get("data")
    return jsonify(compute_value_leaders(data, limit=limit))


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
        return jsonify({"schemaVersion": 1, "label": label, "message": "sin datos", "color": "lightgrey"})

    top = models[0]
    if top.get("score") is not None:
        metric = f" ({top['score']} elo)"
    elif top.get("tokens_week") is not None:
        metric = f" ({fmt_compact_number(top['tokens_week'])} tok)"
    elif top.get("win_rate_pct") is not None:
        metric = f" ({top['win_rate_pct']}%)"
    else:
        metric = ""

    return jsonify(
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
        '<rss version="1.0" xmlns:atom="http://www.w3.org/2005/Atom"></rss>'
        if False
        else (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            "<title>Ranking AI Monitor — Alertas de Modelos IA</title>"
            f"<link>{escape_html(base_url)}/dashboard</link>"
            "<description>Cambios verificados en LMSYS Arena, OpenRouter, Design Arena y GitHub</description>"
            "<language>es</language>"
            + "".join(items_xml)
            + "</channel></rss>"
        )
    )
    return Response(rss, mimetype="application/rss+xml; charset=utf-8")


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ranking IA · ¿Cuál es la mejor IA hoy?</title>
    <link rel="alternate" type="application/rss+xml" title="Ranking AI Cambios RSS" href="/feed.xml">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://telegram.org/js/telegram-web-app.js" defer></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <style>
        :root {
            --bg-canvas: #0c1017;
            --bg-card: #161b24;
            --bg-card-2: #1f2632;
            --bg-pill: #181e28;
            --lime: #a3ff12;
            --lime-dim: rgba(163, 255, 18, 0.14);
            --sheet: #ffffff;
            --ink-dark: #0f141c;
            --ink-gray: #647082;
            --text-light: #f4f7fb;
            --text-muted: #8e98aa;
            --border-subtle: rgba(255, 255, 255, 0.07);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg-canvas);
            color: var(--text-light);
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
            padding-bottom: 40px;
        }

        .shell {
            max-width: 1340px;
            margin: 0 auto;
            padding: 18px 26px 32px;
        }

        /* ───────────── 1. TOP NAVIGATION BAR ───────────── */
        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            padding: 6px 0 20px;
            flex-wrap: wrap;
        }

        .brand-cluster {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .brand-logo {
            display: flex;
            align-items: center;
            gap: 9px;
            text-decoration: none;
            color: var(--text-light);
            font-weight: 700;
            font-size: 1.08rem;
            letter-spacing: -0.02em;
        }

        .brand-logo svg {
            width: 22px;
            height: 22px;
            color: var(--text-light);
        }

        .nav-center {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }

        .white-nav-pill {
            background: #f8fafc;
            border-radius: 999px;
            padding: 4px;
            display: inline-flex;
            align-items: center;
            gap: 2px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
            flex-wrap: wrap;
        }

        .nav-tab {
            border: none;
            background: transparent;
            color: #262e3b;
            font-family: inherit;
            font-size: 0.8rem;
            font-weight: 500;
            padding: 8px 16px;
            border-radius: 999px;
            cursor: pointer;
            transition: all 0.18s ease;
            white-space: nowrap;
        }

        .nav-tab:hover { color: #000; }

        .nav-tab.active {
            background: var(--lime);
            color: #0b0f14;
            font-weight: 700;
        }

        .nav-tab.active::before { content: "• "; }

        .icon-circle {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--bg-pill);
            border: 1px solid var(--border-subtle);
            color: var(--text-muted);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            text-decoration: none;
            font-size: 0.84rem;
            transition: all 0.18s ease;
            position: relative;
        }

        .icon-circle:hover {
            background: var(--bg-card-2);
            color: var(--text-light);
            border-color: rgba(255, 255, 255, 0.16);
        }

        .icon-dot {
            position: absolute;
            top: 7px;
            right: 7px;
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--lime);
        }

        .topbar-right {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .profile-avatar {
            width: 38px;
            height: 38px;
            border-radius: 50%;
            background: linear-gradient(135deg, #f8fafc 0%, #cbd5e1 100%);
            border: 2px solid rgba(255, 255, 255, 0.18);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: #0f172a;
            font-weight: 800;
            font-size: 0.78rem;
        }

        /* ───────────── 2. HERO HEADER + 1-CLICK BEGINNER GOALS ───────────── */
        .hero-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 14px;
            gap: 12px;
            flex-wrap: wrap;
        }

        .hero-left {
            display: flex;
            align-items: center;
            gap: 14px;
        }

        .hero-title {
            font-size: 2rem;
            font-weight: 500;
            letter-spacing: -0.025em;
            color: #ffffff;
        }

        .hero-subtitle {
            color: var(--text-muted);
            font-size: 0.85rem;
            margin-top: 2px;
        }

        .hero-actions {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .btn-dark-pill {
            background: var(--bg-pill);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: var(--text-light);
            font-family: inherit;
            font-size: 0.8rem;
            font-weight: 500;
            padding: 9px 16px;
            border-radius: 999px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.18s ease;
        }

        .btn-dark-pill:hover {
            background: var(--bg-card-2);
            border-color: rgba(163, 255, 18, 0.45);
        }

        /* Beginner 1-click Goal Strip */
        .goals-strip {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 18px;
            flex-wrap: wrap;
        }

        .goals-label {
            font-size: 0.78rem;
            color: var(--text-muted);
            margin-right: 4px;
        }

        .goal-chip {
            border: 1px solid var(--border-subtle);
            background: var(--bg-card);
            color: #dce3ee;
            font-family: inherit;
            font-size: 0.79rem;
            font-weight: 500;
            padding: 8px 15px;
            border-radius: 999px;
            cursor: pointer;
            transition: all 0.18s ease;
        }

        .goal-chip:hover {
            border-color: rgba(163, 255, 18, 0.45);
            color: #ffffff;
        }

        .goal-chip.active {
            background: var(--lime-dim);
            border-color: var(--lime);
            color: var(--lime);
            font-weight: 600;
        }

        /* Collapsible Beginner Guide Banner */
        .guide-banner {
            display: none;
            background: #141a24;
            border: 1px solid rgba(163, 255, 18, 0.35);
            border-radius: 22px;
            padding: 18px 22px;
            margin-bottom: 18px;
            gap: 18px;
            grid-template-columns: repeat(3, 1fr);
        }

        .guide-banner.open {
            display: grid;
        }

        @media (max-width: 820px) {
            .guide-banner { grid-template-columns: 1fr; }
        }

        .guide-step strong {
            display: block;
            color: var(--lime);
            font-size: 0.84rem;
            margin-bottom: 4px;
        }

        .guide-step p {
            color: #cbd5e1;
            font-size: 0.79rem;
            line-height: 1.45;
        }

        /* ───────────── 3. TOP ASYMMETRIC BENTO CARDS ───────────── */
        .bento-top {
            display: grid;
            grid-template-columns: 1.38fr 1fr;
            gap: 18px;
            margin-bottom: 18px;
        }

        @media (max-width: 1024px) {
            .bento-top { grid-template-columns: 1fr; }
        }

        .dark-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 28px;
            padding: 25px 28px;
            position: relative;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            min-height: 244px;
        }

        .metrics-triple {
            display: grid;
            grid-template-columns: 1.15fr 1fr 0.85fr;
            gap: 18px;
            margin-bottom: 24px;
        }

        @media (max-width: 640px) {
            .metrics-triple { grid-template-columns: 1fr; gap: 14px; }
        }

        .metric-label {
            color: var(--text-muted);
            font-size: 0.78rem;
            font-weight: 400;
            margin-bottom: 7px;
        }

        .metric-big {
            font-size: 1.95rem;
            font-weight: 400;
            letter-spacing: -0.03em;
            color: #ffffff;
            font-variant-numeric: tabular-nums;
            line-height: 1.12;
        }

        .metric-big .prefix {
            color: var(--text-muted);
            font-size: 1.1rem;
            font-weight: 400;
            margin-right: 4px;
        }

        .metric-big .suffix {
            color: var(--text-muted);
            font-size: 1rem;
            font-weight: 300;
            margin-left: 4px;
        }

        .metric-hint {
            font-size: 0.73rem;
            color: var(--lime);
            margin-top: 4px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        /* Progress bars + overlapping lab avatars */
        .bars-grid {
            display: grid;
            grid-template-columns: 1fr 1.2fr 0.9fr;
            gap: 18px;
            align-items: end;
        }

        @media (max-width: 640px) {
            .bars-grid { grid-template-columns: 1fr; }
        }

        .bar-col-head {
            display: flex;
            justify-content: space-between;
            color: var(--text-muted);
            font-size: 0.75rem;
            margin-bottom: 8px;
        }

        .bar-track {
            height: 10px;
            background: #252c39;
            border-radius: 999px;
            overflow: hidden;
            margin-bottom: 14px;
        }

        .bar-fill {
            height: 100%;
            background: var(--lime);
            border-radius: 999px;
            transition: width 0.5s ease;
        }

        .avatar-stack {
            display: flex;
            align-items: center;
            justify-content: center;
            padding-left: 8px;
        }

        .lab-chip {
            width: 30px;
            height: 30px;
            border-radius: 50%;
            border: 2.5px solid var(--bg-card);
            margin-left: -8px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 0.68rem;
            font-weight: 700;
            color: #0f141c;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
        }

        /* Right Top Card: Spotlight & 3 Wallet Cards */
        .corner-arrow {
            position: absolute;
            top: 20px;
            right: 20px;
            width: 34px;
            height: 34px;
            border-radius: 50%;
            background: #202734;
            border: 1px solid var(--border-subtle);
            color: var(--text-light);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.85rem;
        }

        .spotlight-row {
            display: flex;
            align-items: baseline;
            gap: 12px;
            flex-wrap: wrap;
            margin-bottom: 16px;
        }

        .pill-subtle {
            background: #212835;
            color: #cbd5e1;
            font-size: 0.73rem;
            padding: 4px 11px;
            border-radius: 999px;
            font-weight: 500;
        }

        .wallet-strip {
            display: flex;
            align-items: flex-end;
            gap: 10px;
        }

        .wallet-cards {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
            flex: 1;
            align-items: end;
        }

        .w-card {
            background: #202734;
            border-radius: 18px;
            padding: 13px 14px;
            height: 92px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            cursor: pointer;
            transition: all 0.2s ease;
            border: 1px solid transparent;
        }

        .w-card:hover {
            border-color: rgba(255, 255, 255, 0.18);
        }

        .w-card .w-top {
            font-size: 0.78rem;
            font-weight: 600;
            color: #e2e8f0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .w-card .w-sub {
            font-size: 0.73rem;
            color: var(--text-muted);
        }

        .w-card.featured {
            background: var(--lime);
            height: 114px;
            border-radius: 20px;
            color: #0b0f14;
            box-shadow: 0 10px 26px rgba(163, 255, 18, 0.22);
        }

        .w-card.featured .w-top {
            color: #0b0f14;
            font-weight: 800;
        }

        .w-card.featured .w-sub {
            color: rgba(11, 15, 20, 0.8);
            font-weight: 600;
        }

        .btn-white-pill {
            background: #ffffff;
            color: #0b0f14;
            border: none;
            font-family: inherit;
            font-size: 0.76rem;
            font-weight: 700;
            padding: 11px 16px;
            border-radius: 999px;
            cursor: pointer;
            white-space: nowrap;
            transition: transform 0.15s ease;
        }

        .btn-white-pill:hover { transform: translateY(-1px); }

        /* ───────────── 4. ACTIVE FILTERS BAR ───────────── */
        .filters-bar {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }

        .filters-title {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-size: 0.82rem;
            color: #d8dee9;
            margin-right: 4px;
            white-space: nowrap;
        }

        .count-dot {
            background: #ffffff;
            color: #0b0f14;
            font-size: 0.7rem;
            font-weight: 800;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }

        .filter-pill {
            flex: 1;
            min-width: 160px;
            background-color: var(--bg-card);
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='22' height='22' viewBox='0 0 24 24'%3E%3Ccircle cx='12' cy='12' r='11' fill='%23212835'/%3E%3Cpath d='M8.5 10.5L12 14l3.5-3.5' fill='none' stroke='%2394a3b8' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 8px center;
            background-size: 22px 22px;
            border: 1px solid var(--border-subtle);
            border-radius: 999px;
            padding: 9px 38px 9px 16px;
            color: #cbd5e1;
            font-family: inherit;
            font-size: 0.78rem;
            outline: none;
            appearance: none;
            cursor: pointer;
        }

        .filter-pill:focus { border-color: rgba(163, 255, 18, 0.55); }

        .search-pill-wrap {
            flex: 1.25;
            min-width: 220px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 999px;
            padding: 4px 6px 4px 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .search-pill-wrap input {
            background: transparent;
            border: none;
            color: var(--text-light);
            font-family: inherit;
            font-size: 0.79rem;
            width: 100%;
            outline: none;
        }

        .search-pill-wrap input::placeholder { color: #647084; }

        .search-icon-btn {
            width: 30px;
            height: 30px;
            border-radius: 50%;
            background: #212835;
            color: #cbd5e1;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 0.76rem;
            flex-shrink: 0;
        }

        /* ───────────── 5. WHITE BOTTOM MASTER-DETAIL SHEET + NOTCH ───────────── */
        .white-sheet {
            background: var(--sheet);
            color: var(--ink-dark);
            border-radius: 32px;
            padding: 26px 26px 28px;
            position: relative;
            box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
        }

        .sheet-notch {
            position: absolute;
            top: 0;
            left: 50%;
            transform: translateX(-50%);
            background: var(--bg-canvas);
            padding: 4px 14px 12px;
            border-radius: 0 0 24px 24px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            z-index: 5;
        }

        .sheet-notch::before,
        .sheet-notch::after {
            content: "";
            position: absolute;
            top: 0;
            width: 20px;
            height: 20px;
            pointer-events: none;
        }

        .sheet-notch::before {
            left: -20px;
            background: radial-gradient(circle at 0 100%, transparent 20px, var(--bg-canvas) 20.5px);
        }

        .sheet-notch::after {
            right: -20px;
            background: radial-gradient(circle at 100% 100%, transparent 20px, var(--bg-canvas) 20.5px);
        }

        @media (max-width: 880px) {
            .sheet-notch {
                position: static;
                transform: none;
                border-radius: 999px;
                padding: 6px;
                margin-bottom: 16px;
                display: flex;
                justify-content: center;
                flex-wrap: wrap;
            }
            .sheet-notch::before, .sheet-notch::after { display: none; }
        }

        .notch-btn {
            border: none;
            background: #1a202b;
            color: #d8dee9;
            font-family: inherit;
            font-size: 0.75rem;
            font-weight: 500;
            padding: 7px 14px;
            border-radius: 999px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 7px;
            transition: all 0.18s ease;
        }

        .notch-btn .nbadge {
            background: #2a3242;
            color: #cbd5e1;
            font-size: 0.68rem;
            padding: 1px 7px;
            border-radius: 999px;
            font-weight: 700;
        }

        .notch-btn.active {
            background: var(--lime);
            color: #0b0f14;
            font-weight: 700;
        }

        .notch-btn.active .nbadge {
            background: #0b0f14;
            color: var(--lime);
        }

        .sheet-top-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
            min-height: 36px;
            gap: 12px;
        }

        .sheet-heading {
            font-size: 0.98rem;
            font-weight: 700;
            color: var(--ink-dark);
        }

        .sheet-subheading {
            font-size: 0.76rem;
            color: var(--ink-gray);
            margin-top: 2px;
        }

        .sheet-tools {
            display: flex;
            gap: 8px;
        }

        .sheet-tool-btn {
            width: 34px;
            height: 34px;
            border-radius: 50%;
            border: 1px solid #e2e8f0;
            background: #f8fafc;
            color: #334155;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.82rem;
            text-decoration: none;
        }

        .sheet-tool-btn:hover { background: #e2e8f0; }

        .split-grid {
            display: grid;
            grid-template-columns: 0.76fr 1.24fr;
            gap: 22px;
            align-items: stretch;
        }

        @media (max-width: 980px) {
            .split-grid { grid-template-columns: 1fr; }
        }

        /* Left Model List inside White Sheet */
        .model-list {
            display: flex;
            flex-direction: column;
            gap: 6px;
            max-height: 440px;
            overflow-y: auto;
            padding-right: 4px;
        }

        .model-list::-webkit-scrollbar { width: 5px; }
        .model-list::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 999px; }

        .model-row {
            display: grid;
            grid-template-columns: 38px 1fr auto auto;
            align-items: center;
            gap: 12px;
            padding: 11px 16px;
            border-radius: 999px;
            cursor: pointer;
            transition: all 0.16s ease;
            color: var(--ink-dark);
        }

        .model-row:hover { background: #f1f5f9; }

        .model-row.selected {
            background: #141923;
            color: #ffffff;
            box-shadow: 0 12px 26px rgba(15, 20, 28, 0.24);
        }

        .row-avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 0.78rem;
            color: #0f141c;
            flex-shrink: 0;
        }

        .model-row.selected .row-avatar {
            box-shadow: 0 0 0 2px var(--lime);
        }

        .row-title {
            font-size: 0.83rem;
            font-weight: 700;
            line-height: 1.25;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .row-sub {
            font-size: 0.73rem;
            color: var(--ink-gray);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .model-row.selected .row-sub { color: #94a3b8; }

        .row-tag {
            background: #f1f5f9;
            color: #475569;
            font-size: 0.7rem;
            font-weight: 600;
            padding: 5px 12px;
            border-radius: 999px;
            white-space: nowrap;
        }

        .model-row.selected .row-tag {
            background: #ffffff;
            color: #0f141c;
            font-weight: 700;
        }

        .row-metric {
            font-size: 0.92rem;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
            text-align: right;
            min-width: 76px;
        }

        .row-metric .unit-sm {
            color: #94a3b8;
            font-size: 0.7rem;
            font-weight: 400;
            margin-left: 2px;
        }

        /* Right Dark Inset Detail Panel */
        .detail-dark-card {
            background: #151a23;
            color: var(--text-light);
            border-radius: 26px;
            padding: 24px 26px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 16px;
            min-height: 425px;
        }

        .detail-header {
            display: grid;
            grid-template-columns: 1.15fr 0.95fr 1.1fr;
            gap: 16px;
            align-items: start;
        }

        @media (max-width: 680px) {
            .detail-header { grid-template-columns: 1fr; }
        }

        .d-caption {
            color: var(--text-muted);
            font-size: 0.73rem;
            margin-bottom: 6px;
        }

        .d-id-row {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }

        .d-id {
            font-size: 1.42rem;
            font-weight: 500;
            letter-spacing: -0.02em;
            color: #ffffff;
        }

        .d-id .hash {
            color: var(--text-muted);
            font-size: 1.05rem;
            margin-right: 2px;
        }

        .d-company {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 1.18rem;
            font-weight: 600;
            color: #ffffff;
        }

        .d-customer {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .d-cust-name {
            font-size: 0.86rem;
            font-weight: 700;
            color: #ffffff;
        }

        .d-cust-sub {
            font-size: 0.73rem;
            color: var(--text-muted);
        }

        /* Plain-Spanish Beginner Summary Box */
        .simple-explain-box {
            background: rgba(163, 255, 18, 0.08);
            border: 1px solid rgba(163, 255, 18, 0.24);
            border-radius: 16px;
            padding: 12px 16px;
            font-size: 0.82rem;
            line-height: 1.48;
            color: #e6edf7;
        }

        .simple-explain-box strong {
            color: var(--lime);
        }

        /* 4 Sub-cards inside Dark Detail Panel */
        .subcards-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
        }

        @media (max-width: 780px) {
            .subcards-grid { grid-template-columns: repeat(2, 1fr); }
        }

        .sub-box {
            background: #202633;
            border-radius: 18px;
            padding: 15px;
            min-height: 112px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            position: relative;
            border: 1px solid rgba(255, 255, 255, 0.04);
        }

        .sub-box .mini-arrow {
            position: absolute;
            top: 13px;
            right: 13px;
            color: var(--text-muted);
            font-size: 0.78rem;
        }

        .sub-box .sub-val {
            font-size: 1.04rem;
            font-weight: 600;
            color: #ffffff;
            font-variant-numeric: tabular-nums;
            padding-right: 16px;
        }

        .sub-box .sub-lbl {
            font-size: 0.72rem;
            color: var(--text-muted);
            line-height: 1.35;
        }

        .sub-box.dotted-add {
            background: radial-gradient(rgba(255,255,255,0.08) 1px, transparent 1px);
            background-size: 10px 10px;
            background-color: rgba(255, 255, 255, 0.015);
            border: 1.5px dashed rgba(255, 255, 255, 0.16);
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.18s ease;
        }

        .sub-box.dotted-add:hover {
            border-color: var(--lime);
            background-color: rgba(163, 255, 18, 0.06);
        }

        .plus-circle {
            width: 34px;
            height: 34px;
            border-radius: 50%;
            background: #262e3d;
            color: #ffffff;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 1rem;
            margin-bottom: 6px;
        }

        /* Bottom Summary Bar inside Dark Detail Panel */
        .detail-footer-bar {
            background: #202633;
            border-radius: 18px;
            padding: 13px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            flex-wrap: wrap;
        }

        .footer-stats {
            display: flex;
            align-items: center;
            gap: 24px;
            flex-wrap: wrap;
        }

        .f-stat-lbl {
            font-size: 0.69rem;
            color: var(--text-muted);
            margin-bottom: 2px;
        }

        .f-stat-val {
            font-size: 0.98rem;
            font-weight: 600;
            color: #ffffff;
            font-variant-numeric: tabular-nums;
        }

        .footer-ctas {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .btn-lime {
            background: var(--lime);
            color: #0b0f14;
            border: none;
            font-family: inherit;
            font-size: 0.8rem;
            font-weight: 700;
            padding: 11px 20px;
            border-radius: 999px;
            cursor: pointer;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }

        .btn-lime:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 20px rgba(163, 255, 18, 0.28);
        }

        /* Secondary Views inside Detail Card */
        .panel-view { display: none; }
        .panel-view.active { display: flex; flex-direction: column; justify-content: space-between; height: 100%; gap: 16px; }

        .chart-wrap {
            height: 245px;
            background: #1b212d;
            border-radius: 18px;
            padding: 14px;
        }

        .feed-scroll {
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 270px;
            overflow-y: auto;
        }

        .feed-card {
            background: #202633;
            border-radius: 14px;
            padding: 11px 14px;
            font-size: 0.81rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }

        .feed-card a {
            color: var(--lime);
            text-decoration: none;
            font-weight: 600;
        }

        .feed-card a:hover { text-decoration: underline; }

        .icon-circle svg,
        .sheet-tool-btn svg,
        .search-icon-btn svg,
        .btn-dark-pill svg {
            width: 15px;
            height: 15px;
            stroke: currentColor;
            stroke-width: 1.8;
            fill: none;
            stroke-linecap: round;
            stroke-linejoin: round;
            flex-shrink: 0;
        }

        /* ───────────── RESPONSIVE / MOBILE FIRST-FRIENDLY ───────────── */
        html { -webkit-text-size-adjust: 100%; }

        @media (max-width: 1080px) {
            .shell { padding: 16px 18px 32px; }
        }

        /* Tablets / small laptops: keep the top navigation on one thumb-scrollable row */
        @media (max-width: 860px) {
            .shell { overflow-x: clip; }
            .topbar { gap: 10px 14px; }
            .hero-title { font-size: 1.5rem; }
            .hero-subtitle { font-size: 0.78rem; }
            .nav-center {
                order: 3;
                flex-basis: 100%;
                width: 100%;
                flex-wrap: nowrap;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                scrollbar-width: none;
                padding-bottom: 2px;
            }
            .nav-center::-webkit-scrollbar { display: none; }
            .white-nav-pill { flex-wrap: nowrap; width: max-content; }
            .nav-tab { white-space: nowrap; }
            .topbar-right { margin-left: auto; }
        }

        /* Phones: stack filters full-width, single-row scrollable goal chips, roomier taps */
        @media (max-width: 720px) {
            .shell { padding: 14px 14px 28px; }
            .hero-row { margin-bottom: 12px; }
            .goals-strip {
                flex-wrap: nowrap;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                scrollbar-width: none;
                margin-left: -2px;
                margin-right: -2px;
                padding-bottom: 4px;
            }
            .goals-strip::-webkit-scrollbar { display: none; }
            .goals-label { flex: 0 0 auto; }
            .goal-chip { flex: 0 0 auto; white-space: nowrap; }

            .filters-bar { gap: 8px; }
            .filters-title { width: 100%; margin-right: 0; }
            .filter-pill,
            .search-pill-wrap { flex: 1 1 100%; min-width: 0; width: 100%; }
            .filter-pill,
            .search-pill-wrap input { font-size: 16px; } /* evita zoom automático en iOS */
            .search-pill-wrap { order: -1; }

            .dark-card { padding: 20px 18px; min-height: 0; }
            .wallet-strip { flex-direction: column; align-items: stretch; gap: 12px; }
            .btn-white-pill { width: 100%; justify-content: center; }
            .white-sheet { padding: 20px 15px 22px; border-radius: 24px; }

            .model-row { grid-template-columns: 34px 1fr auto auto; gap: 10px; }
            .row-metric { min-width: 62px; font-size: 0.85rem; }

            .subcards-grid { grid-template-columns: repeat(2, 1fr); }
            .detail-dark-card { padding: 18px 15px; min-height: 0; }
            .detail-header { grid-template-columns: 1fr; gap: 12px; }
            .panel-view.active { gap: 12px; }
            .sheet-top-row { flex-wrap: wrap; }
        }

        @media (max-width: 420px) {
            .hero-title { font-size: 1.3rem; }
            .btn-dark-pill { padding: 8px 12px; font-size: 0.76rem; }
            .metric-big { font-size: 1.6rem; }
            .d-id { font-size: 1.2rem; }
            .white-nav-pill .nav-tab { padding: 8px 12px; font-size: 0.76rem; }
            .sheet-notch { width: 100%; }
            .notch-btn { flex: 1; justify-content: center; font-size: 0.7rem; padding: 7px 8px; }
            .notch-btn .nbadge { display: none; }
        }
    </style>
</head>
<body>
    <div class="shell">
        <!-- 1. TOP NAVIGATION BAR -->
        <nav class="topbar">
            <div class="brand-cluster">
                <a href="/dashboard" class="brand-logo">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round">
                        <path d="M12 2v20M2 12h20M4.93 4.93l14.14 14.14M19.07 4.93L4.93 19.07"/>
                    </svg>
                    <span>ranking.ia</span>
                </a>
                <button class="icon-circle" onclick="toggleGuia()" title="Guía rápida para principiantes">?</button>
            </div>

            <div class="nav-center">
                <div class="white-nav-pill" id="topNavPills">
                    <button class="nav-tab active" data-panel="detail" onclick="activarNavSuperior(this, 'detail')">Mejores IAs</button>
                    <button class="nav-tab" data-panel="value" onclick="activarNavSuperior(this, 'value')">IAs Gratis ($0)</button>
                    <button class="nav-tab" data-panel="chart" onclick="activarNavSuperior(this, 'chart')">Gráfico de Evolución</button>
                    <button class="nav-tab" data-panel="trends" onclick="activarNavSuperior(this, 'trends')">Novedades</button>
                    <button class="nav-tab" data-panel="changes" onclick="activarNavSuperior(this, 'changes')">Últimos Cambios</button>
                </div>

                <a class="icon-circle" href="/feed.xml" target="_blank" rel="noopener" title="Suscripción RSS gratuita">
                    <svg viewBox="0 0 24 24"><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></svg>
                </a>
                <a class="icon-circle" href="/api/rankings" target="_blank" rel="noopener" title="Datos abiertos JSON">
                    <svg viewBox="0 0 24 24"><path d="M8 4H6a2 2 0 0 0-2 2v4a2 2 0 0 1-2 2 2 2 0 0 1 2 2v4a2 2 0 0 0 2 2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v4a2 2 0 0 0 2 2 2 2 0 0 0-2 2v4a2 2 0 0 1-2 2h-2"/></svg>
                </a>
            </div>

            <div class="topbar-right">
                <button class="icon-circle" onclick="cambiarVistaPanel('changes')" title="Ver movimientos recientes">
                    <svg viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
                    <span class="icon-dot" id="bellDot" style="display:none;"></span>
                </button>
                <a class="icon-circle" href="/health" target="_blank" rel="noopener" title="Estado del monitor 24/7">
                    <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.93 4.93l2.12 2.12M16.95 16.95l2.12 2.12M2 12h3M19 12h3M4.93 19.07l2.12-2.12M16.95 7.05l2.12-2.12"/></svg>
                </a>
                <div class="profile-avatar" title="Monitor Automático Activo">IA</div>
            </div>
        </nav>

        <!-- 2. HERO ROW -->
        <section class="hero-row">
            <div class="hero-left">
                <button class="icon-circle" onclick="resetearFiltros()" title="Volver al inicio">
                    <svg viewBox="0 0 24 24"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>
                </button>
                <div>
                    <h1 class="hero-title" id="heroTitle">¿Cuál es la mejor IA hoy?</h1>
                    <div class="hero-subtitle">Guía fácil actualizada cada hora según miles de pruebas reales a ciegas</div>
                </div>
            </div>
            <div class="hero-actions">
                <button class="btn-dark-pill" onclick="toggleGuia()">
                    <span>💡</span> <span>¿Cómo elegir?</span>
                </button>
                <button class="btn-dark-pill" onclick="cargarDatos()">
                    <svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
                    <span id="ultimaActualizacion">Actualizar</span>
                </button>
            </div>
        </section>

        <!-- COLLAPSIBLE 3-STEP BEGINNER GUIDE -->
        <section class="guide-banner" id="guideBanner">
            <div class="guide-step">
                <strong>1. Elige para qué la quieres</strong>
                <p>Pulsa uno de los botones rápidos de abajo (escribir, programar, crear webs, imágenes o usar gratis) sin complicarte con términos técnicos.</p>
            </div>
            <div class="guide-step">
                <strong>2. Fíjate en las medallas 🥇 🥈 🥉</strong>
                <p>Los puntos salen de miles de personas comparando respuestas sin saber qué empresa las escribió. <b>Más puntos = mejor calidad real.</b></p>
            </div>
            <div class="guide-step">
                <strong>3. Toca una IA y pruébala</strong>
                <p>Al tocar cualquier fila blanca verás a la derecha un resumen en español sencillo y el botón verde <b>Probar esta IA ↗</b> para abrirla.</p>
            </div>
        </section>

        <!-- 1-CLICK BEGINNER GOALS -->
        <section class="goals-strip" aria-label="Atajos rápidos por tarea">
            <span class="goals-label">¿Qué quieres hacer hoy?</span>
            <button class="goal-chip active" id="goalGeneral" onclick="elegirObjetivo('general', this)">🏆 Todo uso (Escribir y dudas)</button>
            <button class="goal-chip" id="goalCode" onclick="elegirObjetivo('code', this)">💻 Programar código</button>
            <button class="goal-chip" id="goalWeb" onclick="elegirObjetivo('web', this)">🌐 Crear páginas web</button>
            <button class="goal-chip" id="goalVisual" onclick="elegirObjetivo('visual', this)">🎨 Imágenes y fotos</button>
            <button class="goal-chip" id="goalFree" onclick="elegirObjetivo('free', this)">🆓 Usar Gratis ($0)</button>
            <button class="goal-chip" id="goalPopular" onclick="elegirObjetivo('popular', this)">🔥 Las más populares</button>
        </section>

        <!-- 3. TOP BENTO CARDS -->
        <section class="bento-top">
            <!-- Left Dark Summary Card -->
            <div class="dark-card">
                <div class="metrics-triple">
                    <div>
                        <div class="metric-label">🥇 #1 en Calidad General Hoy</div>
                        <div class="metric-big"><span id="kpiTopElo">1,539</span><span class="suffix">pts</span></div>
                        <div class="metric-hint" id="kpiTopModelName">Gemini 4 Carbon (High)</div>
                    </div>
                    <div>
                        <div class="metric-label">🔥 Uso Mundial Esta Semana</div>
                        <div class="metric-big"><span class="prefix">~</span><span id="kpiTopTokens">15.80</span><span class="suffix">T usos</span></div>
                        <div class="metric-hint" id="kpiPopularName">Líder en volumen global</div>
                    </div>
                    <div>
                        <div class="metric-label">🔄 Revisión Automática</div>
                        <div class="metric-big"><span id="kpiInterval">60</span><span class="suffix">min</span></div>
                        <div class="metric-hint">Vigilancia continua 24/7</div>
                    </div>
                </div>

                <div class="bars-grid">
                    <div>
                        <div class="bar-col-head"><span>Calidad (Votos)</span><span id="barArenaCount">155 mod</span></div>
                        <div class="bar-track"><div class="bar-fill" id="barArenaFill" style="width: 100%;"></div></div>
                        <div class="avatar-stack" id="avatarsArena"></div>
                    </div>
                    <div>
                        <div class="bar-col-head"><span>Diseño Visual</span><span id="barDesignCount">18 mod</span></div>
                        <div class="bar-track"><div class="bar-fill" id="barDesignFill" style="width: 44%;"></div></div>
                        <div class="avatar-stack" id="avatarsDesign"></div>
                    </div>
                    <div>
                        <div class="bar-col-head"><span>Más Usadas</span><span id="barOrCount">10 mod</span></div>
                        <div class="bar-track"><div class="bar-fill" id="barOrFill" style="width: 28%;"></div></div>
                        <div class="avatar-stack" id="avatarsOr"></div>
                    </div>
                </div>
            </div>

            <!-- Right Dark Spotlight Card -->
            <div class="dark-card">
                <button class="corner-arrow" onclick="cambiarVistaPanel('chart')" title="Ver gráfico de evolución">↗</button>
                <div>
                    <div class="metric-label">Inteligencias Artificiales Comparadas</div>
                    <div class="spotlight-row">
                        <div class="metric-big"><span class="prefix">+</span><span id="modelosCount">183</span><span class="suffix">modelos</span></div>
                        <span class="pill-subtle" id="fuentesCount">6 fuentes al día</span>
                    </div>
                </div>

                <div class="wallet-strip">
                    <div class="wallet-cards">
                        <div class="w-card" id="wCardDesign" onclick="seleccionarFuente('design')">
                            <div class="w-top" id="wDesignTop">#1 · 1387 pts</div>
                            <div class="w-sub">Diseño &amp; Webs</div>
                        </div>
                        <div class="w-card featured" id="wCardArena" onclick="seleccionarFuente('arena')">
                            <div class="w-top" id="wArenaTop">#1 · 1539 pts</div>
                            <div class="w-sub">Mejor Calidad</div>
                        </div>
                        <div class="w-card" id="wCardOr" onclick="seleccionarFuente('openrouter')">
                            <div class="w-top" id="wOrTop">#1 · 15.8T</div>
                            <div class="w-sub">Más Usadas</div>
                        </div>
                    </div>
                    <button class="btn-white-pill" onclick="cambiarVistaPanel('value')">Ver Gratis $0</button>
                </div>
            </div>
        </section>

        <!-- 4. ACTIVE FILTERS ROW -->
        <section class="filters-bar">
            <div class="filters-title">
                <span>Filtros fáciles</span>
                <span class="count-dot" id="activeFilterCount">2</span>
            </div>

            <select id="selSource" class="filter-pill" aria-label="Tipo de ranking">
                <option value="arena">Ranking: Mejor Calidad (Votos humanos)</option>
                <option value="openrouter">Ranking: Más Usadas del Mundo</option>
                <option value="design">Ranking: Diseño de Páginas y Apps</option>
            </select>

            <select id="selCategory" class="filter-pill" aria-label="Tarea">
                <option value="Text">Tarea: Escribir, resumir y pensar (General)</option>
            </select>

            <select id="selProvider" class="filter-pill" onchange="aplicarFiltros()" aria-label="Empresa creadora">
                <option value="">Empresa: Todas las marcas</option>
                <option value="claude">Anthropic (Claude)</option>
                <option value="gemini">Google (Gemini)</option>
                <option value="gpt">OpenAI (ChatGPT / GPT)</option>
                <option value="muse">Meta (Muse / Llama)</option>
                <option value="deepseek">DeepSeek</option>
                <option value="qwen">Alibaba (Qwen)</option>
                <option value="kimi">Moonshot (Kimi)</option>
                <option value="grok">xAI (Grok)</option>
                <option value="mimo">Xiaomi (MiMo)</option>
            </select>

            <select id="selLimit" class="filter-pill" onchange="aplicarFiltros()" aria-label="Cantidad a mostrar">
                <option value="all">Mostrar: Lista completa</option>
                <option value="top5">Mostrar: Solo el Top 5</option>
                <option value="free">Mostrar: Solo Gratis ($0)</option>
            </select>

            <div class="search-pill-wrap">
                <input id="modelSearch" type="search" placeholder="Buscar IA (ej. Claude Opus 5.5, Gemini, GPT)..." oninput="aplicarFiltros()">
                <span class="search-icon-btn">
                    <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
                </span>
            </div>
        </section>

        <!-- 5. WHITE MASTER-DETAIL BOTTOM SHEET -->
        <section class="white-sheet">
            <!-- Top Center Dark Inverted Notch -->
            <div class="sheet-notch">
                <button class="notch-btn" id="notchAll" onclick="cambiarNotch('all')">Ver Todas</button>
                <button class="notch-btn" id="notchFree" onclick="cambiarNotch('top5')">Top Recomendadas <span class="nbadge">5</span></button>
                <button class="notch-btn active" id="notchActive" onclick="cambiarNotch('source')">
                    <span id="notchActiveLabel">General</span>
                    <span class="nbadge" id="notchActiveCount">15</span>
                </button>
            </div>

            <div class="sheet-top-row">
                <div>
                    <div class="sheet-heading" id="listHeading">Mejores IAs para Todo Uso (General)</div>
                    <div class="sheet-subheading" id="listSubheading">Haz clic en cualquier IA de la lista para ver su explicación sencilla a la derecha</div>
                </div>
                <div class="sheet-tools">
                    <button class="sheet-tool-btn" onclick="cambiarVistaPanel('detail')" title="Ver ficha explicada">
                        <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 15h6"/></svg>
                    </button>
                    <button class="sheet-tool-btn" onclick="cambiarVistaPanel('chart')" title="Ver gráfico comparativo">
                        <svg viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
                    </button>
                </div>
            </div>

            <div class="split-grid">
                <!-- Left Column: Selectable Model List -->
                <div class="model-list" id="modelListContainer">
                    <div style="padding:24px;color:#64748b;font-size:0.85rem;">Cargando las mejores IAs de hoy...</div>
                </div>

                <!-- Right Column: Deep Dark Inset Detail & Analytics Card -->
                <div class="detail-dark-card">
                    <!-- VIEW A: SIMPLE MODEL DETAILS (Default) -->
                    <div class="panel-view active" id="panel-detail">
                        <div class="detail-header">
                            <div>
                                <div class="d-caption">Posición en esta tarea</div>
                                <div class="d-id-row">
                                    <div class="d-id"><span class="hash">#</span><span id="detRankCode">1 del Mundo</span></div>
                                    <span class="pill-subtle" id="detStatusPill">Muy recomendada</span>
                                </div>
                            </div>
                            <div>
                                <div class="d-caption">Empresa creadora</div>
                                <div class="d-company">
                                    <span id="detCompany">Google</span>
                                    <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" style="color:var(--lime);"><path d="M12 2L19 9L12 16L5 9L12 2Z"/></svg>
                                </div>
                            </div>
                            <div>
                                <div class="d-caption">IA Seleccionada</div>
                                <div class="d-customer">
                                    <div class="row-avatar" id="detAvatar" style="background:#bfdbfe;">GO</div>
                                    <div>
                                        <div class="d-cust-name" id="detModelName">Gemini 4 Carbon (High)</div>
                                        <div class="d-cust-sub" id="detModelSub">Ideal para escribir, pensar y resolver dudas</div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Plain-Spanish Explanation Banner -->
                        <div class="simple-explain-box" id="simpleExplainBox">
                            <strong>💡 En palabras simples:</strong> Selecciona cualquier modelo de la izquierda para entender por qué destaca y si te conviene usarlo.
                        </div>

                        <div class="subcards-grid">
                            <div class="sub-box">
                                <span class="mini-arrow">↗</span>
                                <div class="sub-val" id="subVal1">1,539 pts</div>
                                <div class="sub-lbl" id="subLbl1">Nota de calidad (más alto = mejor)</div>
                            </div>
                            <div class="sub-box">
                                <span class="mini-arrow">↗</span>
                                <div class="sub-val" id="subVal2">4,483 votos</div>
                                <div class="sub-lbl" id="subLbl2">Personas reales que la probaron</div>
                            </div>
                            <div class="sub-box">
                                <span class="mini-arrow">↗</span>
                                <div class="sub-val" id="subVal3">Web / API</div>
                                <div class="sub-lbl" id="subLbl3">Acceso y coste estimado</div>
                            </div>
                            <div class="sub-box dotted-add" onclick="cambiarVistaPanel('chart')" title="Comparar evolución en gráfico">
                                <div class="plus-circle">+</div>
                                <div class="sub-lbl" style="text-align:center;">Ver en Gráfico</div>
                            </div>
                        </div>

                        <div class="detail-footer-bar">
                            <div class="footer-stats">
                                <div>
                                    <div class="f-stat-lbl">Nivel de Calidad</div>
                                    <div class="f-stat-val" id="footBestRank">Sobresaliente</div>
                                </div>
                                <div>
                                    <div class="f-stat-lbl">Puntuación</div>
                                    <div class="f-stat-val" id="footScore">1,539 pts</div>
                                </div>
                                <div>
                                    <div class="f-stat-lbl">Revisiones</div>
                                    <div class="f-stat-val" id="snapshotId">—</div>
                                </div>
                            </div>
                            <div class="footer-ctas">
                                <button class="icon-circle" onclick="cambiarVistaPanel('value')" title="Ver alternativas Gratis ($0)">
                                    <svg viewBox="0 0 24 24"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                                </button>
                                <a class="btn-lime" id="btnTryModel" href="https://gemini.google.com" target="_blank" rel="noopener">
                                    <span>Probar esta IA</span> <span>↗</span>
                                </a>
                            </div>
                        </div>
                    </div>

                    <!-- VIEW B: INTERACTIVE ELO CHART -->
                    <div class="panel-view" id="panel-chart">
                        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                            <div>
                                <div class="d-caption">Comparativa Visual (Últimos 30 días)</div>
                                <div style="font-size:1.15rem;font-weight:600;">¿Qué IA está subiendo más rápido?</div>
                            </div>
                            <button class="btn-dark-pill" onclick="cambiarVistaPanel('detail')">← Volver a la explicación</button>
                        </div>
                        <div class="chart-wrap">
                            <canvas id="chartElo"></canvas>
                            <div id="eloEmpty" style="display:none;color:var(--text-muted);text-align:center;padding-top:85px;font-size:0.84rem;">
                                Guardando historial horario para dibujar la evolución de esta tarea…
                            </div>
                        </div>
                        <div class="detail-footer-bar">
                            <div class="f-stat-lbl">Cuanto más arriba esté la línea de una IA, mejores resultados da frente a sus rivales.</div>
                            <a class="btn-lime" id="chartDownload" href="/api/chart.svg?source=arena&category=Text" target="_blank" rel="noopener">Descargar Imagen</a>
                        </div>
                    </div>

                    <!-- VIEW C: FREE MODELS & VALUE RADAR -->
                    <div class="panel-view" id="panel-value">
                        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                            <div>
                                <div class="d-caption">Ahorra el 100 % sin perder calidad</div>
                                <div style="font-size:1.15rem;font-weight:600;">Mejores IAs Gratis ($0) y Económicas</div>
                            </div>
                            <button class="btn-dark-pill" onclick="cambiarVistaPanel('detail')">← Volver a la explicación</button>
                        </div>
                        <div class="feed-scroll" id="valueData"></div>
                        <div class="detail-footer-bar">
                            <div class="f-stat-lbl">Todas las opciones marcadas con 🆓 se pueden usar gratis en OpenRouter.</div>
                            <a class="btn-lime" href="https://openrouter.ai/models?max_price=0" target="_blank" rel="noopener">Usar IAs Gratis ↗</a>
                        </div>
                    </div>

                    <!-- VIEW D: GITHUB / HF / HN TRENDS -->
                    <div class="panel-view" id="panel-trends">
                        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                            <div>
                                <div class="d-caption">Lo más comentado hoy en el mundo tecnológico</div>
                                <div style="font-size:1.15rem;font-weight:600;">Proyectos Nuevos y Noticias de IA</div>
                            </div>
                            <button class="btn-dark-pill" onclick="cambiarVistaPanel('detail')">← Volver a la explicación</button>
                        </div>
                        <div class="feed-scroll" id="trendingData"></div>
                        <div class="detail-footer-bar">
                            <div class="f-stat-lbl">Haz clic en cualquier titular verde para abrir la noticia o herramienta original.</div>
                            <button class="btn-lime" onclick="cambiarVistaPanel('detail')">Ver Rankings</button>
                        </div>
                    </div>

                    <!-- VIEW E: DETECTED CHANGES LOG -->
                    <div class="panel-view" id="panel-changes">
                        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                            <div>
                                <div class="d-caption">Vigilancia automática cada 60 minutos</div>
                                <div style="font-size:1.15rem;font-weight:600;">Últimos Movimientos (<span id="cambiosCount">0</span>)</div>
                            </div>
                            <button class="btn-dark-pill" onclick="cambiarVistaPanel('detail')">← Volver a la explicación</button>
                        </div>
                        <div class="feed-scroll" id="cambiosLista"></div>
                        <div class="detail-footer-bar">
                            <div class="f-stat-lbl">El bot revisa cada hora y avisa por Telegram únicamente cuando cambia algún puesto importante.</div>
                            <a class="btn-lime" href="/feed.xml" target="_blank" rel="noopener">Suscribirse RSS</a>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    </div>

    <script>
        if (window.Telegram && window.Telegram.WebApp) {
            try { window.Telegram.WebApp.ready(); window.Telegram.WebApp.expand(); } catch (e) {}
        }

        let chartElo = null;
        let currentSources = {};
        let visibleModels = [];
        let selectedIdx = 0;
        let currentPanel = 'detail';

        const CATEGORY_LABELS = {
            'Text': {
                select: 'Tarea: Escribir, resumir y pensar (General)',
                short: 'Chat y Textos',
                heading: 'Mejores IAs para Todo Uso (Escribir, estudiar y resolver dudas)',
                use: 'Ideal para redactar, resumir textos, estudiar y responder cualquier duda'
            },
            'Code': {
                select: 'Tarea: Programar y escribir código',
                short: 'Programación',
                heading: 'Mejores IAs para Programación y Código',
                use: 'Especialista en escribir programas, corregir errores y lógica técnica'
            },
            'WebDev': {
                select: 'Tarea: Crear páginas web y aplicaciones',
                short: 'Crear Webs',
                heading: 'Mejores IAs para Crear Páginas Web y Apps Completas',
                use: 'Diseña y programa páginas web modernas e interactivas listas para usar'
            },
            'Vision': {
                select: 'Tarea: Ver y analizar fotos o capturas',
                short: 'Analizar Fotos',
                heading: 'Mejores IAs para Entender Fotos, Gráficos y Capturas',
                use: 'Capaz de mirar fotografías, documentos escaneados o pantallas y explicarlos'
            },
            'Agent': {
                select: 'Tarea: Agentes que resuelven tareas solos',
                short: 'Agente Autónomo',
                heading: 'Mejores Agentes IA (Trabajan paso a paso por ti)',
                use: 'Ejecuta tareas largas de varios pasos de forma autónoma'
            },
            'Document': {
                select: 'Tarea: Leer PDFs y documentos largos',
                short: 'Documentos PDF',
                heading: 'Mejores IAs para Leer Documentos y Contratos Largos',
                use: 'Analiza informes extensos y extrae respuestas precisas'
            },
            'Search': {
                select: 'Tarea: Buscar información actual en internet',
                short: 'Búsqueda Web',
                heading: 'Mejores IAs para Buscar Datos Actuales en Internet',
                use: 'Consulta internet con fuentes reales para responder sobre actualidad'
            },
            'Text-to-Image': {
                select: 'Tarea: Crear imágenes desde cero',
                short: 'Crear Imagen',
                heading: 'Mejores IAs para Generar Imágenes e Ilustraciones',
                use: 'Convierte cualquier descripción escrita en una imagen o ilustración'
            },
            'Image Edit': {
                select: 'Tarea: Editar y retocar fotos',
                short: 'Editar Foto',
                heading: 'Mejores IAs para Retocar y Modificar Imágenes',
                use: 'Modifica detalles de una foto siguiendo instrucciones sencillas'
            },
            'Image-to-WebDev': {
                select: 'Tarea: Convertir un dibujo en página web',
                short: 'Foto a Web',
                heading: 'Mejores IAs para Pasar de Captura a Página Web',
                use: 'Transforma una imagen o boceto en una página web funcional'
            },
            'Text-to-Video': {
                select: 'Tarea: Crear vídeos a partir de texto',
                short: 'Crear Vídeo',
                heading: 'Mejores IAs para Generar Vídeos desde Texto',
                use: 'Genera clips de vídeo a partir de una idea escrita'
            },
            'Image-to-Video': {
                select: 'Tarea: Dar movimiento a una foto (Vídeo)',
                short: 'Foto a Vídeo',
                heading: 'Mejores IAs para Animar Fotos en Vídeo',
                use: 'Convierte una imagen fija en un vídeo en movimiento'
            },
            'Video Edit': {
                select: 'Tarea: Edición inteligente de vídeo',
                short: 'Editar Vídeo',
                heading: 'Mejores IAs para Editar Vídeo',
                use: 'Edita y transforma vídeos automáticamente'
            }
        };

        const LAB_META = [
            { match: 'claude', name: 'Anthropic (Claude)', code: 'AN', bg: '#fed7aa', url: 'https://claude.ai' },
            { match: 'gemini', name: 'Google (Gemini)', code: 'GO', bg: '#bfdbfe', url: 'https://gemini.google.com' },
            { match: 'gemma', name: 'Google (Gemma)', code: 'GO', bg: '#bfdbfe', url: 'https://aistudio.google.com' },
            { match: 'gpt', name: 'OpenAI (ChatGPT)', code: 'OA', bg: '#bbf7d0', url: 'https://chatgpt.com' },
            { match: 'o1', name: 'OpenAI (ChatGPT)', code: 'OA', bg: '#bbf7d0', url: 'https://chatgpt.com' },
            { match: 'o3', name: 'OpenAI (ChatGPT)', code: 'OA', bg: '#bbf7d0', url: 'https://chatgpt.com' },
            { match: 'deepseek', name: 'DeepSeek', code: 'DS', bg: '#c7d2fe', url: 'https://chat.deepseek.com' },
            { match: 'kimi', name: 'Moonshot (Kimi)', code: 'KM', bg: '#f5d0fe', url: 'https://www.kimi.com' },
            { match: 'qwen', name: 'Alibaba (Qwen)', code: 'QW', bg: '#ddd6fe', url: 'https://chat.qwen.ai' },
            { match: 'grok', name: 'xAI (Grok)', code: 'XA', bg: '#e2e8f0', url: 'https://grok.com' },
            { match: 'muse', name: 'Meta AI (Muse)', code: 'MT', bg: '#fde68a', url: 'https://www.meta.ai' },
            { match: 'llama', name: 'Meta AI (Llama)', code: 'MT', bg: '#bae6fd', url: 'https://www.meta.ai' },
            { match: 'glm', name: 'Zhipu AI (GLM)', code: 'ZP', bg: '#fecdd3', url: 'https://chat.z.ai' },
            { match: 'mimo', name: 'Xiaomi (MiMo)', code: 'XM', bg: '#fed7aa', url: 'https://openrouter.ai' },
            { match: 'minimax', name: 'MiniMax', code: 'MM', bg: '#fbcfe8', url: 'https://chat.minimax.io' },
            { match: 'nemotron', name: 'NVIDIA', code: 'NV', bg: '#d9f99d', url: 'https://build.nvidia.com' },
            { match: 'ernie', name: 'Baidu (Ernie)', code: 'BD', bg: '#fed7aa', url: 'https://yiyan.baidu.com' },
            { match: 'wan', name: 'Alibaba (Wan)', code: 'WN', bg: '#ddd6fe', url: 'https://wan.video' },
            { match: 'flux', name: 'Black Forest (Flux)', code: 'BF', bg: '#e2e8f0', url: 'https://bfl.ai' }
        ];

        function inferLab(modelName, author) {
            const hay = ((modelName || '') + ' ' + (author || '')).toLowerCase();
            for (const item of LAB_META) {
                if (hay.includes(item.match)) return item;
            }
            const clean = String(author || modelName || 'IA').replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
            return {
                name: author ? author.charAt(0).toUpperCase() + author.slice(1) : 'Laboratorio IA',
                code: (clean.substring(0, 2) || 'IA'),
                bg: '#e2e8f0',
                url: 'https://openrouter.ai/models'
            };
        }

        function catInfo(cat) {
            return CATEGORY_LABELS[cat] || {
                select: `Tarea: ${cat || 'General'}`,
                short: cat || 'General',
                heading: `Clasificación: ${cat || 'General'}`,
                use: 'Evaluada en pruebas comparativas reales'
            };
        }

        function fmtCompact(n) {
            if (typeof n !== 'number') return '';
            if (n >= 1e12) return (n / 1e12).toFixed(2) + 'T';
            if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
            if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
            if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
            return String(n);
        }

        function fmtIntComma(n) {
            if (typeof n !== 'number') return '—';
            return n.toLocaleString('en-US');
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text == null ? '' : String(text);
            return div.innerHTML;
        }

        function toggleGuia() {
            document.getElementById('guideBanner').classList.toggle('open');
        }

        function activarNavSuperior(btn, panelName) {
            document.querySelectorAll('#topNavPills .nav-tab').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');
            const titles = {
                detail: '¿Cuál es la mejor IA hoy?',
                value: 'IAs Gratis ($0) y Económicas',
                chart: 'Evolución de Calidad',
                trends: 'Novedades del Día en IA',
                changes: 'Últimos Cambios en el Ranking'
            };
            document.getElementById('heroTitle').textContent = titles[panelName] || '¿Cuál es la mejor IA hoy?';
            cambiarVistaPanel(panelName);
        }

        function cambiarVistaPanel(panelName) {
            currentPanel = panelName;
            document.querySelectorAll('.panel-view').forEach(p => p.classList.remove('active'));
            const target = document.getElementById('panel-' + panelName);
            if (target) target.classList.add('active');
            if (panelName === 'chart') cargarElo();
        }

        function elegirObjetivo(goal, btn) {
            document.querySelectorAll('.goal-chip').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');

            const selSrc = document.getElementById('selSource');
            const selLim = document.getElementById('selLimit');
            document.getElementById('selProvider').value = '';
            document.getElementById('modelSearch').value = '';
            selLim.value = 'all';

            if (goal === 'free') {
                cambiarVistaPanel('value');
                document.querySelectorAll('#topNavPills .nav-tab').forEach(b => {
                    b.classList.toggle('active', b.dataset.panel === 'value');
                });
                return;
            }

            cambiarVistaPanel('detail');
            document.querySelectorAll('#topNavPills .nav-tab').forEach(b => {
                b.classList.toggle('active', b.dataset.panel === 'detail');
            });

            if (goal === 'popular') {
                selSrc.value = 'openrouter';
                actualizarTarjetasWallet('openrouter');
                sincronizarCategoriasSelect();
            } else {
                selSrc.value = 'arena';
                actualizarTarjetasWallet('arena');
                sincronizarCategoriasSelect();
                const catSel = document.getElementById('selCategory');
                const desired = {
                    general: 'Text',
                    code: 'Code',
                    web: 'WebDev',
                    visual: 'Vision'
                }[goal] || 'Text';
                const opts = [...catSel.options].map(o => o.value);
                if (opts.includes(desired)) catSel.value = desired;
                else if (goal === 'code' && opts.includes('WebDev')) catSel.value = 'WebDev';
                else if (goal === 'visual' && opts.includes('Text-to-Image')) catSel.value = 'Text-to-Image';
            }
            selectedIdx = 0;
            aplicarFiltros();
        }

        function seleccionarFuente(src) {
            const sel = document.getElementById('selSource');
            sel.value = src;
            actualizarTarjetasWallet(src);
            sincronizarCategoriasSelect();
            selectedIdx = 0;
            cambiarVistaPanel('detail');
            aplicarFiltros();
            cargarElo();
        }

        function actualizarTarjetasWallet(activeSrc) {
            const map = { design: 'wCardDesign', arena: 'wCardArena', openrouter: 'wCardOr' };
            Object.entries(map).forEach(([k, id]) => {
                const el = document.getElementById(id);
                if (el) el.classList.toggle('featured', k === activeSrc);
            });
        }

        function cambiarNotch(mode) {
            document.querySelectorAll('.notch-btn').forEach(b => b.classList.remove('active'));
            if (mode === 'all') {
                document.getElementById('notchAll').classList.add('active');
                document.getElementById('selLimit').value = 'all';
                document.getElementById('selProvider').value = '';
            } else if (mode === 'top5') {
                document.getElementById('notchFree').classList.add('active');
                document.getElementById('selLimit').value = 'top5';
            } else {
                document.getElementById('notchActive').classList.add('active');
                document.getElementById('selLimit').value = 'all';
                document.getElementById('selProvider').value = '';
                document.getElementById('modelSearch').value = '';
            }
            selectedIdx = 0;
            aplicarFiltros();
        }

        function resetearFiltros() {
            elegirObjetivo('general', document.getElementById('goalGeneral'));
        }

        function renderAvatarCluster(containerId, models) {
            const el = document.getElementById(containerId);
            if (!el) return;
            const seen = new Set();
            const chips = [];
            for (const m of (models || [])) {
                const lab = inferLab(m.model, m.author);
                if (seen.has(lab.code)) continue;
                seen.add(lab.code);
                chips.push(`<span class="lab-chip" style="background:${lab.bg};" title="${escapeHtml(lab.name)}">${escapeHtml(lab.code)}</span>`);
                if (chips.length >= 5) break;
            }
            el.innerHTML = chips.join('');
        }

        async function cargarDatos() {
            await Promise.all([cargarRankings(), cargarHistorial(), cargarCambios(), cargarValue(), cargarSalud()]);
            if (currentPanel === 'chart') cargarElo();
        }

        async function cargarSalud() {
            const res = await fetch('/health').catch(() => null);
            if (!res || !res.ok) return;
            const h = await res.json();
            if (h.update_interval_minutes) {
                document.getElementById('kpiInterval').textContent = h.update_interval_minutes;
            }
        }

        async function cargarRankings() {
            const res = await fetch('/api/rankings').catch(() => null);
            if (!res || !res.ok) {
                document.getElementById('ultimaActualizacion').textContent = 'Sin datos';
                document.getElementById('modelListContainer').innerHTML = '<div style="padding:20px;color:#ef4444;">Todavía no hay datos cargados.</div>';
                return;
            }
            const data = await res.json();
            const rawDate = (data.data && data.data.generated_at) || data.created_at;
            const ts = new Date(rawDate);
            document.getElementById('ultimaActualizacion').textContent =
                isNaN(ts.getTime()) ? 'Al día' : 'Al día · ' + ts.toLocaleDateString();

            const snap = data.data || {};
            const fuentes = (snap.sources && typeof snap.sources === 'object') ? snap.sources : snap;
            currentSources = fuentes;

            let okCount = 0, totalModels = 0;
            for (const val of Object.values(fuentes)) {
                if (val && (val.status === 'ok' || val.status === 'stale')) okCount++;
                if (val && Array.isArray(val.models)) totalModels += val.models.length;
            }
            document.getElementById('fuentesCount').textContent = `${okCount} fuentes al día`;
            document.getElementById('modelosCount').textContent = totalModels;

            const arenaModels = (fuentes.arena && fuentes.arena.models) || [];
            const designModels = (fuentes.design && fuentes.design.models) || [];
            const orModels = (fuentes.openrouter && fuentes.openrouter.models) || [];

            const arenaTextTop = arenaModels.find(m => (m.category === 'Text' || !m.category) && m.rank === 1) || arenaModels[0];
            if (arenaTextTop && arenaTextTop.score) {
                document.getElementById('kpiTopElo').textContent = fmtIntComma(arenaTextTop.score);
                document.getElementById('kpiTopModelName').textContent = arenaTextTop.model;
                document.getElementById('wArenaTop').textContent = `#1 · ${arenaTextTop.score} pts`;
            }
            if (designModels[0] && designModels[0].score) {
                document.getElementById('wDesignTop').textContent = `#1 · ${designModels[0].score} pts`;
            }
            if (orModels[0] && orModels[0].tokens_week) {
                const t = (orModels[0].tokens_week / 1e12).toFixed(2);
                document.getElementById('kpiTopTokens').textContent = t;
                document.getElementById('kpiPopularName').textContent = orModels[0].model;
                document.getElementById('wOrTop').textContent = `#1 · ${fmtCompact(orModels[0].tokens_week)}`;
            }

            document.getElementById('barArenaCount').textContent = `${arenaModels.length} IAs`;
            document.getElementById('barDesignCount').textContent = `${designModels.length} IAs`;
            document.getElementById('barOrCount').textContent = `${orModels.length} IAs`;

            renderAvatarCluster('avatarsArena', arenaModels.filter(m => m.category === 'Text' || !m.category));
            renderAvatarCluster('avatarsDesign', designModels);
            renderAvatarCluster('avatarsOr', orModels);

            sincronizarCategoriasSelect();
            aplicarFiltros();
            renderTendencias(fuentes);
        }

        function sincronizarCategoriasSelect() {
            const catSel = document.getElementById('selCategory');
            const source = document.getElementById('selSource').value;
            if (source !== 'arena') {
                const label = source === 'openrouter' ? 'Uso mundial real' : 'Diseño de interfaces y webs';
                catSel.innerHTML = `<option value="">Tarea: ${label}</option>`;
                catSel.disabled = true;
                return;
            }
            catSel.disabled = false;
            const arenaModels = (currentSources.arena && currentSources.arena.models) || [];
            const rawCats = [...new Set(arenaModels.map(m => m.category).filter(Boolean))];
            const priority = ['Text', 'Code', 'WebDev', 'Vision', 'Agent', 'Document', 'Search', 'Text-to-Image', 'Image Edit', 'Text-to-Video', 'Image-to-Video', 'Video Edit', 'Image-to-WebDev'];
            const cats = [
                ...priority.filter(c => rawCats.includes(c)),
                ...rawCats.filter(c => !priority.includes(c))
            ];
            if (!cats.length) {
                catSel.innerHTML = '<option value="">Tarea: General</option>';
                return;
            }
            const prev = catSel.value || (cats.includes('Text') ? 'Text' : cats[0]);
            catSel.innerHTML = cats.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(catInfo(c).select)}</option>`).join('');
            catSel.value = cats.includes(prev) ? prev : (cats.includes('Text') ? 'Text' : cats[0]);
        }

        function aplicarFiltros() {
            const source = document.getElementById('selSource').value;
            const cat = document.getElementById('selCategory').value;
            const prov = document.getElementById('selProvider').value.toLowerCase();
            const limMode = document.getElementById('selLimit').value;
            const q = (document.getElementById('modelSearch').value || '').trim().toLowerCase();

            let fCount = 1;
            if (source === 'arena' && cat) fCount++;
            if (prov) fCount++;
            if (limMode !== 'all') fCount++;
            if (q) fCount++;
            document.getElementById('activeFilterCount').textContent = fCount;

            // If user types a search query, search across ALL categories of the selected source if needed
            const payload = currentSources[source] || {};
            let rows = Array.isArray(payload.models) ? payload.models.slice() : [];

            if (source === 'arena' && cat && !q) {
                rows = rows.filter(m => m.category === cat);
            } else if (source === 'arena' && q) {
                // Keep matching models deduplicated by model+category, prioritizing Text/Code/WebDev
                const matchingCurrentCat = rows.filter(m => m.category === cat && ((m.model || '') + ' ' + (m.author || '')).toLowerCase().includes(q));
                if (matchingCurrentCat.length > 0) {
                    rows = matchingCurrentCat;
                } else {
                    rows = rows.filter(m => ((m.model || '') + ' ' + (m.author || '') + ' ' + (m.category || '')).toLowerCase().includes(q));
                }
            }

            if (prov) {
                rows = rows.filter(m => ((m.model || '') + ' ' + (m.author || '')).toLowerCase().includes(prov));
            }
            if (limMode === 'top5') {
                rows = rows.slice(0, 5);
            } else if (limMode === 'free') {
                rows = rows.filter(m => m.is_free || String(m.model).toLowerCase().includes('free'));
            }
            if (q && source !== 'arena') {
                rows = rows.filter(m => ((m.model || '') + ' ' + (m.author || '')).toLowerCase().includes(q));
            }

            visibleModels = rows.map(m => ({ ...m, _source: source }));

            if (source === 'arena' && cat) {
                const info = catInfo(cat);
                document.getElementById('listHeading').textContent = info.heading;
                document.getElementById('notchActiveLabel').textContent = info.short;
            } else if (source === 'openrouter') {
                document.getElementById('listHeading').textContent = 'IAs Más Usadas del Mundo Esta Semana';
                document.getElementById('notchActiveLabel').textContent = 'Más Usadas';
            } else {
                document.getElementById('listHeading').textContent = 'Mejores IAs para Diseño de Interfaces y Webs';
                document.getElementById('notchActiveLabel').textContent = 'Diseño';
            }
            document.getElementById('notchActiveCount').textContent = visibleModels.length;

            if (selectedIdx >= visibleModels.length) selectedIdx = 0;
            renderListaModelos();
            if (visibleModels.length > 0) {
                renderDetalleModelo(visibleModels[selectedIdx]);
            }
        }

        function renderListaModelos() {
            const box = document.getElementById('modelListContainer');
            if (!visibleModels.length) {
                box.innerHTML = '<div style="padding:26px 14px;color:#64748b;font-size:0.85rem;">No encontramos ninguna IA con ese filtro. Prueba borrar el texto de búsqueda o pulsar "Ver Todas".</div>';
                return;
            }
            const medals = { 1: '🥇 ', 2: '🥈 ', 3: '🥉 ' };
            box.innerHTML = visibleModels.map((m, idx) => {
                const lab = inferLab(m.model, m.author);
                const isSel = idx === selectedIdx;
                let metricHtml = '—';
                if (m.score != null) metricHtml = `${fmtIntComma(m.score)}<span class="unit-sm">pts</span>`;
                else if (m.tokens_week != null) metricHtml = `${fmtCompact(m.tokens_week)}`;
                else if (m.win_rate_pct != null) metricHtml = `${m.win_rate_pct}%`;

                let pill = m.category ? catInfo(m.category).short : (m._source === 'openrouter' ? 'Popular' : 'Diseño');
                if (m.is_free || String(m.model).toLowerCase().includes('(free)')) pill = 'Gratis $0';

                const medal = medals[m.rank] || `#${m.rank} `;
                const rec = m.rank <= 3 ? ' · Top recomendada' : '';
                const subText = `Creada por ${lab.name}${rec}`;

                return `
                    <div class="model-row ${isSel ? 'selected' : ''}" onclick="seleccionarModelo(${idx})">
                        <div class="row-avatar" style="background:${lab.bg};">${escapeHtml(lab.code)}</div>
                        <div style="min-width:0;">
                            <div class="row-title">${medal}${escapeHtml(m.model)}</div>
                            <div class="row-sub">${escapeHtml(subText)}</div>
                        </div>
                        <span class="row-tag">${escapeHtml(pill)}</span>
                        <div class="row-metric">${metricHtml}</div>
                    </div>
                `;
            }).join('');
        }

        function seleccionarModelo(idx) {
            selectedIdx = idx;
            renderListaModelos();
            if (visibleModels[idx]) {
                renderDetalleModelo(visibleModels[idx]);
                cambiarVistaPanel('detail');
            }
        }

        function buscarOtrasPosiciones(modelName, currentCategory) {
            const arenaModels = (currentSources.arena && currentSources.arena.models) || [];
            const base = String(modelName || '').split('(')[0].trim().toLowerCase();
            if (!base) return [];
            const extras = [];
            const seenCats = new Set([currentCategory]);
            for (const item of arenaModels) {
                if (!item.category || seenCats.has(item.category)) continue;
                const itemBase = String(item.model || '').split('(')[0].trim().toLowerCase();
                if (itemBase === base && item.rank <= 5) {
                    seenCats.add(item.category);
                    extras.push(`#${item.rank} en ${catInfo(item.category).short}`);
                }
            }
            return extras.slice(0, 3);
        }

        function renderDetalleModelo(m) {
            const lab = inferLab(m.model, m.author);
            const cInfo = catInfo(m.category);
            const isFree = Boolean(m.is_free || String(m.model).toLowerCase().includes('free'));

            document.getElementById('detRankCode').textContent = `${m.rank} en su lista`;
            document.getElementById('detStatusPill').textContent =
                isFree ? '🆓 Gratis ($0)' : (m.rank <= 3 ? '🏆 Top Podio Mundial' : (m.rank <= 10 ? '⭐ Top 10 Mundial' : 'Verificada'));
            document.getElementById('detCompany').textContent = lab.name;

            const av = document.getElementById('detAvatar');
            av.style.background = lab.bg;
            av.textContent = lab.code;

            document.getElementById('detModelName').textContent = m.model;
            document.getElementById('detModelSub').textContent =
                m._source === 'arena' ? cInfo.use : (m._source === 'openrouter' ? 'Una de las IAs más utilizadas del planeta hoy' : 'Especialista en diseño visual e interfaces web');

            const tryBtn = document.getElementById('btnTryModel');
            tryBtn.href = isFree ? 'https://openrouter.ai/models?max_price=0' : lab.url;

            // Build friendly plain-Spanish explanation
            const otherBadges = m._source === 'arena' ? buscarOtrasPosiciones(m.model, m.category) : [];
            const alsoText = otherBadges.length ? ` Además destaca como <b>${escapeHtml(otherBadges.join(', '))}</b>.` : '';
            let priceSentence = 'Se puede probar desde la web oficial de su creador.';
            if (isFree) {
                priceSentence = '¡Es <b>100 % gratuita ($0)</b> para usar a través de OpenRouter!';
            } else if (m.prompt_per_m != null) {
                priceSentence = m.prompt_per_m <= 0.6
                    ? 'Además es <b>muy económica</b> (cuesta apenas centavos por miles de palabras).'
                    : 'Es un modelo de <b>gama alta</b> pensado para máxima calidad.';
            }

            let rankIntro = `Ocupa el <b>puesto #${m.rank}</b> en <b>${escapeHtml(m.category ? cInfo.short : 'esta clasificación')}</b>`;
            if (m.score != null) rankIntro += ` con <b>${fmtIntComma(m.score)} puntos de calidad</b>`;
            else if (m.win_rate_pct != null) rankIntro += ` ganando el <b>${m.win_rate_pct}%</b> de duelos directos`;
            else if (m.tokens_week != null) rankIntro += ` con un uso masivo esta semana`;

            document.getElementById('simpleExplainBox').innerHTML =
                `<strong>💡 En palabras simples:</strong> ${rankIntro}, creada por <b>${escapeHtml(lab.name)}</b>.${alsoText} ${priceSentence}`;

            // Sub-card 1: Primary Score
            let mainMetric = '—';
            if (m.score != null) mainMetric = `${fmtIntComma(m.score)} pts`;
            else if (m.tokens_week != null) mainMetric = `${fmtCompact(m.tokens_week)} usos`;
            else if (m.win_rate_pct != null) mainMetric = `${m.win_rate_pct}% éxito`;

            document.getElementById('subVal1').textContent = mainMetric;
            document.getElementById('subLbl1').textContent =
                m.score != null ? 'Nota de calidad (más alto = mejor)' : 'Nivel de actividad verificado';

            // Sub-card 2: Human Votes / Confidence
            if (m.votes != null) {
                document.getElementById('subVal2').textContent = `${fmtIntComma(m.votes)} votos`;
                document.getElementById('subLbl2').textContent = 'Personas reales que la evaluaron';
            } else if (m.growth_pct != null) {
                document.getElementById('subVal2').textContent = `${m.growth_pct >= 0 ? '+' : ''}${m.growth_pct}%`;
                document.getElementById('subLbl2').textContent = 'Crecimiento de popularidad semanal';
            } else {
                document.getElementById('subVal2').textContent = `Puesto #${m.rank}`;
                document.getElementById('subLbl2').textContent = 'Posición frente a sus competidores';
            }

            // Sub-card 3: Simple Cost Description
            if (isFree) {
                document.getElementById('subVal3').textContent = 'Gratis ($0)';
                document.getElementById('subLbl3').textContent = 'Uso sin coste disponible hoy';
            } else if (m.prompt_per_m != null) {
                const labelTier = m.prompt_per_m <= 0.6 ? 'Muy barata' : (m.prompt_per_m <= 3 ? 'Precio medio' : 'Premium');
                document.getElementById('subVal3').textContent = labelTier;
                document.getElementById('subLbl3').textContent = `~$${Number(m.prompt_per_m).toFixed(2)} entrada / 1M tokens`;
            } else {
                document.getElementById('subVal3').textContent = 'Web oficial';
                document.getElementById('subLbl3').textContent = `Disponible en ${lab.name}`;
            }

            document.getElementById('footBestRank').textContent =
                m.rank === 1 ? '🥇 Líder #1' : (m.rank <= 3 ? '🏆 Top 3 Élite' : (m.rank <= 10 ? '⭐ Excelente' : 'Muy buena'));
            document.getElementById('footScore').textContent = mainMetric;
        }

        function renderTendencias(fuentes) {
            const gh = (fuentes.github && fuentes.github.ai) ? fuentes.github.ai.slice(0, 5) : [];
            const hf = (fuentes.huggingface && fuentes.huggingface.items) ? fuentes.huggingface.items.slice(0, 4) : [];
            const hn = (fuentes.hackernews && fuentes.hackernews.items) ? fuentes.hackernews.items.slice(0, 5) : [];
            const items = [];

            for (const r of gh) {
                items.push(`<div class="feed-card"><div><a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">🛠️ Proyecto nuevo: ${escapeHtml(r.full_name)}</a><div style="color:var(--text-muted);font-size:0.74rem;margin-top:2px;">${escapeHtml((r.description || '').substring(0, 100))}</div></div><span class="pill-subtle">${r.stars} ★</span></div>`);
            }
            for (const m of hf) {
                items.push(`<div class="feed-card"><div><a href="${escapeHtml(m.url)}" target="_blank" rel="noopener">🤖 Modelo abierto: ${escapeHtml(m.id)}</a></div><span class="pill-subtle">${m.likes} ❤️</span></div>`);
            }
            for (const s of hn) {
                items.push(`<div class="feed-card"><div><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">📰 Noticia: ${escapeHtml(s.title)}</a></div><span class="pill-subtle">${s.points} pts</span></div>`);
            }
            document.getElementById('trendingData').innerHTML = items.join('') || '<div style="color:var(--text-muted);padding:16px;">No hay noticias nuevas en este momento.</div>';
        }

        async function cargarValue() {
            const res = await fetch('/api/value-leaders?limit=10').catch(() => null);
            if (!res || !res.ok) return;
            const data = await res.json();
            const rows = [];
            for (const fm of (data.free_models || [])) {
                const ctx = fm.context_length ? `Memoria larga (${Math.round(fm.context_length / 1000)}K)` : '100 % Gratis';
                rows.push(`<div class="feed-card"><div><strong style="color:var(--lime);">🆓 ${escapeHtml(fm.name || fm.id)}</strong><div style="color:var(--text-muted);font-size:0.74rem;">Coste $0.00 · ${escapeHtml(fm.rank_info || 'Disponible gratis')}</div></div><span class="pill-subtle">${escapeHtml(ctx)}</span></div>`);
            }
            for (const vm of (data.value_models || [])) {
                rows.push(`<div class="feed-card"><div><strong>💎 ${escapeHtml(vm.model)}</strong><div style="color:var(--text-muted);font-size:0.74rem;">Gran calidad (${vm.score} pts) a bajo precio (~$${Number(vm.blended_per_m).toFixed(2)})</div></div><span class="pill-subtle">Calidad/Precio ${vm.value_index}</span></div>`);
            }
            document.getElementById('valueData').innerHTML = rows.join('') || '<div style="color:var(--text-muted);padding:16px;">Actualizando lista de modelos gratuitos…</div>';
        }

        async function cargarHistorial() {
            const res = await fetch('/api/history').catch(() => null);
            const data = (res && res.ok) ? await res.json() : [];
            document.getElementById('snapshotId').textContent = data.length || 0;
        }

        async function cargarElo() {
            const source = document.getElementById('selSource').value;
            const catRaw = document.getElementById('selCategory').value;
            const dl = document.getElementById('chartDownload');
            if (dl) {
                const dp = new URLSearchParams({ source });
                if (source === 'arena' && catRaw) dp.set('category', catRaw);
                dl.href = '/api/chart.svg?' + dp.toString();
            }
            const params = new URLSearchParams({ source, days: '30', limit: '6' });
            if (source === 'arena' && catRaw) params.set('category', catRaw);

            let payload = {};
            try {
                const res = await fetch('/api/elo-history?' + params.toString());
                if (res.ok) payload = await res.json();
            } catch (e) { payload = {}; }
            const series = payload.series || {};

            const canvas = document.getElementById('chartElo');
            if (!canvas) return;
            if (chartElo) { chartElo.destroy(); chartElo = null; }
            const empty = document.getElementById('eloEmpty');
            if (Object.keys(series).length === 0) {
                canvas.style.display = 'none';
                empty.style.display = 'block';
                return;
            }
            canvas.style.display = 'block';
            empty.style.display = 'none';

            const labels = [...new Set(Object.values(series).flat().map(p => p[0]))].sort();
            const palette = ['#a3ff12', '#ffffff', '#38bdf8', '#fbbf24', '#c084fc', '#f87171'];
            const datasets = Object.entries(series).map(([name, points], i) => ({
                label: name,
                data: labels.map(l => { const hit = points.find(p => p[0] === l); return hit ? hit[1] : null; }),
                borderColor: palette[i % palette.length],
                backgroundColor: palette[i % palette.length],
                spanGaps: true,
                tension: 0.3,
                pointRadius: 3.5,
                borderWidth: 2.3
            }));

            chartElo = new Chart(canvas.getContext('2d'), {
                type: 'line',
                data: { labels: labels.map(l => new Date(l).toLocaleDateString()), datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'bottom', labels: { color: '#cbd5e1', boxWidth: 10, font: { size: 10.5 } } }
                    },
                    scales: {
                        x: { ticks: { color: '#8e98aa', maxTicksLimit: 7 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        y: { ticks: { color: '#8e98aa' }, grid: { color: 'rgba(255,255,255,0.05)' } }
                    }
                }
            });
        }

        async function cargarCambios() {
            const res = await fetch('/api/changes').catch(() => null);
            const data = (res && res.ok) ? await res.json() : [];
            document.getElementById('cambiosCount').textContent = data.length;
            document.getElementById('bellDot').style.display = data.length > 0 ? 'block' : 'none';

            if (data.length === 0) {
                document.getElementById('cambiosLista').innerHTML = '<div class="feed-card"><span style="color:var(--text-muted);">Todo estable por ahora. Revisamos automáticamente cada hora y te avisaremos en cuanto cambie algún puesto.</span></div>';
                return;
            }
            document.getElementById('cambiosLista').innerHTML = data.slice(0, 15).map(c => {
                const fecha = new Date(c.detected_at).toLocaleString();
                return `<div class="feed-card"><div><div style="font-size:0.71rem;color:var(--text-muted);">${escapeHtml(fecha)}</div><div>${escapeHtml(String(c.summary || '').substring(0, 260))}</div></div></div>`;
            }).join('');
        }

        document.getElementById('selSource').addEventListener('change', (e) => {
            actualizarTarjetasWallet(e.target.value);
            sincronizarCategoriasSelect();
            selectedIdx = 0;
            aplicarFiltros();
            cargarElo();
        });

        document.getElementById('selCategory').addEventListener('change', () => {
            selectedIdx = 0;
            aplicarFiltros();
            cargarElo();
        });

        cargarDatos();
        setInterval(cargarDatos, 60000);
    </script>
</body>
</html>
"""
