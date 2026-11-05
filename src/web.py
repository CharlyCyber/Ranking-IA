"""Dashboard Web Público (compatible con Telegram Mini App) + API Abierta Gratuita + RSS."""

from datetime import datetime, timezone
from email.utils import format_datetime

from flask import Blueprint, Response, jsonify, render_template_string, request

from src.db import Database
from src.html_utils import escape_html
from src.insights import compute_value_leaders, fmt_compact_number, render_elo_svg, search_models

web = Blueprint("web", __name__)
db = Database()
# Hidrata SQLite en frío si existe data/rankings.json local
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
        return jsonify({"source": source, "category": category, "series": {}, "note": "Sin snapshots estructurados en el rango"})

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
            "color": "0ea5e9",
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
            f"<guid isPermaLink=\"false\">{guid}</guid>"
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


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ranking AI Monitor · Open Hub</title>
    <link rel="alternate" type="application/rss+xml" title="Ranking AI Cambios RSS" href="/feed.xml">
    <script src="https://telegram.org/js/telegram-web-app.js" defer></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <style>
        :root {
            --bg: #0b1120;
            --surface: #111827;
            --surface-2: #1e293b;
            --border: #26334d;
            --text: #e2e8f0;
            --muted: #94a3b8;
            --accent: #38bdf8;
            --green: #4ade80;
            --amber: #fbbf24;
            --red: #f87171;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; line-height: 1.5; }
        .container { max-width: 1260px; margin: 0 auto; padding: 20px; }
        header { display: flex; justify-content: space-between; align-items: center; padding: 14px 0 22px; border-bottom: 1px solid var(--border); margin-bottom: 24px; flex-wrap: wrap; gap: 12px; }
        header h1 { font-size: 1.45rem; color: var(--accent); display: flex; align-items: center; gap: 8px; }
        .badge-free { font-size: 0.7rem; background: rgba(74, 222, 128, 0.14); color: var(--green); border: 1px solid rgba(74, 222, 128, 0.35); padding: 2px 8px; border-radius: 999px; font-weight: 600; }
        header .subtitle { color: var(--muted); font-size: 0.85rem; margin-top: 2px; }
        .header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .btn { background: var(--accent); color: #082f49; border: none; padding: 8px 14px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 0.83rem; text-decoration: none; display: inline-flex; align-items: center; gap: 5px; }
        .btn:hover { opacity: 0.92; }
        .btn-ghost { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); }
        .status-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 22px; }
        .status-card { background: var(--surface); padding: 14px 18px; border-radius: 12px; border: 1px solid var(--border); }
        .status-card .label { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; }
        .status-card .value { font-size: 1.15rem; font-weight: 700; margin-top: 4px; color: #f8fafc; }
        .search-box { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 22px; }
        .search-row { display: flex; gap: 10px; flex-wrap: wrap; }
        .search-input { flex: 1; min-width: 220px; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 10px 14px; border-radius: 8px; font-size: 0.9rem; outline: none; }
        .search-input:focus { border-color: var(--accent); }
        .search-results { margin-top: 12px; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
        .model-pill { background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-size: 0.82rem; }
        .model-pill strong { color: #f8fafc; display: block; margin-bottom: 4px; }
        .tag { display: inline-block; background: rgba(56, 189, 248, 0.13); color: var(--accent); border-radius: 5px; padding: 1px 6px; font-size: 0.72rem; margin: 2px 4px 2px 0; }
        .tag-free { background: rgba(74, 222, 128, 0.16); color: var(--green); }
        .grid { display: grid; grid-template-columns: 1.15fr 0.85fr; gap: 20px; margin-bottom: 22px; }
        @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
        .card { background: var(--surface); border-radius: 12px; padding: 18px; border: 1px solid var(--border); }
        .card h2 { font-size: 0.98rem; color: var(--accent); margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
        .tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
        .tab-btn { background: var(--surface-2); color: var(--muted); border: 1px solid var(--border); padding: 5px 11px; border-radius: 999px; font-size: 0.76rem; cursor: pointer; font-weight: 600; }
        .tab-btn.active { background: var(--accent); color: #082f49; border-color: var(--accent); }
        table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
        th, td { text-align: left; padding: 8px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); }
        th { color: var(--muted); font-size: 0.73rem; text-transform: uppercase; font-weight: 600; }
        td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
        .rank-num { font-weight: 700; color: var(--accent); width: 34px; }
        .chart-container { height: 270px; }
        select.ctrl { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); padding: 6px 10px; border-radius: 7px; font-size: 0.8rem; }
        .change-item { padding: 10px 0; border-bottom: 1px solid var(--surface-2); font-size: 0.84rem; }
        .change-item:last-child { border-bottom: none; }
        .change-time { color: var(--muted); font-size: 0.74rem; }
        .trend-list { list-style: none; font-size: 0.83rem; }
        .trend-list li { padding: 7px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .trend-list li:last-child { border-bottom: none; }
        .trend-list a { color: #7dd3fc; text-decoration: none; font-weight: 600; }
        .trend-list a:hover { text-decoration: underline; }
        .loading { text-align: center; padding: 28px; color: var(--muted); font-size: 0.86rem; }
        .error { color: var(--red); padding: 18px; text-align: center; font-size: 0.86rem; }
        footer { margin-top: 28px; padding-top: 16px; border-top: 1px solid var(--border); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px; color: var(--muted); font-size: 0.78rem; }
        footer a { color: var(--accent); text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>🏆 Ranking AI Monitor <span class="badge-free">100% FREE &amp; OPEN</span></h1>
                <div class="subtitle">LMSYS Arena · OpenRouter · Design Arena · GitHub AI · Hacker News</div>
            </div>
            <div class="header-actions">
                <a class="btn btn-ghost" href="/feed.xml" target="_blank" rel="noopener">📡 RSS</a>
                <a class="btn btn-ghost" href="/api/rankings" target="_blank" rel="noopener">{ } JSON API</a>
                <button class="btn" onclick="cargarDatos()">↻ Actualizar</button>
            </div>
        </header>

        <div class="status-bar">
            <div class="status-card"><div class="label">Última actualización</div><div class="value" id="ultimaActualizacion">—</div></div>
            <div class="status-card"><div class="label">Fuentes activas</div><div class="value" id="fuentesCount">—</div></div>
            <div class="status-card"><div class="label">Modelos indexados</div><div class="value" id="modelosCount">—</div></div>
            <div class="status-card"><div class="label">Snapshots / Cambios</div><div class="value" id="snapshotId">—</div></div>
        </div>

        <div class="search-box">
            <div class="search-row">
                <input id="modelSearch" class="search-input" type="search" placeholder="🔍 Busca y compara cualquier modelo en todos los rankings (ej. claude, gpt, deepseek, kimi, gemini, qwen)..." oninput="buscarModelos(this.value)">
            </div>
            <div id="searchResults" class="search-results" style="display:none;"></div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>
                    <span>📊 Explorador de Rankings en Vivo</span>
                    <select id="arenaCatFilter" class="ctrl" onchange="renderTablaActiva()" style="display:none;"></select>
                </h2>
                <div class="tabs" id="sourceTabs"></div>
                <div id="fuentesData"><div class="loading">Cargando rankings...</div></div>
            </div>

            <div style="display:flex;flex-direction:column;gap:20px;">
                <div class="card">
                    <h2>📈 Evolución de puntuaciones (30 días)</h2>
                    <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
                        <select id="selSource" class="ctrl">
                            <option value="arena">Arena (Elo)</option>
                            <option value="design">Design Arena (Elo)</option>
                            <option value="openrouter">OpenRouter (tokens/sem)</option>
                        </select>
                        <select id="selCategory" class="ctrl"></select>
                    </div>
                    <div class="chart-container"><canvas id="chartElo"></canvas></div>
                    <div id="eloEmpty" class="loading" style="display:none;">Acumulando snapshots estructurados para trazar la serie temporal…</div>
                </div>

                <div class="card">
                    <h2>🆓 Radar de Modelos Gratis ($0) &amp; Calidad/Precio</h2>
                    <div id="valueData"><div class="loading">Calculando índice...</div></div>
                </div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>🐙 Tendencias Dev &amp; IA Hoy (GitHub / HF / HN)</h2>
                <div id="trendingData"><div class="loading">Cargando tendencias...</div></div>
            </div>
            <div class="card">
                <h2>🚨 Historial de Cambios (<span id="cambiosCount">0</span>)</h2>
                <div id="cambiosLista"><div class="loading">Cargando...</div></div>
            </div>
        </div>

        <footer>
            <div>Datos abiertos actualizados automáticamente · Cero anuncios · Código abierto MIT</div>
            <div>
                <a href="/api/chart.svg?source=arena&category=Text" target="_blank">📈 Gráfico SVG</a> ·
                <a href="/api/badge/arena" target="_blank">🛡️ Badge JSON</a> ·
                <a href="/api/value-leaders" target="_blank">💎 API Value</a>
            </div>
        </footer>
    </div>

    <script>
        if (window.Telegram && window.Telegram.WebApp) {
            try { window.Telegram.WebApp.ready(); window.Telegram.WebApp.expand(); } catch (e) {}
        }

        let chartElo = null;
        let currentSources = {};
        let activeTab = 'arena';
        let searchTimer = null;

        function fmtCompact(n) {
            if (typeof n !== 'number') return '';
            if (n >= 1e12) return (n / 1e12).toFixed(2) + 'T';
            if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
            if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
            if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
            return String(n);
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text == null ? '' : String(text);
            return div.innerHTML;
        }

        async function cargarDatos() {
            await Promise.all([cargarRankings(), cargarHistorial(), cargarCambios(), cargarValue()]);
        }

        async function cargarRankings() {
            const res = await fetch('/api/rankings').catch(() => null);
            if (!res || !res.ok) {
                document.getElementById('ultimaActualizacion').textContent = 'Sin datos';
                document.getElementById('fuentesData').innerHTML = '<div class="error">No hay datos disponibles todavía</div>';
                return;
            }
            const data = await res.json();
            document.getElementById('ultimaActualizacion').textContent = new Date(data.created_at).toLocaleString();

            const snap = data.data || {};
            const fuentes = (snap.sources && typeof snap.sources === 'object') ? snap.sources : snap;
            currentSources = fuentes;

            const modelSources = ['arena', 'openrouter', 'design'].filter(k => fuentes[k]);
            let okCount = 0, totalModels = 0;
            for (const [k, v] of Object.entries(fuentes)) {
                if (v && (v.status === 'ok' || v.status === 'stale')) okCount++;
                if (v && Array.isArray(v.models)) totalModels += v.models.length;
            }
            document.getElementById('fuentesCount').textContent = `${okCount} / ${Object.keys(fuentes).length} OK`;
            document.getElementById('modelosCount').textContent = totalModels;

            const tabsEl = document.getElementById('sourceTabs');
            const labels = { arena: '🏆 LMSYS Arena', openrouter: '⚡ OpenRouter', design: '🎨 Design Arena' };
            if (!fuentes[activeTab] && modelSources.length) activeTab = modelSources[0];
            tabsEl.innerHTML = modelSources.map(k => {
                const st = (fuentes[k] || {}).status;
                const dot = st === 'ok' ? '🟢' : (st === 'stale' ? '🟡' : '🔴');
                return `<button class="tab-btn ${k === activeTab ? 'active' : ''}" onclick="seleccionarTab('${k}')">${dot} ${labels[k] || k}</button>`;
            }).join('');

            actualizarFiltroArena();
            renderTablaActiva();
            renderTendencias(fuentes);
        }

        function seleccionarTab(tab) {
            activeTab = tab;
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            cargarRankings();
        }

        function actualizarFiltroArena() {
            const sel = document.getElementById('arenaCatFilter');
            if (activeTab !== 'arena' || !currentSources.arena || !Array.isArray(currentSources.arena.models)) {
                sel.style.display = 'none';
                return;
            }
            const cats = [...new Set(currentSources.arena.models.map(m => m.category).filter(Boolean))];
            if (!cats.length) { sel.style.display = 'none'; return; }
            sel.style.display = '';
            const prev = sel.value || (cats.includes('Text') ? 'Text' : cats[0]);
            sel.innerHTML = cats.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
            sel.value = cats.includes(prev) ? prev : cats[0];
        }

        function renderTablaActiva() {
            const box = document.getElementById('fuentesData');
            const payload = currentSources[activeTab];
            if (!payload) { box.innerHTML = '<div class="loading">Sin datos</div>'; return; }
            if (typeof payload === 'string') {
                box.innerHTML = `<pre>${escapeHtml(payload.substring(0, 600))}</pre>`;
                return;
            }
            if (payload.status === 'error' && (!payload.models || !payload.models.length)) {
                box.innerHTML = `<div class="error">⚠️ Fuente temporalmente no disponible: ${escapeHtml(payload.error || '')}</div>`;
                return;
            }
            let rows = Array.isArray(payload.models) ? payload.models : [];
            if (activeTab === 'arena') {
                const cat = document.getElementById('arenaCatFilter').value;
                if (cat) rows = rows.filter(m => m.category === cat);
            }
            let html = '<table><thead><tr><th>#</th><th>Modelo</th><th class="num">Métrica</th><th class="num">Extra / Precio</th></tr></thead><tbody>';
            for (const m of rows.slice(0, 18)) {
                let metric = '—';
                if (m.score != null) metric = `<strong>${m.score}</strong> elo`;
                else if (m.tokens_week != null) metric = `<strong>${fmtCompact(m.tokens_week)}</strong> tok`;
                else if (m.win_rate_pct != null) metric = `<strong>${m.win_rate_pct}%</strong>`;

                let extra = '';
                if (m.is_free || String(m.model).toLowerCase().includes('(free)')) extra = '<span class="tag tag-free">FREE $0</span>';
                else if (m.prompt_per_m != null) extra = `$${Number(m.prompt_per_m).toFixed(2)}/M`;
                else if (m.growth_pct != null) extra = `${m.growth_pct >= 0 ? '+' : ''}${m.growth_pct}% sem`;
                else if (m.votes != null) extra = `${fmtCompact(m.votes)} votos`;
                else if (m.win_rate_pct != null && m.score != null) extra = `win ${m.win_rate_pct}%`;

                const author = m.author ? ` <span style="color:var(--muted);font-size:0.75rem;">· ${escapeHtml(m.author)}</span>` : '';
                html += `<tr><td class="rank-num">${escapeHtml(m.rank)}</td><td>${escapeHtml(m.model)}${author}</td><td class="num">${metric}</td><td class="num">${extra}</td></tr>`;
            }
            html += '</tbody></table>';
            box.innerHTML = html;
        }

        function renderTendencias(fuentes) {
            const gh = (fuentes.github && fuentes.github.ai) ? fuentes.github.ai.slice(0, 5) : [];
            const hf = (fuentes.huggingface && fuentes.huggingface.items) ? fuentes.huggingface.items.slice(0, 4) : [];
            const hn = (fuentes.hackernews && fuentes.hackernews.items) ? fuentes.hackernews.items.slice(0, 5) : [];
            let parts = [];

            if (gh.length) {
                parts.push('<div style="font-size:0.78rem;color:var(--muted);text-transform:uppercase;margin-bottom:4px;">Repos IA nuevos en GitHub</div><ul class="trend-list">' +
                    gh.map(r => `<li>⭐ <a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.full_name)}</a> (${r.stars}★)<div style="color:var(--muted);font-size:0.77rem;">${escapeHtml((r.description || '').substring(0, 110))}</div></li>`).join('') +
                    '</ul>');
            }
            if (hf.length) {
                parts.push('<div style="font-size:0.78rem;color:var(--muted);text-transform:uppercase;margin:12px 0 4px;">Hugging Face Open-Weights</div><ul class="trend-list">' +
                    hf.map(m => `<li>🤗 <a href="${escapeHtml(m.url)}" target="_blank" rel="noopener">${escapeHtml(m.id)}</a> (${m.likes}❤️ · ${escapeHtml(m.pipeline_tag || 'model')})</li>`).join('') +
                    '</ul>');
            }
            if (hn.length) {
                parts.push('<div style="font-size:0.78rem;color:var(--muted);text-transform:uppercase;margin:12px 0 4px;">Hacker News</div><ul class="trend-list">' +
                    hn.map(s => `<li>🔥 <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title)}</a> <span style="color:var(--muted);">(${s.points} pts · ${s.comments} 💬)</span></li>`).join('') +
                    '</ul>');
            }
            document.getElementById('trendingData').innerHTML = parts.join('') || '<div class="loading">Sin tendencias en este momento</div>';
        }

        async function cargarValue() {
            const res = await fetch('/api/value-leaders?limit=6').catch(() => null);
            if (!res || !res.ok) return;
            const data = await res.json();
            const free = data.free_models || [];
            const val = data.value_models || [];
            let html = '';
            if (free.length) {
                html += '<div style="margin-bottom:10px;"><div style="font-size:0.76rem;color:var(--muted);margin-bottom:5px;">API 100% GRATUITA ($0)</div>' +
                    free.slice(0, 5).map(m => `<span class="tag tag-free">${escapeHtml(m.name || m.id)}${m.context_length ? ' · ' + Math.round(m.context_length/1000) + 'K' : ''}</span>`).join('') +
                    '</div>';
            }
            if (val.length) {
                html += '<table><thead><tr><th>Modelo (Sweet Spot)</th><th class="num">ELO</th><th class="num">Coste medio</th></tr></thead><tbody>' +
                    val.slice(0, 5).map(v => `<tr><td>${escapeHtml(v.model)}</td><td class="num">${v.score}</td><td class="num">$${Number(v.blended_per_m).toFixed(2)}/M</td></tr>`).join('') +
                    '</tbody></table>';
            }
            document.getElementById('valueData').innerHTML = html || '<div style="color:var(--muted);font-size:0.82rem;">El catálogo de precios gratuitos de OpenRouter se sincroniza automáticamente en cada ciclo.</div>';
        }

        function buscarModelos(q) {
            clearTimeout(searchTimer);
            const box = document.getElementById('searchResults');
            if (!q || q.trim().length < 2) { box.style.display = 'none'; box.innerHTML = ''; return; }
            searchTimer = setTimeout(async () => {
                const res = await fetch('/api/models/search?q=' + encodeURIComponent(q.trim())).catch(() => null);
                if (!res || !res.ok) return;
                const payload = await res.json();
                const items = payload.results || [];
                box.style.display = 'grid';
                if (!items.length) {
                    box.innerHTML = '<div style="color:var(--muted);font-size:0.83rem;">Ningún modelo coincide con esa búsqueda.</div>';
                    return;
                }
                box.innerHTML = items.map(it => {
                    const badges = (it.appearances || []).slice(0, 5).map(a => {
                        const m = a.score != null ? `${a.score}` : (a.tokens_week != null ? fmtCompact(a.tokens_week) : (a.win_rate_pct != null ? a.win_rate_pct + '%' : ''));
                        return `<span class="tag">#${a.rank} ${escapeHtml(a.source)}${m ? ' · ' + escapeHtml(m) : ''}</span>`;
                    }).join('');
                    const freeTag = it.is_free ? '<span class="tag tag-free">FREE $0</span>' : '';
                    return `<div class="model-pill"><strong>${escapeHtml(it.model)} ${freeTag}</strong>${badges}</div>`;
                }).join('');
            }, 180);
        }

        async function cargarHistorial() {
            const res = await fetch('/api/history').catch(() => null);
            const data = (res && res.ok) ? await res.json() : [];
            const cambiosCount = document.getElementById('cambiosCount').textContent || '0';
            document.getElementById('snapshotId').textContent = `${data.length} snaps · ${cambiosCount} alertas`;
            await cargarElo();
        }

        async function refrescarCategorias() {
            const catSel = document.getElementById('selCategory');
            const source = document.getElementById('selSource').value;
            catSel.style.display = source === 'arena' ? '' : 'none';
            if (source !== 'arena') return;
            try {
                const res = await fetch('/api/rankings');
                if (!res.ok) return;
                const payload = await res.json();
                const models = ((((payload.data || {}).sources) || {}).arena || {}).models || [];
                const cats = [...new Set(models.map(m => m.category).filter(Boolean))];
                const current = catSel.value || (cats.includes('Text') ? 'Text' : '');
                catSel.innerHTML = cats.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
                if (cats.includes(current)) catSel.value = current;
            } catch (e) {}
        }

        async function cargarElo() {
            await refrescarCategorias();
            const source = document.getElementById('selSource').value;
            const cat = document.getElementById('selCategory').value;
            const params = new URLSearchParams({ source, days: '30', limit: '8' });
            if (source === 'arena' && cat) params.set('category', cat);

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
                canvas.parentElement.style.display = 'none';
                empty.style.display = '';
                return;
            }
            canvas.parentElement.style.display = '';
            empty.style.display = 'none';

            const labels = [...new Set(Object.values(series).flat().map(p => p[0]))].sort();
            const palette = ['#38bdf8','#4ade80','#fbbf24','#f87171','#c084fc','#22d3ee','#fb923c','#f472b6'];
            const datasets = Object.entries(series).map(([name, points], i) => ({
                label: name,
                data: labels.map(l => { const hit = points.find(p => p[0] === l); return hit ? hit[1] : null; }),
                borderColor: palette[i % palette.length],
                backgroundColor: palette[i % palette.length],
                spanGaps: true, tension: 0.25, pointRadius: 2.5, borderWidth: 2,
            }));

            const ctx = canvas.getContext('2d');
            chartElo = new Chart(ctx, {
                type: 'line',
                data: { labels: labels.map(l => new Date(l).toLocaleDateString()), datasets },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8', boxWidth: 12, font: { size: 10 } } } },
                    scales: {
                        x: { ticks: { color: '#94a3b8', maxTicksLimit: 8 }, grid: { color: '#1e293b' } },
                        y: { ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } }
                    }
                }
            });
        }

        async function cargarCambios() {
            const res = await fetch('/api/changes').catch(() => null);
            const data = (res && res.ok) ? await res.json() : [];
            document.getElementById('cambiosCount').textContent = data.length;

            if (data.length === 0) {
                document.getElementById('cambiosLista').innerHTML = '<div style="color:var(--muted);font-size:0.83rem;">Sin cambios bruscos recientes; monitorizando cada 30 min.</div>';
                return;
            }
            document.getElementById('cambiosLista').innerHTML = data.slice(0, 12).map(c => {
                const fecha = new Date(c.detected_at).toLocaleString();
                return `<div class="change-item"><div class="change-time">${fecha}</div><div>${escapeHtml(String(c.summary || '').substring(0, 300))}</div></div>`;
            }).join('');
        }

        document.getElementById('selSource').addEventListener('change', cargarElo);
        document.getElementById('selCategory').addEventListener('change', cargarElo);
        cargarDatos();
        setInterval(cargarDatos, 60000);
    </script>
</body>
</html>
"""
