from flask import Blueprint, jsonify, render_template_string, request

from src.db import Database

web = Blueprint('web', __name__)
db = Database()


@web.route('/dashboard')
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@web.route('/api/rankings')
def api_rankings():
    snapshot = db.get_latest_snapshot()
    if snapshot:
        return jsonify(snapshot)
    return jsonify({"error": "Aún no hay datos"}), 404


@web.route('/api/history')
def api_history():
    snapshots = db.get_snapshots(days=30)
    return jsonify(snapshots)


@web.route('/api/changes')
def api_changes():
    changes = db.get_changes(days=30)
    return jsonify(changes)


@web.route('/api/elo-history')
def api_elo_history():
    """Series temporales de puntuación por modelo (snapshots estructurados v3).

    Query params: source (arena|design|openrouter), category (arena), days, limit.
    Los snapshots legados de texto crudo se ignoran automáticamente.
    """
    source = request.args.get('source', 'arena')
    days = min(int(request.args.get('days', 30)), 90)
    limit = min(int(request.args.get('limit', 8)), 20)
    category = request.args.get('category')
    if source == 'arena' and not category:
        category = 'Text'

    snapshots = list(reversed(db.get_snapshots(days=days)))  # cronológico
    series: dict[str, list] = {}
    for snap in snapshots:
        data = snap.get('data') or {}
        src = (data.get('sources') or {}).get(source)
        if not isinstance(src, dict) or src.get('status') not in ('ok', 'stale'):
            continue
        for m in src.get('models') or []:
            if not isinstance(m, dict):
                continue
            if category and (m.get('category') or 'default') != category:
                continue
            value = m.get('score', m.get('tokens_week'))
            if not isinstance(value, int):
                continue
            series.setdefault(m.get('model', '?'), []).append([snap['created_at'], value])

    if not series:
        return jsonify({'source': source, 'series': {}, 'note': 'Sin snapshots estructurados en el rango'})

    # Limita a los N modelos con mayor puntuación final.
    ranked = sorted(series.items(), key=lambda kv: kv[1][-1][1], reverse=True)[:limit]
    return jsonify({
        'source': source,
        'category': category,
        'series': {name: points for name, points in ranked},
    })


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ranking AI - Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        header { display: flex; justify-content: space-between; align-items: center; padding: 20px 0; border-bottom: 1px solid #1e293b; margin-bottom: 30px; flex-wrap: wrap; gap: 10px; }
        header h1 { font-size: 1.5rem; color: #38bdf8; }
        header .subtitle { color: #94a3b8; font-size: 0.85rem; }
        .status-bar { display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 25px; }
        .status-card { background: #1e293b; padding: 15px 20px; border-radius: 10px; flex: 1; min-width: 150px; border: 1px solid #334155; }
        .status-card .label { color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
        .status-card .value { font-size: 1.2rem; font-weight: 600; margin-top: 5px; color: #f1f5f9; }
        .status-card .value.ok { color: #4ade80; }
        .status-card .value.warn { color: #fbbf24; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        .card { background: #1e293b; border-radius: 10px; padding: 20px; border: 1px solid #334155; }
        .card h2 { font-size: 1rem; color: #38bdf8; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #334155; }
        .card pre { background: #0f172a; padding: 12px; border-radius: 6px; font-size: 0.8rem; overflow-x: auto; max-height: 300px; color: #cbd5e1; white-space: pre-wrap; word-break: break-word; }
        .change-item { padding: 10px 0; border-bottom: 1px solid #1e293b; font-size: 0.85rem; line-height: 1.5; }
        .change-item:last-child { border-bottom: none; }
        .change-time { color: #64748b; font-size: 0.75rem; }
        .loading { text-align: center; padding: 40px; color: #64748b; }
        .error { color: #f87171; padding: 20px; text-align: center; }
        .chart-container { height: 250px; }
        .refresh-btn { background: #38bdf8; color: #0f172a; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 0.85rem; }
        .refresh-btn:hover { background: #7dd3fc; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>🏆 Ranking AI Monitor</h1>
                <div class="subtitle">Detección de cambios en rankings de IA - Actualización cada 30 min</div>
            </div>
            <button class="refresh-btn" onclick="cargarDatos()">↻ Actualizar</button>
        </header>

        <div class="status-bar" id="statusBar">
            <div class="status-card"><div class="label">Última actualización</div><div class="value" id="ultimaActualizacion">—</div></div>
            <div class="status-card"><div class="label">Fuentes</div><div class="value" id="fuentesCount">—</div></div>
            <div class="status-card"><div class="label">Cambios detectados</div><div class="value" id="cambiosCount">—</div></div>
            <div class="status-card"><div class="label">Snapshot más reciente</div><div class="value" id="snapshotId">—</div></div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>📊 Últimos datos por fuente</h2>
                <div id="fuentesData"><div class="loading">Cargando...</div></div>
            </div>
            <div class="card">
                <h2>📈 Evolución de puntuaciones (30 días)</h2>
                <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
                    <select id="selSource" class="refresh-btn" style="background:#334155;color:#e2e8f0;">
                        <option value="arena">Arena (Elo)</option>
                        <option value="design">Design Arena (Elo)</option>
                        <option value="openrouter">OpenRouter (tokens/sem)</option>
                    </select>
                    <select id="selCategory" class="refresh-btn" style="background:#334155;color:#e2e8f0;"></select>
                </div>
                <div class="chart-container"><canvas id="chartElo"></canvas></div>
                <div id="eloEmpty" class="loading" style="display:none;">Sin snapshots estructurados todavía — esperando la primera actualización v3…</div>
            </div>
        </div>

        <div class="card">
            <h2>🚨 Cambios detectados</h2>
            <div id="cambiosLista"><div class="loading">Cargando...</div></div>
        </div>
    </div>

    <script>
        let chartElo = null;

        async function cargarDatos() {
            await Promise.all([cargarRankings(), cargarHistorial(), cargarCambios()]);
        }

        async function cargarRankings() {
            const res = await fetch('/api/rankings').catch(() => null);
            if (!res || !res.ok) {
                document.getElementById('ultimaActualizacion').textContent = 'Sin datos';
                document.getElementById('fuentesData').innerHTML = '<div class="error">No hay datos disponibles</div>';
                return;
            }
            const data = await res.json();
            document.getElementById('ultimaActualizacion').textContent = new Date(data.created_at).toLocaleString();

            // v3: data.data = {generated_at, sources:{...}}; v2 legado: data.data = {fuente: texto}
            const snap = data.data || {};
            const fuentes = (snap.sources && typeof snap.sources === 'object') ? snap.sources : snap;
            const fuentesKeys = Object.keys(fuentes);
            document.getElementById('fuentesCount').textContent = fuentesKeys.length;

            let html = '';
            for (const [key, val] of Object.entries(fuentes)) {
                const label = key.replace(/_/g, ' ');
                let body;
                if (typeof val === 'string') {
                    // formato v2 legado: texto crudo de la web
                    body = `<pre>${escapeHtml(val.substring(0, 500))}</pre>`;
                } else if (val && typeof val === 'object') {
                    const status = val.status || '?';
                    const badge = status === 'ok' ? '🟢' : (status === 'stale' ? '🟡' : '🔴');
                    const items = val.models || val.items || [];
                    let head = `<div style="color:#94a3b8;font-size:0.78rem;margin-bottom:6px;">${badge} ${escapeHtml(status)} · ${items.length} entradas`;
                    if (val.error) head += ` · ${escapeHtml(String(val.error).substring(0, 120))}`;
                    head += '</div>';
                    const rows = items.slice(0, 10).map(m => {
                        if (m.model) {
                            const metric = (m.score != null) ? `${m.score} elo` : (m.tokens_week != null ? `${m.tokens_week} tokens` : '');
                            const cat = m.category ? `[${m.category}] ` : '';
                            return `${cat}${m.rank ?? '–'}. ${m.model} — ${metric}`;
                        }
                        if (m.full_name) return `⭐ ${m.full_name} — ${m.stars}★`;
                        if (m.title) return `• ${m.title} (${m.points} pts)`;
                        return JSON.stringify(m).substring(0, 120);
                    }).join(String.fromCharCode(10));
                    body = head + `<pre>${escapeHtml(rows)}${items.length > 10 ? '… +' + (items.length - 10) + ' más' : ''}</pre>`;
                } else {
                    body = '<pre>(vacío)</pre>';
                }
                html += `<details open><summary style="cursor:pointer;font-weight:600;margin-bottom:5px;color:#f1f5f9;">🔹 ${label}</summary>${body}</details>`;
            }
            document.getElementById('fuentesData').innerHTML = html || '<div class="loading">Sin datos</div>';
        }

        async function cargarHistorial() {
            const res = await fetch('/api/history');
            const data = await res.json();
            document.getElementById('snapshotId').textContent = data.length + ' snapshots';
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
                const current = catSel.value;
                catSel.innerHTML = cats.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
                if (cats.includes(current)) catSel.value = current;
            } catch (e) { /* sin datos aún */ }
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
                spanGaps: true, tension: 0.25, pointRadius: 2, borderWidth: 2,
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
            const res = await fetch('/api/changes');
            const data = await res.json();
            document.getElementById('cambiosCount').textContent = data.length;

            if (data.length === 0) {
                document.getElementById('cambiosLista').innerHTML = '<div style="color:#64748b;">No se han detectado cambios aún.</div>';
                return;
            }
            let html = '';
            for (const c of data) {
                const fecha = new Date(c.detected_at).toLocaleString();
                html += `<div class="change-item"><div class="change-time">${fecha}</div><div>${escapeHtml(c.summary.substring(0, 300))}</div></div>`;
            }
            document.getElementById('cambiosLista').innerHTML = html;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        document.getElementById('selSource').addEventListener('change', cargarElo);
        document.getElementById('selCategory').addEventListener('change', cargarElo);
        cargarDatos();
        setInterval(cargarDatos, 60000);
    </script>
</body>
</html>
"""
