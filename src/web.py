from flask import Blueprint, jsonify, render_template_string
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
                <h2>📈 Historial de snapshots (30 días)</h2>
                <div class="chart-container"><canvas id="chartSnapshots"></canvas></div>
            </div>
        </div>

        <div class="card">
            <h2>🚨 Cambios detectados</h2>
            <div id="cambiosLista"><div class="loading">Cargando...</div></div>
        </div>
    </div>

    <script>
        let chartSnapshots = null;

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

            const fuentes = data.data || {};
            const fuentesKeys = Object.keys(fuentes);
            document.getElementById('fuentesCount').textContent = fuentesKeys.length;

            let html = '';
            for (const [key, val] of Object.entries(fuentes)) {
                const label = key.replace(/_/g, ' ');
                const texto = val ? val.substring(0, 500) : '(vacío)';
                html += `<details><summary style="cursor:pointer;font-weight:600;margin-bottom:5px;color:#f1f5f9;">🔹 ${label}</summary><pre>${escapeHtml(texto)}</pre></details>`;
            }
            document.getElementById('fuentesData').innerHTML = html;
        }

        async function cargarHistorial() {
            const res = await fetch('/api/history');
            const data = await res.json();
            document.getElementById('snapshotId').textContent = data.length + ' snapshots';

            const fechas = data.map(d => new Date(d.created_at).toLocaleDateString()).reverse();
            const counts = data.map((d, i) => i + 1).reverse();

            if (chartSnapshots) chartSnapshots.destroy();
            const ctx = document.getElementById('chartSnapshots').getContext('2d');
            chartSnapshots = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: fechas.slice(-30),
                    datasets: [{
                        label: 'Snapshots acumulados',
                        data: counts.slice(-30),
                        borderColor: '#38bdf8',
                        backgroundColor: 'rgba(56, 189, 248, 0.1)',
                        fill: true,
                        tension: 0.3
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { ticks: { color: '#94a3b8', maxTicksLimit: 10 }, grid: { color: '#1e293b' } },
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

        cargarDatos();
        setInterval(cargarDatos, 60000);
    </script>
</body>
</html>
"""
