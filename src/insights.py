"""Analítica determinista a 0 tokens: búsquedas, comparativas, Calidad/Precio y gráficas SVG.

Permite responder al instante en botones de Telegram, Modo Inline (@bot <query>) y API REST
sin consumir cuota de LLM.
"""

from urllib.parse import quote

from src.html_utils import escape_html
from src.parsers import _norm_model_key


def fmt_compact_number(n) -> str:
    if not isinstance(n, (int, float)):
        return str(n)
    for divisor, suffix in (
        (1_000_000_000_000, "T"),
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    ):
        if abs(n) >= divisor:
            return f"{n / divisor:.1f}{suffix}"
    return str(int(n) if isinstance(n, int) else round(n, 2))


def fmt_price(prompt_per_m: float | None, completion_per_m: float | None) -> str:
    if prompt_per_m is None and completion_per_m is None:
        return "s/d"
    p = float(prompt_per_m or 0)
    c = float(completion_per_m or 0)
    if p == 0 and c == 0:
        return "GRATIS ($0)"
    return f"${p:.2f} / ${c:.2f} por 1M tok"


def search_models(snapshot: dict | None, query: str, limit: int = 12) -> list[dict]:
    """Busca modelos en todas las fuentes del snapshot agrupando sus posiciones."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sources"), dict):
        return []

    q = (query or "").strip().lower()
    q_norm = _norm_model_key(q)
    aggregated: dict[str, dict] = {}

    sources = snapshot["sources"]
    for src_name in ("arena", "openrouter", "design"):
        src = sources.get(src_name)
        if not isinstance(src, dict) or src.get("status") not in ("ok", "stale"):
            continue
        for entry in src.get("models") or []:
            if not isinstance(entry, dict) or not entry.get("model"):
                continue
            model_name = str(entry["model"])
            key = _norm_model_key(model_name)
            if q and q not in model_name.lower() and (not q_norm or q_norm not in key):
                continue

            item = aggregated.setdefault(
                key or model_name.lower(),
                {
                    "model": model_name,
                    "appearances": [],
                    "best_rank": 999,
                    "prompt_per_m": entry.get("prompt_per_m"),
                    "completion_per_m": entry.get("completion_per_m"),
                    "context_length": entry.get("context_length"),
                    "is_free": bool(entry.get("is_free")),
                },
            )
            rank = int(entry.get("rank") or 999)
            item["best_rank"] = min(item["best_rank"], rank)
            if item["prompt_per_m"] is None and entry.get("prompt_per_m") is not None:
                item["prompt_per_m"] = entry.get("prompt_per_m")
                item["completion_per_m"] = entry.get("completion_per_m")
                item["context_length"] = entry.get("context_length")
                item["is_free"] = bool(entry.get("is_free"))

            cat = entry.get("category")
            label = f"{src_name}/{cat}" if cat else src_name
            item["appearances"].append(
                {
                    "source": label,
                    "rank": rank,
                    "score": entry.get("score"),
                    "win_rate_pct": entry.get("win_rate_pct"),
                    "tokens_week": entry.get("tokens_week"),
                    "growth_pct": entry.get("growth_pct"),
                }
            )

    results = sorted(aggregated.values(), key=lambda x: (x["best_rank"], -len(x["appearances"])))
    return results[:limit]


def compute_value_leaders(snapshot: dict | None, limit: int = 8) -> dict:
    """Cruza ELO de Arena/Design con precios de OpenRouter + lista de modelos gratuitos."""
    out: dict[str, list] = {"value_models": [], "free_models": []}
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sources"), dict):
        return out

    sources = snapshot["sources"]
    openrouter = sources.get("openrouter") if isinstance(sources.get("openrouter"), dict) else {}
    free_catalog = openrouter.get("free_models") if isinstance(openrouter.get("free_models"), list) else []

    # También detectar modelos con "(free)" o precio 0 dentro del propio ranking
    seen_free: set[str] = set()
    for fm in free_catalog:
        if isinstance(fm, dict) and fm.get("name"):
            seen_free.add(str(fm["name"]).lower())
            out["free_models"].append(fm)

    for src_name in ("openrouter", "arena", "design"):
        src = sources.get(src_name)
        if not isinstance(src, dict):
            continue
        for m in src.get("models") or []:
            if not isinstance(m, dict) or not m.get("model"):
                continue
            name = str(m["model"])
            if (m.get("is_free") or "(free)" in name.lower() or ":free" in name.lower()) and name.lower() not in seen_free:
                seen_free.add(name.lower())
                out["free_models"].append(
                    {
                        "id": name,
                        "name": name,
                        "context_length": m.get("context_length") or 0,
                        "prompt_per_m": 0.0,
                        "completion_per_m": 0.0,
                        "is_free": True,
                        "rank_info": f"#{m.get('rank')} en {src_name}",
                    }
                )

    # Índice Calidad / Precio sobre modelos con ELO y precio > 0 conocido
    candidates: dict[str, dict] = {}
    arena = sources.get("arena") if isinstance(sources.get("arena"), dict) else {}
    for m in arena.get("models") or []:
        if not isinstance(m, dict) or not m.get("score") or m.get("category") not in ("Text", "Code", "WebDev", None):
            continue
        blended = None
        if m.get("prompt_per_m") is not None and m.get("completion_per_m") is not None:
            # Coste medio ponderado 3:1 (input:output) por millón de tokens
            blended = (3 * float(m["prompt_per_m"]) + float(m["completion_per_m"])) / 4.0
        if blended is None or blended <= 0:
            continue
        score = int(m["score"])
        # Puntos de ELO por encima de 1200 divididos por coste suavizado
        value_index = round((max(score - 1200, 10)) / ((blended + 0.25) ** 0.45), 1)
        key = _norm_model_key(m["model"])
        prev = candidates.get(key)
        if not prev or value_index > prev["value_index"]:
            candidates[key] = {
                "model": m["model"],
                "category": m.get("category") or "Text",
                "rank": m.get("rank"),
                "score": score,
                "blended_per_m": round(blended, 3),
                "prompt_per_m": m.get("prompt_per_m"),
                "completion_per_m": m.get("completion_per_m"),
                "value_index": value_index,
            }

    out["value_models"] = sorted(candidates.values(), key=lambda x: x["value_index"], reverse=True)[:limit]
    out["free_models"] = out["free_models"][:limit]
    return out


def render_category_card(snapshot: dict | None, view: str) -> str:
    """Renderiza tarjetas HTML instantáneas (0 tokens) para el menú interactivo de Telegram."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sources"), dict):
        return "⚠️ <i>Aún no hay datos disponibles. Inténtalo en unos minutos.</i>"

    sources = snapshot["sources"]
    arena = sources.get("arena") if isinstance(sources.get("arena"), dict) else {}
    arena_models = arena.get("models") or []

    if view == "general":
        rows = [m for m in arena_models if m.get("category") in ("Text", None)][:10]
        if not rows:
            return "⚠️ <i>Ranking General de Arena no disponible en este momento.</i>"
        lines = ["🏆 <b>Top 10 IA General (LMSYS Arena · Text)</b>\n"]
        for m in rows:
            price = f" · 💰 {fmt_price(m.get('prompt_per_m'), m.get('completion_per_m'))}" if m.get("prompt_per_m") is not None else ""
            lines.append(f"<b>{m['rank']}. {escape_html(m['model'])}</b> — <code>{m.get('score')}</code> elo{price}")
        return "\n".join(lines)

    if view == "coding":
        code_rows = [m for m in arena_models if m.get("category") in ("Code", "WebDev")][:8]
        design = (sources.get("design") or {}).get("models") or []
        lines = []
        if code_rows:
            cat_name = code_rows[0].get("category") or "Code"
            lines.append(f"💻 <b>Top Programación (Arena · {escape_html(cat_name)})</b>")
            for m in code_rows[:6]:
                lines.append(f"  {m['rank']}. <b>{escape_html(m['model'])}</b> — <code>{m.get('score')}</code> elo")
        if design:
            lines.append("\n🎨 <b>Top Frontend / UI (Design Arena)</b>")
            for m in design[:5]:
                lines.append(f"  {m['rank']}. <b>{escape_html(m['model'])}</b> — <code>{m.get('score')}</code> elo")
        return "\n".join(lines) if lines else "⚠️ <i>Datos de programación no disponibles ahora mismo.</i>"

    if view == "multimodal":
        lines = ["🎬 <b>Líderes Multimodales (Arena)</b>"]
        for cat, icon in (("Vision", "👁️"), ("Text-to-Image", "🖼️"), ("Image Edit", "✏️"), ("Text-to-Video", "🎥")):
            top3 = [m for m in arena_models if m.get("category") == cat][:3]
            if top3:
                lines.append(f"\n{icon} <b>{cat}</b>")
                for m in top3:
                    lines.append(f"  {m['rank']}. {escape_html(m['model'])} — <code>{m.get('score')}</code> elo")
        return "\n".join(lines) if len(lines) > 1 else "⚠️ <i>Datos multimodales no disponibles.</i>"

    if view == "openrouter":
        or_models = ((sources.get("openrouter") or {}).get("models") or [])[:10]
        if not or_models:
            return "⚠️ <i>Datos de uso de OpenRouter no disponibles.</i>"
        lines = ["⚡ <b>Top Uso Real en Producción (OpenRouter · Esta Semana)</b>\n"]
        for m in or_models:
            tok = fmt_compact_number(m.get("tokens_week", 0))
            growth = m.get("growth_pct", 0)
            sign = "+" if growth >= 0 else ""
            lines.append(
                f"<b>{m['rank']}. {escape_html(m['model'])}</b> ({escape_html(m.get('author', ''))})\n"
                f"   └─ 📊 <code>{tok}</code> tokens · {sign}{growth}%"
            )
        return "\n".join(lines)

    if view == "free":
        leaders = compute_value_leaders(snapshot, limit=8)
        lines = []
        if leaders["free_models"]:
            lines.append("🆓 <b>Mejores Modelos con API Gratuita ($0)</b>")
            for i, fm in enumerate(leaders["free_models"][:6], 1):
                ctx = f" · {int(fm['context_length']) // 1000}K ctx" if fm.get("context_length") else ""
                extra = f" · {escape_html(fm['rank_info'])}" if fm.get("rank_info") else ""
                lines.append(f"  {i}. <b>{escape_html(fm.get('name') or fm.get('id'))}</b>{ctx}{extra}")
        if leaders["value_models"]:
            lines.append("\n💎 <b>Mejor Calidad / Precio (ELO vs $/1M tokens)</b>")
            for vm in leaders["value_models"][:5]:
                lines.append(
                    f"  • <b>{escape_html(vm['model'])}</b> ({vm['score']} elo) — "
                    f"~${vm['blended_per_m']:.2f}/1M tok"
                )
        if not lines:
            return (
                "🆓 <b>Radar de Modelos Gratis y Calidad/Precio</b>\n\n"
                "<i>Los precios en vivo se enriquecen automáticamente en la próxima actualización con OpenRouter API.</i>"
            )
        return "\n".join(lines)

    return "Selecciona una categoría del menú."


