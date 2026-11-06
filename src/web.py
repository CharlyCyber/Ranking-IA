"""Dashboard Web Público v4.2 (Diseño Obsidian + Neon Lime + White Split Sheet) + API + RSS."""

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
        '<rss version="2.0"><channel>'
        "<title>Ranking AI Monitor — Alertas de Modelos IA</title>"
        f"<link>{escape_html(base_url)}/dashboard</link>"
        "<description>Cambios verificados en LMSYS Arena, OpenRouter, Design Arena y GitHub</description>"
        "<language>es</language>"
        + "".join(items_xml)
        + "</channel></rss>"
    )
    return Response(rss, mimetype="application/rss+xml; charset=utf-8")


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ranking IA · Monitor</title>
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
            --lime-dim: rgba(163, 255, 18, 0.16);
            --sheet: #ffffff;
            --sheet-muted: #f3f5f8;
            --ink-dark: #0f141c;
            --ink-gray: #717b8a;
            --text-light: #f4f7fb;
            --text-muted: #8a94a6;
            --border-subtle: rgba(255, 255, 255, 0.065);
            --border-light: #ebeff4;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg-canvas);
            color: var(--text-light);
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
            padding-bottom: 36px;
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
            padding: 6px 0 22px;
            flex-wrap: wrap;
        }

        .brand-cluster {
            display: flex;
            align-items: center;
            gap: 14px;
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
        }

        .nav-tab {
            border: none;
            background: transparent;
            color: #262e3b;
            font-family: inherit;
            font-size: 0.78rem;
            font-weight: 500;
            padding: 8px 16px;
            border-radius: 999px;
            cursor: pointer;
            transition: all 0.18s ease;
            white-space: nowrap;
        }

        .nav-tab:hover {
            color: #000;
        }

        .nav-tab.active {
            background: var(--lime);
            color: #0b0f14;
            font-weight: 700;
        }

        .nav-tab.active::before {
            content: "• ";
        }

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
            border-color: rgba(255, 255, 255, 0.15);
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

        /* ───────────── 2. HERO HEADER ROW ───────────── */
        .hero-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 20px;
            gap: 12px;
            flex-wrap: wrap;
        }

        .hero-left {
            display: flex;
            align-items: center;
            gap: 14px;
        }

        .hero-title {
            font-size: 2.15rem;
            font-weight: 400;
            letter-spacing: -0.025em;
            color: #ffffff;
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

        /* ───────────── 3. TOP ASYMMETRIC BENTO CARDS ───────────── */
        .bento-top {
            display: grid;
            grid-template-columns: 1.38fr 1fr;
            gap: 18px;
            margin-bottom: 20px;
        }

        @media (max-width: 1024px) {
            .bento-top { grid-template-columns: 1fr; }
        }

        .dark-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 28px;
            padding: 26px 28px;
            position: relative;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            min-height: 248px;
        }

        .metrics-triple {
            display: grid;
            grid-template-columns: 1fr 1.15fr 0.85fr;
            gap: 18px;
            margin-bottom: 26px;
        }

        @media (max-width: 640px) {
            .metrics-triple { grid-template-columns: 1fr; gap: 14px; }
        }

        .metric-label {
            color: var(--text-muted);
            font-size: 0.78rem;
            font-weight: 400;
            margin-bottom: 8px;
        }

        .metric-big {
            font-size: 2rem;
            font-weight: 400;
            letter-spacing: -0.03em;
            color: #ffffff;
            font-variant-numeric: tabular-nums;
            line-height: 1.1;
        }

        .metric-big .prefix {
            color: var(--text-muted);
            font-size: 1.15rem;
            font-weight: 400;
            margin-right: 3px;
        }

        .metric-big .suffix {
            color: var(--text-muted);
            font-size: 1.1rem;
            font-weight: 300;
            margin-left: 4px;
        }

        /* Progress bars + overlapping lab avatars */
        .bars-grid {
            display: grid;
            grid-template-columns: 1fr 1.25fr 0.9fr;
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
            margin-bottom: 18px;
        }

        .pill-subtle {
            background: #212835;
            color: #cbd5e1;
            font-size: 0.72rem;
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
            border-color: rgba(255, 255, 255, 0.16);
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
            font-size: 0.72rem;
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
            color: rgba(11, 15, 20, 0.78);
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

        .btn-white-pill:hover {
            transform: translateY(-1px);
        }

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
            font-size: 0.83rem;
            color: #d8dee9;
            margin-right: 6px;
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
            min-width: 155px;
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

        .filter-pill:focus {
            border-color: rgba(163, 255, 18, 0.5);
        }

        .search-pill-wrap {
            flex: 1.2;
            min-width: 200px;
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
            font-size: 0.78rem;
            width: 100%;
            outline: none;
        }

        .search-pill-wrap input::placeholder {
            color: #5c6678;
        }

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

        /* Inverted dark center cutout notch */
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

        @media (max-width: 860px) {
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
            margin-bottom: 18px;
            min-height: 36px;
        }

        .sheet-heading {
            font-size: 0.96rem;
            font-weight: 600;
            color: var(--ink-dark);
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

        .sheet-tool-btn:hover {
            background: #e2e8f0;
        }

        .split-grid {
            display: grid;
            grid-template-columns: 0.72fr 1.28fr;
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
            max-height: 410px;
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

        .model-row:hover {
            background: #f1f5f9;
        }

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
            font-size: 0.82rem;
            font-weight: 700;
            line-height: 1.25;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .row-sub {
            font-size: 0.72rem;
            color: var(--ink-gray);
        }

        .model-row.selected .row-sub {
            color: #94a3b8;
        }

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
            font-weight: 500;
            font-variant-numeric: tabular-nums;
            text-align: right;
            min-width: 78px;
        }

        .row-metric .unit-sm {
            color: #94a3b8;
            font-size: 0.72rem;
            margin-right: 2px;
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
            gap: 18px;
            min-height: 396px;
        }

        .detail-header {
            display: grid;
            grid-template-columns: 1.25fr 0.95fr 0.95fr;
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
            font-size: 1.48rem;
            font-weight: 400;
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
            font-size: 1.25rem;
            font-weight: 600;
            color: #ffffff;
        }

        .d-customer {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .d-cust-name {
            font-size: 0.84rem;
            font-weight: 600;
            color: #ffffff;
        }

        .d-cust-sub {
            font-size: 0.71rem;
            color: var(--text-muted);
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
            padding: 16px;
            min-height: 118px;
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
            font-size: 1.08rem;
            font-weight: 500;
            color: #ffffff;
            font-variant-numeric: tabular-nums;
            padding-right: 16px;
        }

        .sub-box .sub-lbl {
            font-size: 0.72rem;
            color: var(--text-muted);
            line-height: 1.3;
        }

        .sub-box.dotted-add {
            background: radial-gradient(rgba(255,255,255,0.08) 1px, transparent 1px);
            background-size: 10px 10px;
            background-color: rgba(255, 255, 255, 0.015);
            border: 1.5px dashed rgba(255, 255, 255, 0.14);
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
            padding: 14px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            flex-wrap: wrap;
        }

        .footer-stats {
            display: flex;
            align-items: center;
            gap: 28px;
            flex-wrap: wrap;
        }

        .f-stat-lbl {
            font-size: 0.69rem;
            color: var(--text-muted);
            margin-bottom: 2px;
        }

        .f-stat-val {
            font-size: 1.02rem;
            font-weight: 500;
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
            font-size: 0.78rem;
            font-weight: 700;
            padding: 11px 20px;
            border-radius: 999px;
            cursor: pointer;
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }

        .btn-lime:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 20px rgba(163, 255, 18, 0.28);
        }

        /* Secondary Views inside Detail Card (ELO Chart / Tendencias / Cambios) */
        .panel-view { display: none; }
        .panel-view.active { display: flex; flex-direction: column; justify-content: space-between; height: 100%; gap: 16px; }

        .chart-wrap {
            height: 235px;
            background: #1b212d;
            border-radius: 18px;
            padding: 14px;
        }

        .feed-scroll {
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 255px;
            overflow-y: auto;
        }

        .feed-card {
            background: #202633;
            border-radius: 14px;
            padding: 11px 14px;
            font-size: 0.8rem;
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
                <button class="icon-circle" onclick="cambiarVistaPanel('detail')" title="Vista principal">
                    <svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>
                </button>
            </div>

            <div class="nav-center">
                <div class="white-nav-pill" id="topNavPills">
                    <button class="nav-tab" data-panel="chart" onclick="activarNavSuperior(this, 'chart')">Evolución</button>
                    <button class="nav-tab active" data-panel="detail" onclick="activarNavSuperior(this, 'detail')">Rankings</button>
                    <button class="nav-tab" data-panel="value" onclick="activarNavSuperior(this, 'value')">Gratis &amp; Precio</button>
                    <button class="nav-tab" data-panel="trends" onclick="activarNavSuperior(this, 'trends')">Tendencias</button>
                    <button class="nav-tab" data-panel="changes" onclick="activarNavSuperior(this, 'changes')">Cambios</button>
                </div>

                <a class="icon-circle" href="/api/rankings" target="_blank" rel="noopener" title="JSON API Abierta">
                    <svg viewBox="0 0 24 24"><path d="M8 4H6a2 2 0 0 0-2 2v4a2 2 0 0 1-2 2 2 2 0 0 1 2 2v4a2 2 0 0 0 2 2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v4a2 2 0 0 0 2 2 2 2 0 0 0-2 2v4a2 2 0 0 1-2 2h-2"/></svg>
                </a>
                <a class="icon-circle" href="/feed.xml" target="_blank" rel="noopener" title="Feed RSS 2.0">
                    <svg viewBox="0 0 24 24"><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></svg>
                </a>
                <a class="icon-circle" href="/api/chart.svg?source=arena&category=Text" target="_blank" rel="noopener" title="Gráfico SVG">
                    <svg viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
                </a>
                <a class="icon-circle" href="/api/badge/arena" target="_blank" rel="noopener" title="Shields Badge">
                    <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                </a>
            </div>

            <div class="topbar-right">
                <button class="icon-circle" onclick="cambiarVistaPanel('changes')" title="Alertas detectadas">
                    <svg viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
                    <span class="icon-dot" id="bellDot" style="display:none;"></span>
                </button>
                <a class="icon-circle" href="/health" target="_blank" rel="noopener" title="Estado 24/7 (/health)">
                    <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.77 1.02 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                </a>
                <div class="profile-avatar" title="Agente IA 24/7 Activo">IA</div>
            </div>
        </nav>

        <!-- 2. HERO ROW -->
        <section class="hero-row">
            <div class="hero-left">
                <button class="icon-circle" onclick="resetearFiltros()" title="Restablecer vista">
                    <svg viewBox="0 0 24 24"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>
                </button>
                <h1 class="hero-title" id="heroTitle">Rankings IA</h1>
            </div>
            <div class="hero-actions">
                <button class="icon-circle" onclick="cambiarVistaPanel('chart')" title="Abrir serie temporal ELO">
                    <svg viewBox="0 0 24 24"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="2" y1="14" x2="6" y2="14"/><line x1="10" y1="8" x2="14" y2="8"/><line x1="18" y1="16" x2="22" y2="16"/></svg>
                </button>
                <button class="btn-dark-pill" onclick="cargarDatos()">
                    <svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="4"/><path d="M12 8v8"/><path d="M8 12h8"/></svg>
                    <span id="ultimaActualizacion">Actualizar ranking</span>
                </button>
            </div>
        </section>

        <!-- 3. TOP BENTO CARDS -->
        <section class="bento-top">
            <!-- Left Dark Summary Card -->
            <div class="dark-card">
                <div class="metrics-triple">
                    <div>
                        <div class="metric-label">Líder ELO Global</div>
                        <div class="metric-big"><span class="prefix">#1</span><span id="kpiTopElo">1,506</span><span class="suffix">elo</span></div>
                    </div>
                    <div>
                        <div class="metric-label">Volumen Semanal Top</div>
                        <div class="metric-big"><span class="prefix">~</span><span id="kpiTopTokens">15.80</span><span class="suffix">T tok</span></div>
                    </div>
                    <div>
                        <div class="metric-label">Ciclo de Revisión</div>
                        <div class="metric-big"><span id="kpiInterval">60</span><span class="suffix">min</span></div>
                    </div>
                </div>

                <div class="bars-grid">
                    <div>
                        <div class="bar-col-head"><span>Arena</span><span id="barArenaCount">120 mod</span></div>
                        <div class="bar-track"><div class="bar-fill" id="barArenaFill" style="width: 100%;"></div></div>
                        <div class="avatar-stack" id="avatarsArena"></div>
                    </div>
                    <div>
                        <div class="bar-col-head"><span>Design Arena</span><span id="barDesignCount">18 mod</span></div>
                        <div class="bar-track"><div class="bar-fill" id="barDesignFill" style="width: 44%;"></div></div>
                        <div class="avatar-stack" id="avatarsDesign"></div>
                    </div>
                    <div>
                        <div class="bar-col-head"><span>OpenRouter</span><span id="barOrCount">10 mod</span></div>
                        <div class="bar-track"><div class="bar-fill" id="barOrFill" style="width: 26%;"></div></div>
                        <div class="avatar-stack" id="avatarsOr"></div>
                    </div>
                </div>
            </div>

            <!-- Right Dark Spotlight Card -->
            <div class="dark-card">
                <button class="corner-arrow" onclick="cambiarVistaPanel('chart')" title="Ver evolución histórica">↗</button>
                <div>
                    <div class="metric-label">Modelos Indexados en Vivo</div>
                    <div class="spotlight-row">
                        <div class="metric-big"><span class="prefix">+</span><span id="modelosCount">148</span><span class="suffix">modelos</span></div>
                        <span class="pill-subtle" id="fuentesCount">5 fuentes activas</span>
                    </div>
                </div>

                <div class="wallet-strip">
                    <div class="wallet-cards">
                        <div class="w-card" id="wCardDesign" onclick="seleccionarFuente('design')">
                            <div class="w-top" id="wDesignTop">#1 · 1387</div>
                            <div class="w-sub">Design UI</div>
                        </div>
                        <div class="w-card featured" id="wCardArena" onclick="seleccionarFuente('arena')">
                            <div class="w-top" id="wArenaTop">#1 · 1506</div>
                            <div class="w-sub">LMSYS Arena</div>
                        </div>
                        <div class="w-card" id="wCardOr" onclick="seleccionarFuente('openrouter')">
                            <div class="w-top" id="wOrTop">#1 · 15.8T</div>
                            <div class="w-sub">OpenRouter</div>
                        </div>
                    </div>
                    <button class="btn-white-pill" onclick="cambiarVistaPanel('chart')">Ver ELO</button>
                </div>
            </div>
        </section>

        <!-- 4. ACTIVE FILTERS ROW -->
        <section class="filters-bar">
            <div class="filters-title">
                <span>Filtros activos</span>
                <span class="count-dot" id="activeFilterCount">2</span>
            </div>

            <select id="selSource" class="filter-pill" aria-label="Fuente">
                <option value="arena">Fuente: LMSYS Arena</option>
                <option value="openrouter">Fuente: OpenRouter</option>
                <option value="design">Fuente: Design Arena</option>
            </select>

            <select id="selCategory" class="filter-pill" aria-label="Categoría">
                <option value="Text">Categoría: Text</option>
            </select>

            <select id="selProvider" class="filter-pill" onchange="aplicarFiltros()" aria-label="Laboratorio">
                <option value="">Todos los laboratorios</option>
                <option value="claude">Anthropic (Claude)</option>
                <option value="gpt">OpenAI (GPT)</option>
                <option value="gemini">Google (Gemini)</option>
                <option value="deepseek">DeepSeek</option>
                <option value="qwen">Alibaba (Qwen)</option>
                <option value="kimi">Moonshot (Kimi)</option>
                <option value="grok">xAI (Grok)</option>
            </select>

            <select id="selLimit" class="filter-pill" onchange="aplicarFiltros()" aria-label="Filtro rápido">
                <option value="all">Vista: Top Completo</option>
                <option value="top5">Vista: Solo Top 5</option>
                <option value="free">Vista: Gratis ($0 API)</option>
            </select>

            <div class="search-pill-wrap">
                <input id="modelSearch" type="search" placeholder="Buscar modelo o autor..." oninput="aplicarFiltros()">
                <span class="search-icon-btn">
                    <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
                </span>
            </div>
        </section>

        <!-- 5. WHITE MASTER-DETAIL BOTTOM SHEET -->
        <section class="white-sheet">
            <!-- Top Center Dark Inverted Notch -->
            <div class="sheet-notch">
                <button class="notch-btn" id="notchAll" onclick="cambiarNotch('all')">Todos</button>
                <button class="notch-btn" id="notchFree" onclick="cambiarNotch('top5')">Top Podio <span class="nbadge">3</span></button>
                <button class="notch-btn active" id="notchActive" onclick="cambiarNotch('source')">
                    <span id="notchActiveLabel">En Ranking</span>
                    <span class="nbadge" id="notchActiveCount">10</span>
                </button>
            </div>

            <div class="sheet-top-row">
                <div class="sheet-heading" id="listHeading">Clasificación Actual</div>
                <div class="sheet-tools">
                    <button class="sheet-tool-btn" onclick="cambiarVistaPanel('detail')" title="Ficha de modelo">
                        <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 15h6"/><path d="M12 12v6"/></svg>
                    </button>
                    <button class="sheet-tool-btn" onclick="cambiarVistaPanel('chart')" title="Gráfico ELO">
                        <svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/></svg>
                    </button>
                </div>
            </div>

            <div class="split-grid">
                <!-- Left Column: Unpaid-Invoices-Style Selectable Model List -->
                <div class="model-list" id="modelListContainer">
                    <div style="padding:24px;color:#64748b;font-size:0.85rem;">Cargando clasificación...</div>
                </div>

                <!-- Right Column: Deep Dark Inset Detail & Analytics Card -->
                <div class="detail-dark-card">
                    <!-- VIEW A: MODEL DETAILS (Default, matches reference screenshot) -->
                    <div class="panel-view active" id="panel-detail">
                        <div class="detail-header">
                            <div>
                                <div class="d-caption">Posición &amp; Código</div>
                                <div class="d-id-row">
                                    <div class="d-id"><span class="hash">#</span><span id="detRankCode">01-TXT</span></div>
                                    <span class="pill-subtle" id="detStatusPill">Verificado</span>
                                </div>
                            </div>
                            <div>
                                <div class="d-caption">Laboratorio</div>
                                <div class="d-company">
                                    <span id="detCompany">Anthropic</span>
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" style="color:#ffffff;opacity:0.9;"><path d="M12 2L19 9L12 16L5 9L12 2Z"/><path d="M12 16L17 21H7L12 16Z" opacity="0.55"/></svg>
                                </div>
                            </div>
                            <div>
                                <div class="d-caption">Modelo Seleccionado</div>
                                <div class="d-customer">
                                    <div class="row-avatar" id="detAvatar" style="background:#fde68a;">CL</div>
                                    <div>
                                        <div class="d-cust-name" id="detModelName">claude-fable-5-high</div>
                                        <div class="d-cust-sub" id="detModelSub">LMSYS Arena · Text</div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="subcards-grid">
                            <div class="sub-box">
                                <span class="mini-arrow">↗</span>
                                <div class="sub-val" id="subVal1">1,506 elo</div>
                                <div class="sub-lbl" id="subLbl1">Puntuación Principal</div>
                            </div>
                            <div class="sub-box">
                                <span class="mini-arrow">↗</span>
                                <div class="sub-val" id="subVal2">#1 Puesto</div>
                                <div class="sub-lbl" id="subLbl2">Categoría Activa</div>
                            </div>
                            <div class="sub-box">
                                <span class="mini-arrow">↗</span>
                                <div class="sub-val" id="subVal3">API Activa</div>
                                <div class="sub-lbl" id="subLbl3">Coste / Crecimiento</div>
                            </div>
                            <div class="sub-box dotted-add" onclick="cambiarVistaPanel('chart')" title="Abrir evolución histórica de ELO">
                                <div class="plus-circle">+</div>
                                <div class="sub-lbl" style="text-align:center;">Ver Serie ELO</div>
                            </div>
                        </div>

                        <div class="detail-footer-bar">
                            <div class="footer-stats">
                                <div>
                                    <div class="f-stat-lbl">Mejor Puesto</div>
                                    <div class="f-stat-val" id="footBestRank">#1</div>
                                </div>
                                <div>
                                    <div class="f-stat-lbl">Métrica Líder</div>
                                    <div class="f-stat-val" id="footScore">1,506 elo</div>
                                </div>
                                <div>
                                    <div class="f-stat-lbl">Snapshots / Alertas</div>
                                    <div class="f-stat-val" id="snapshotId">—</div>
                                </div>
                            </div>
                            <div class="footer-ctas">
                                <button class="icon-circle" onclick="cambiarVistaPanel('trends')" title="Tendencias GitHub/HN">
                                    <svg viewBox="0 0 24 24"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
                                </button>
                                <button class="icon-circle" onclick="cambiarVistaPanel('value')" title="Modelos Gratis ($0)">
                                    <svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                                </button>
                                <button class="btn-lime" onclick="cambiarVistaPanel('chart')">Evolución ELO</button>
                            </div>
                        </div>
                    </div>

                    <!-- VIEW B: INTERACTIVE ELO CHART -->
                    <div class="panel-view" id="panel-chart">
                        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                            <div>
                                <div class="d-caption">Evolución Histórica (30 días)</div>
                                <div style="font-size:1.15rem;font-weight:600;">Curva de Puntuación por Modelo</div>
                            </div>
                            <button class="btn-dark-pill" onclick="cambiarVistaPanel('detail')">← Volver a ficha</button>
                        </div>
                        <div class="chart-wrap">
                            <canvas id="chartElo"></canvas>
                            <div id="eloEmpty" style="display:none;color:var(--text-muted);text-align:center;padding-top:85px;font-size:0.84rem;">
                                Acumulando puntos históricos para esta fuente…
                            </div>
                        </div>
                        <div class="detail-footer-bar">
                            <div class="f-stat-lbl">Usa los selectores de Fuente y Categoría de arriba para cambiar la gráfica en vivo.</div>
                            <a class="btn-lime" href="/api/chart.svg?source=arena&category=Text" target="_blank" rel="noopener" style="text-decoration:none;">Exportar SVG</a>
                        </div>
                    </div>

                    <!-- VIEW C: FREE MODELS & VALUE RADAR -->
                    <div class="panel-view" id="panel-value">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <div>
                                <div class="d-caption">Optimización de Costes IA</div>
                                <div style="font-size:1.15rem;font-weight:600;">Modelos Gratis ($0) &amp; Calidad/Precio</div>
                            </div>
                            <button class="btn-dark-pill" onclick="cambiarVistaPanel('detail')">← Volver a ficha</button>
                        </div>
                        <div class="feed-scroll" id="valueData"></div>
                        <div class="detail-footer-bar">
                            <div class="f-stat-lbl">Sincronizado con el catálogo público gratuito <code>/api/v1/models</code> de OpenRouter.</div>
                            <a class="btn-lime" href="/api/value-leaders" target="_blank" rel="noopener" style="text-decoration:none;">Ver JSON</a>
                        </div>
                    </div>

                    <!-- VIEW D: GITHUB / HF / HN TRENDS -->
                    <div class="panel-view" id="panel-trends">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <div>
                                <div class="d-caption">Radar Diario de Comunidad</div>
                                <div style="font-size:1.15rem;font-weight:600;">GitHub AI · Hugging Face · Hacker News</div>
                            </div>
                            <button class="btn-dark-pill" onclick="cambiarVistaPanel('detail')">← Volver a ficha</button>
                        </div>
                        <div class="feed-scroll" id="trendingData"></div>
                        <div class="detail-footer-bar">
                            <div class="f-stat-lbl">Repositorios nuevos con tracción real y debates técnicos del día.</div>
                            <button class="btn-lime" onclick="cambiarVistaPanel('detail')">Ver Rankings</button>
                        </div>
                    </div>

                    <!-- VIEW E: DETECTED CHANGES LOG -->
                    <div class="panel-view" id="panel-changes">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <div>
                                <div class="d-caption">Auditoría Automática Cada 1 Hora</div>
                                <div style="font-size:1.15rem;font-weight:600;">Cambios Detectados (<span id="cambiosCount">0</span>)</div>
                            </div>
                            <button class="btn-dark-pill" onclick="cambiarVistaPanel('detail')">← Volver a ficha</button>
                        </div>
                        <div class="feed-scroll" id="cambiosLista"></div>
                        <div class="detail-footer-bar">
                            <div class="f-stat-lbl">Suscríbete gratis por RSS o Telegram para recibir alertas al instante.</div>
                            <a class="btn-lime" href="/feed.xml" target="_blank" rel="noopener" style="text-decoration:none;">Abrir RSS</a>
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

        const LAB_META = [
            { match: 'claude', name: 'Anthropic', code: 'AN', bg: '#fed7aa' },
            { match: 'gpt', name: 'OpenAI', code: 'OA', bg: '#bbf7d0' },
            { match: 'o1', name: 'OpenAI', code: 'OA', bg: '#bbf7d0' },
            { match: 'o3', name: 'OpenAI', code: 'OA', bg: '#bbf7d0' },
            { match: 'gemini', name: 'Google DeepMind', code: 'GM', bg: '#bfdbfe' },
            { match: 'deepseek', name: 'DeepSeek', code: 'DS', bg: '#c7d2fe' },
            { match: 'kimi', name: 'Moonshot AI', code: 'KM', bg: '#f5d0fe' },
            { match: 'qwen', name: 'Alibaba Qwen', code: 'QW', bg: '#ddd6fe' },
            { match: 'grok', name: 'xAI', code: 'XA', bg: '#e2e8f0' },
            { match: 'llama', name: 'Meta AI', code: 'MT', bg: '#bae6fd' },
            { match: 'glm', name: 'Zhipu AI', code: 'ZP', bg: '#fecdd3' },
            { match: 'muse', name: 'Muse AI', code: 'MU', bg: '#fde68a' },
            { match: 'minimax', name: 'MiniMax', code: 'MM', bg: '#fbcfe8' },
            { match: 'nemotron', name: 'NVIDIA', code: 'NV', bg: '#d9f99d' },
            { match: 'ernie', name: 'Baidu', code: 'BD', bg: '#fed7aa' },
            { match: 'wan', name: 'Alibaba Wan', code: 'WN', bg: '#ddd6fe' },
            { match: 'flux', name: 'Black Forest', code: 'BF', bg: '#e2e8f0' }
        ];

        function inferLab(modelName, author) {
            const hay = ((modelName || '') + ' ' + (author || '')).toLowerCase();
            for (const item of LAB_META) {
                if (hay.includes(item.match)) return item;
            }
            const clean = String(author || modelName || 'IA').replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
            return {
                name: author ? author.charAt(0).toUpperCase() + author.slice(1) : 'Frontier Lab',
                code: (clean.substring(0, 2) || 'IA'),
                bg: '#e2e8f0'
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

        function activarNavSuperior(btn, panelName) {
            document.querySelectorAll('#topNavPills .nav-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const titles = {
                detail: 'Rankings IA',
                chart: 'Evolución ELO',
                value: 'Gratis & Calidad/Precio',
                trends: 'Tendencias Dev & IA',
                changes: 'Alertas de Cambios'
            };
            document.getElementById('heroTitle').textContent = titles[panelName] || 'Rankings IA';
            cambiarVistaPanel(panelName);
        }

        function cambiarVistaPanel(panelName) {
            document.querySelectorAll('.panel-view').forEach(p => p.classList.remove('active'));
            const target = document.getElementById('panel-' + panelName);
            if (target) target.classList.add('active');
            if (panelName === 'chart') cargarElo();
        }

        function seleccionarFuente(src) {
            const sel = document.getElementById('selSource');
            sel.value = src;
            actualizarTarjetasWallet(src);
            sincronizarCategoriasSelect();
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
            }
            aplicarFiltros();
        }

        function resetearFiltros() {
            document.getElementById('selSource').value = 'arena';
            document.getElementById('selProvider').value = '';
            document.getElementById('selLimit').value = 'all';
            document.getElementById('modelSearch').value = '';
            actualizarTarjetasWallet('arena');
            sincronizarCategoriasSelect();
            cambiarVistaPanel('detail');
            aplicarFiltros();
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
                document.getElementById('modelListContainer').innerHTML = '<div style="padding:20px;color:#ef4444;">No hay datos disponibles todavía.</div>';
                return;
            }
            const data = await res.json();
            const ts = new Date(data.created_at);
            document.getElementById('ultimaActualizacion').textContent =
                isNaN(ts.getTime()) ? 'En vivo' : 'Actualizado ' + ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

            const snap = data.data || {};
            const fuentes = (snap.sources && typeof snap.sources === 'object') ? snap.sources : snap;
            currentSources = fuentes;

            let okCount = 0, totalModels = 0;
            for (const val of Object.values(fuentes)) {
                if (val && (val.status === 'ok' || val.status === 'stale')) okCount++;
                if (val && Array.isArray(val.models)) totalModels += val.models.length;
            }
            document.getElementById('fuentesCount').textContent = `${okCount} fuentes activas`;
            document.getElementById('modelosCount').textContent = totalModels;

            // Populate Top Bento KPIs & Progress Bars
            const arenaModels = (fuentes.arena && fuentes.arena.models) || [];
            const designModels = (fuentes.design && fuentes.design.models) || [];
            const orModels = (fuentes.openrouter && fuentes.openrouter.models) || [];

            const arenaTextTop = arenaModels.find(m => (m.category === 'Text' || !m.category) && m.rank === 1) || arenaModels[0];
            if (arenaTextTop && arenaTextTop.score) {
                document.getElementById('kpiTopElo').textContent = fmtIntComma(arenaTextTop.score);
                document.getElementById('wArenaTop').textContent = `#1 · ${arenaTextTop.score}`;
            }
            if (designModels[0] && designModels[0].score) {
                document.getElementById('wDesignTop').textContent = `#1 · ${designModels[0].score}`;
            }
            if (orModels[0] && orModels[0].tokens_week) {
                const t = (orModels[0].tokens_week / 1e12).toFixed(2);
                document.getElementById('kpiTopTokens').textContent = t;
                document.getElementById('wOrTop').textContent = `#1 · ${fmtCompact(orModels[0].tokens_week)}`;
            }

            document.getElementById('barArenaCount').textContent = `${arenaModels.length} mod`;
            document.getElementById('barDesignCount').textContent = `${designModels.length} mod`;
            document.getElementById('barOrCount').textContent = `${orModels.length} mod`;

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
                catSel.innerHTML = `<option value="">Categoría: Global (${source})</option>`;
                catSel.disabled = true;
                return;
            }
            catSel.disabled = false;
            const arenaModels = (currentSources.arena && currentSources.arena.models) || [];
            const cats = [...new Set(arenaModels.map(m => m.category).filter(Boolean))];
            if (!cats.length) {
                catSel.innerHTML = '<option value="">Categoría: General</option>';
                return;
            }
            const prev = catSel.value || (cats.includes('Text') ? 'Text' : cats[0]);
            catSel.innerHTML = cats.map(c => `<option value="${escapeHtml(c)}">Categoría: ${escapeHtml(c)}</option>`).join('');
            catSel.value = cats.includes(prev) ? prev : (cats.includes('Text') ? 'Text' : cats[0]);
        }

        function aplicarFiltros() {
            const source = document.getElementById('selSource').value;
            const cat = document.getElementById('selCategory').value;
            const prov = document.getElementById('selProvider').value.toLowerCase();
            const limMode = document.getElementById('selLimit').value;
            const q = (document.getElementById('modelSearch').value || '').trim().toLowerCase();

            // Count active filters
            let fCount = 1;
            if (source === 'arena' && cat) fCount++;
            if (prov) fCount++;
            if (limMode !== 'all') fCount++;
            if (q) fCount++;
            document.getElementById('activeFilterCount').textContent = fCount;

            const payload = currentSources[source] || {};
            let rows = Array.isArray(payload.models) ? payload.models.slice() : [];

            if (source === 'arena' && cat) {
                rows = rows.filter(m => m.category === cat);
            }
            if (prov) {
                rows = rows.filter(m => ((m.model || '') + ' ' + (m.author || '')).toLowerCase().includes(prov));
            }
            if (limMode === 'top5') {
                rows = rows.slice(0, 5);
            } else if (limMode === 'free') {
                rows = rows.filter(m => m.is_free || String(m.model).toLowerCase().includes('free'));
            }
            if (q) {
                rows = rows.filter(m => ((m.model || '') + ' ' + (m.author || '') + ' ' + (m.category || '')).toLowerCase().includes(q));
            }

            visibleModels = rows.map(m => ({ ...m, _source: source }));
            const srcNames = { arena: 'LMSYS Arena', openrouter: 'OpenRouter', design: 'Design Arena' };
            const titleSuffix = (source === 'arena' && cat) ? ` · ${cat}` : '';
            document.getElementById('listHeading').textContent = `${srcNames[source] || source}${titleSuffix}`;
            document.getElementById('notchActiveLabel').textContent = source === 'arena' && cat ? cat : (srcNames[source] || source);
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
                box.innerHTML = '<div style="padding:26px 14px;color:#64748b;font-size:0.84rem;">No hay modelos que coincidan con los filtros actuales.</div>';
                return;
            }
            box.innerHTML = visibleModels.map((m, idx) => {
                const lab = inferLab(m.model, m.author);
                const isSel = idx === selectedIdx;
                let metricHtml = '—';
                if (m.score != null) metricHtml = `<span class="unit-sm">elo</span>${fmtIntComma(m.score)}`;
                else if (m.tokens_week != null) metricHtml = `${fmtCompact(m.tokens_week)}`;
                else if (m.win_rate_pct != null) metricHtml = `${m.win_rate_pct}%`;

                let pill = m.category || (m._source === 'openrouter' ? 'Uso Top' : 'UI/Web');
                if (m.is_free || String(m.model).toLowerCase().includes('(free)')) pill = 'FREE $0';

                const subText = m.author ? `Puesto #${m.rank} · ${m.author}` : `Puesto #${m.rank} · ${lab.name}`;
                return `
                    <div class="model-row ${isSel ? 'selected' : ''}" onclick="seleccionarModelo(${idx})">
                        <div class="row-avatar" style="background:${lab.bg};">${escapeHtml(lab.code)}</div>
                        <div style="min-width:0;">
                            <div class="row-title">#${escapeHtml(m.rank)} ${escapeHtml(m.model)}</div>
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

        function renderDetalleModelo(m) {
            const lab = inferLab(m.model, m.author);
            const catCode = (m.category || m._source || 'TOP').substring(0, 3).toUpperCase();
            const rankStr = String(m.rank || 1).padStart(2, '0');

            document.getElementById('detRankCode').textContent = `${rankStr}-${catCode}`;
            document.getElementById('detStatusPill').textContent =
                (m.is_free || String(m.model).toLowerCase().includes('free')) ? 'FREE $0' : 'Activo 1h';
            document.getElementById('detCompany').textContent = lab.name;

            const av = document.getElementById('detAvatar');
            av.style.background = lab.bg;
            av.textContent = lab.code;

            document.getElementById('detModelName').textContent = m.model;
            document.getElementById('detModelSub').textContent =
                `${m._source.toUpperCase()}${m.category ? ' · ' + m.category : ''}`;

            // Sub-card 1: Primary Score / Metric
            let mainMetric = '—';
            if (m.score != null) mainMetric = `${fmtIntComma(m.score)} elo`;
            else if (m.tokens_week != null) mainMetric = `${fmtCompact(m.tokens_week)} tok`;
            else if (m.win_rate_pct != null) mainMetric = `${m.win_rate_pct}%`;

            document.getElementById('subVal1').textContent = mainMetric;
            document.getElementById('subLbl1').textContent =
                m.score != null ? 'Puntuación ELO Verificada' : 'Volumen Semanal';

            // Sub-card 2: Position / Votes / Win-rate
            if (m.votes != null) {
                document.getElementById('subVal2').textContent = `${fmtIntComma(m.votes)}`;
                document.getElementById('subLbl2').textContent = 'Votos Humanos (Arena)';
            } else if (m.growth_pct != null) {
                document.getElementById('subVal2').textContent = `${m.growth_pct >= 0 ? '+' : ''}${m.growth_pct}%`;
                document.getElementById('subLbl2').textContent = 'Crecimiento Semanal';
            } else if (m.win_rate_pct != null) {
                document.getElementById('subVal2').textContent = `${m.win_rate_pct}%`;
                document.getElementById('subLbl2').textContent = 'Tasa de Victoria';
            } else {
                document.getElementById('subVal2').textContent = `Top #${m.rank}`;
                document.getElementById('subLbl2').textContent = `Posición en ${m.category || m._source}`;
            }

            // Sub-card 3: Pricing / Context
            if (m.is_free || String(m.model).toLowerCase().includes('(free)')) {
                document.getElementById('subVal3').textContent = '$0.00 / 1M';
                document.getElementById('subLbl3').textContent = 'API Pública Gratuita';
            } else if (m.prompt_per_m != null) {
                document.getElementById('subVal3').textContent = `$${Number(m.prompt_per_m).toFixed(2)} / $${Number(m.completion_per_m || 0).toFixed(2)}`;
                document.getElementById('subLbl3').textContent = 'Input / Output por 1M tok';
            } else {
                document.getElementById('subVal3').textContent = lab.name;
                document.getElementById('subLbl3').textContent = 'Proveedor Principal';
            }

            document.getElementById('footBestRank').textContent = `#${m.rank}`;
            document.getElementById('footScore').textContent = mainMetric;
        }

        function renderTendencias(fuentes) {
            const gh = (fuentes.github && fuentes.github.ai) ? fuentes.github.ai.slice(0, 5) : [];
            const hf = (fuentes.huggingface && fuentes.huggingface.items) ? fuentes.huggingface.items.slice(0, 4) : [];
            const hn = (fuentes.hackernews && fuentes.hackernews.items) ? fuentes.hackernews.items.slice(0, 5) : [];
            const items = [];

            for (const r of gh) {
                items.push(`<div class="feed-card"><div><a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">⭐ ${escapeHtml(r.full_name)}</a><div style="color:var(--text-muted);font-size:0.73rem;margin-top:2px;">${escapeHtml((r.description || '').substring(0, 95))}</div></div><span class="pill-subtle">${r.stars}★</span></div>`);
            }
            for (const m of hf) {
                items.push(`<div class="feed-card"><div><a href="${escapeHtml(m.url)}" target="_blank" rel="noopener">🤗 ${escapeHtml(m.id)}</a></div><span class="pill-subtle">${m.likes}❤️</span></div>`);
            }
            for (const s of hn) {
                items.push(`<div class="feed-card"><div><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">🔥 ${escapeHtml(s.title)}</a></div><span class="pill-subtle">${s.points} pts</span></div>`);
            }
            document.getElementById('trendingData').innerHTML = items.join('') || '<div style="color:var(--text-muted);padding:16px;">Sin tendencias disponibles ahora mismo.</div>';
        }

        async function cargarValue() {
            const res = await fetch('/api/value-leaders?limit=8').catch(() => null);
            if (!res || !res.ok) return;
            const data = await res.json();
            const rows = [];
            for (const fm of (data.free_models || [])) {
                const ctx = fm.context_length ? `${Math.round(fm.context_length / 1000)}K ctx` : 'API Gratis';
                rows.push(`<div class="feed-card"><div><strong style="color:var(--lime);">🆓 ${escapeHtml(fm.name || fm.id)}</strong><div style="color:var(--text-muted);font-size:0.73rem;">Coste $0.00 · ${escapeHtml(fm.rank_info || 'OpenRouter Free')}</div></div><span class="pill-subtle">${escapeHtml(ctx)}</span></div>`);
            }
            for (const vm of (data.value_models || [])) {
                rows.push(`<div class="feed-card"><div><strong>💎 ${escapeHtml(vm.model)}</strong><div style="color:var(--text-muted);font-size:0.73rem;">${vm.score} elo · ~$${Number(vm.blended_per_m).toFixed(2)} / 1M tok</div></div><span class="pill-subtle">Índice ${vm.value_index}</span></div>`);
            }
            document.getElementById('valueData').innerHTML = rows.join('') || '<div style="color:var(--text-muted);padding:16px;">El catálogo de precios gratuitos de OpenRouter se sincroniza automáticamente en el ciclo horario.</div>';
        }

        async function cargarHistorial() {
            const res = await fetch('/api/history').catch(() => null);
            const data = (res && res.ok) ? await res.json() : [];
            const cambiosCount = document.getElementById('cambiosCount').textContent || '0';
            document.getElementById('snapshotId').textContent = `${data.length} / ${cambiosCount}`;
        }

        async function cargarElo() {
            const source = document.getElementById('selSource').value;
            const catRaw = document.getElementById('selCategory').value;
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
                pointRadius: 3,
                borderWidth: 2.2
            }));

            chartElo = new Chart(canvas.getContext('2d'), {
                type: 'line',
                data: { labels: labels.map(l => new Date(l).toLocaleDateString()), datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'bottom', labels: { color: '#94a3b8', boxWidth: 10, font: { size: 10 } } }
                    },
                    scales: {
                        x: { ticks: { color: '#8a94a6', maxTicksLimit: 7 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        y: { ticks: { color: '#8a94a6' }, grid: { color: 'rgba(255,255,255,0.05)' } }
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
                document.getElementById('cambiosLista').innerHTML = '<div class="feed-card"><span style="color:var(--text-muted);">Sin cambios bruscos recientes; revisando automáticamente cada 1 hora.</span></div>';
                return;
            }
            document.getElementById('cambiosLista').innerHTML = data.slice(0, 15).map(c => {
                const fecha = new Date(c.detected_at).toLocaleString();
                return `<div class="feed-card"><div><div style="font-size:0.7rem;color:var(--text-muted);">${escapeHtml(fecha)}</div><div>${escapeHtml(String(c.summary || '').substring(0, 260))}</div></div></div>`;
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