def render_model_card_html(item: dict) -> str:
    """Ficha HTML compacta de un modelo (para búsqueda o Modo Inline de Telegram)."""
    lines = [f"🤖 <b>{escape_html(item['model'])}</b>"]
    if item.get("prompt_per_m") is not None or item.get("is_free"):
        ctx = f" · Contexto: {int(item['context_length']) // 1000}K" if item.get("context_length") else ""
        lines.append(f"💰 Precio API: <b>{fmt_price(item.get('prompt_per_m'), item.get('completion_per_m'))}</b>{ctx}")

    lines.append("\n📊 <b>Posiciones actuales:</b>")
    for app in item.get("appearances", [])[:8]:
        src = escape_html(app["source"])
        if app.get("score") is not None:
            metric = f"{app['score']} elo"
        elif app.get("win_rate_pct") is not None:
            metric = f"{app['win_rate_pct']}%"
        elif app.get("tokens_week") is not None:
            metric = f"{fmt_compact_number(app['tokens_week'])} tok/sem"
        else:
            metric = ""
        lines.append(f"  • <b>#{app['rank']}</b> en {src}" + (f" — <code>{metric}</code>" if metric else ""))
    return "\n".join(lines)


def render_elo_svg(series: dict[str, list], title: str = "Evolución de ELO (30 días)") -> str:
    """Genera un gráfico SVG limpio y ligero sin dependencias externas (0 MB extra de RAM)."""
    width, height = 760, 340
    pad_l, pad_r, pad_t, pad_b = 58, 24, 48, 66
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    palette = ["#38bdf8", "#4ade80", "#fbbf24", "#f87171", "#c084fc", "#22d3ee", "#fb923c", "#f472b6"]

    all_dates = sorted({pt[0] for pts in series.values() for pt in pts})
    all_vals = [pt[1] for pts in series.values() for pt in pts if isinstance(pt[1], (int, float))]

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" rx="12" fill="#0f172a"/>',
        f'<text x="{pad_l}" y="28" fill="#38bdf8" font-family="system-ui,sans-serif" font-size="15" font-weight="700">'
        f"{escape_html(title)}</text>",
    ]

    if not all_dates or not all_vals:
        svg.append(
            f'<text x="{width // 2}" y="{height // 2}" text-anchor="middle" fill="#94a3b8" '
            'font-family="system-ui,sans-serif" font-size="14">Acumulando historial de puntos…</text></svg>'
        )
        return "".join(svg)

    v_min, v_max = min(all_vals), max(all_vals)
    if v_min == v_max:
        v_min -= 10
        v_max += 10
    span = v_max - v_min

    def x_pos(idx: int) -> float:
        if len(all_dates) == 1:
            return pad_l + plot_w / 2
        return pad_l + (idx / (len(all_dates) - 1)) * plot_w

    def y_pos(val: float) -> float:
        return pad_t + plot_h - ((val - v_min) / span) * plot_h

    # Rejilla horizontal
    for step in range(5):
        val = v_min + (span * step / 4)
        y = y_pos(val)
        y_label = fmt_compact_number(round(val)) if abs(val) >= 10_000 else str(int(round(val)))
        svg.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="#1e293b" stroke-width="1"/>')
        svg.append(
            f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" fill="#94a3b8" '
            f'font-family="system-ui,sans-serif" font-size="11">{y_label}</text>'
        )

    date_index = {d: i for i, d in enumerate(all_dates)}
    for idx, (model_name, pts) in enumerate(list(series.items())[:8]):
        color = palette[idx % len(palette)]
        coords = [(x_pos(date_index[d]), y_pos(v)) for d, v in pts if d in date_index]
        if len(coords) >= 2:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
            svg.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{poly}"/>')
        for x, y in coords:
            svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"/>')

        # Leyenda inferior en 2 filas de 4 columnas
        col = idx % 4
        row = idx // 4
        lx = pad_l + col * (plot_w / 4)
        ly = height - 34 + row * 18
        svg.append(f'<rect x="{lx:.1f}" y="{ly - 8:.1f}" width="10" height="10" rx="2" fill="{color}"/>')
        svg.append(
            f'<text x="{lx + 14:.1f}" y="{ly:.1f}" fill="#cbd5e1" font-family="system-ui,sans-serif" font-size="10.5">'
            f"{escape_html(model_name[:22])}</text>"
        )

    svg.append("</svg>")
    return "".join(svg)


def build_quickchart_url(models: list[dict], title: str = "Top Modelos IA (ELO)") -> str | None:
    """Construye una URL gratuita de QuickChart.io (PNG compartible sin servidor extra)."""
    scored = [m for m in models if isinstance(m, dict) and isinstance(m.get("score"), int)][:8]
    if len(scored) < 2:
        return None
    import json

    labels = [str(m["model"])[:24] for m in scored]
    values = [int(m["score"]) for m in scored]
    min_score = max(0, min(values) - 30)
    cfg = {
        "type": "horizontalBar",
        "data": {
            "labels": labels,
            "datasets": [{"label": "ELO", "data": values, "backgroundColor": "#38bdf8"}],
        },
        "options": {
            "title": {"display": True, "text": title, "fontColor": "#f1f5f9"},
            "legend": {"display": False},
            "scales": {
                "xAxes": [{"ticks": {"min": min_score, "fontColor": "#cbd5e1"}}],
                "yAxes": [{"ticks": {"fontColor": "#f1f5f9"}}],
            },
        },
    }
    encoded = quote(json.dumps(cfg, separators=(",", ":")))
    return f"https://quickchart.io/chart?bkg=%230f172a&w=700&h=380&c={encoded}"
