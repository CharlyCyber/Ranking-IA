# 🏆 Ranking AI Agent v4 — Open & Free AI Leaderboard Hub

Plataforma abierta y gratuita (Bot de Telegram + Dashboard Web / Mini App + API Abierta + RSS) que monitoriza en tiempo real **los rankings, precios y tendencias de modelos de IA**, con alertas deterministas cada 30 minutos, **briefing matutino cacheado** (`/hoy`), **menús instantáneos a 0 tokens** y **Modo Inline** para compartir comparativas en cualquier grupo.

## ✨ Novedades de la v4.0

- **Scraping y APIs resilientes (2026+)**:
  - **LMSYS / Arena**: soporte dual de tablas clásicas y tarjetas modernas (12 categorías: `Text`, `Agent`, `WebDev`, `Vision`, `Document`, `Search`, imagen y vídeo).
  - **OpenRouter**: uso semanal de tokens (con soporte de crecimientos `>999%` y negativos) + cruce automático con la **API pública gratuita `/api/v1/models`** para precios `$/1M tokens`, contexto y modelos `:free`.
  - **Design Arena**, **GitHub Search API**, **Hugging Face Trending (Open-Weights)** y **Hacker News** (Algolia + fallback Firebase).
- **Ingeniería de IA segura y sin coste descontrolado**:
  - **Menús interactivos y buscador a 0 tokens** (`/menu`, `/buscar`, `/chart`): las consultas habituales responden en `<50 ms` sin gastar llamadas al LLM.
  - **Caché global de `/hoy`**: el briefing diario se redacta **1 sola vez por snapshot** y se sirve desde caché a miles de usuarios.
  - **Failover Multi-LLM 100% Gratuito**: cascada automática **Google Gemini Free** → **Groq Cloud Free** (`llama-3.3-70b-versatile`) → **OpenRouter `:free`**.
  - **Defensa Anti-Prompt-Injection & Sanitizador HTML**: separación estricta de `system_instruction`, aislamiento `<untrusted_data>` para textos de GitHub/HN y whitelist HTML con cierre automático de etiquetas.
- **Listo para escalar gratis al público ($0/mes)**:
  - **Persistencia en la nube opcional con Turso (libSQL Free Tier)** vía HTTP API sin dependencias extra (no pierdes suscriptores en despliegues con disco efímero como Render Free).
  - **Difusión en Canal Público** (`PUBLIC_CHANNEL_ID`): 1 sola petición llega a suscriptores ilimitados.
  - **Preferencias por usuario** (`/alertas`): elige recibir *Todas*, *Solo Nuevo #1*, *Solo Coding/UI* o *Silenciar*.
  - **Modo Inline viral (`@TuBot modelo`)**: comparte fichas de cualquier modelo en cualquier chat o grupo a coste cero.
  - **Open Data Hub**: Dashboard adaptable como **Telegram Mini App**, feed **RSS 2.0** (`/feed.xml`), **gráficas SVG dinámicas** (`/api/chart.svg`) y **badges Shields.io** (`/api/badge/arena`).

## 🏗️ Arquitectura

```text
src/
├── main.py        # Bot Telegram: menús inline (0 tokens), Modo Inline, /hoy cacheado, auth admin
├── brain.py       # Multi-LLM gratuito (Gemini → Groq → OpenRouter :free) + guardrails anti-injection
├── insights.py    # Analítica determinista: Calidad/Precio, modelos gratis, buscador y gráficas SVG/QuickChart
├── html_utils.py  # Sanitizador HTML estricto para Telegram y partición segura de mensajes largos
├── fetcher.py     # Captura: OpenRouter Catalog API + Playwright con navegador único y espera de hidratación
├── parsers.py     # Parsers deterministas (Arena clásico/2026, OpenRouter, Design, GitHub, HF, HN)
├── validator.py   # Safety net por fuente antes de persistir
├── detector.py    # Diff local determinista + delta histórico de estrellas + filtro por preferencias
├── trending.py    # APIs oficiales de GitHub, Hugging Face y Hacker News (sin scraping)
├── updater.py     # Orquestador con asyncio.Lock, difusión a canal público y limpieza de bots bloqueados
├── db.py          # SQLite WAL concurrente + sincronización opcional con Turso Cloud (HTTP Pipeline)
├── web.py         # Dashboard Web / Telegram Mini App + API REST + RSS + Badges
└── keepalive.py   # Servidor web + healthcheck externo/local
```

## 🚀 Setup local rápido

```bash
# 1. Dependencias
pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium

# 2. Configuración
cp .env.example .env   # rellena TELEGRAM_TOKEN, GEMINI_API_KEY y CHAT_ID

# 3. Lint y tests
ruff check .
pytest -q

# 4. Arrancar bot + dashboard web (http://localhost:5000/dashboard)
python -m src.main
```

## 🤖 Comandos del Bot

| Comando | Coste LLM | Descripción |
|---------|:---------:|-------------|
| `/start` / `/menu` | **0 tokens** | Panel interactivo (General, Coding, Multimodal, OpenRouter, Gratis/Precio) |
| `/buscar <modelo>` | **0 tokens** | Posición en todos los rankings, ELO y precio por millón de tokens |
| `/chart` | **0 tokens** | Tarjeta visual PNG con el gráfico ELO actual (QuickChart) |
| `/alertas` | **0 tokens** | Configurar filtro de notificaciones (*Todas*, *Top-1*, *Coding*, *Pausa*) |
| `/hoy` | **Cacheado (1/snapshot)** | Briefing ejecutivo del día: rankings + GitHub + HF + HN |
| `/status` | **0 tokens** | Salud de fuentes, proveedores IA activos y cuota restante |
| `/subscribe` / `/unsubscribe` | **0 tokens** | Alta o baja de alertas automáticas |
| `/forceupdate` | *Solo Admin* | Fuerza actualización manual protegida por `CHAT_ID` y lock de concurrencia |
| `@TuBot <consulta>` | **0 tokens** | **Modo Inline**: úsalo desde cualquier grupo de Telegram |
| *(texto libre)* | **1 llamada** | Pregunta abierta al analista IA con rate limit por usuario |

## 🌐 Endpoints Públicos Gratuitos (Dashboard & Open Data)

| Ruta | Descripción |
|------|-------------|
| `/dashboard` | Monitor interactivo con buscador, pestañas, gráfica ELO y radar Gratis/Precio (compatible con Telegram Mini App) |
| `/api/rankings` | Último snapshot completo normalizado en JSON |
| `/api/models/search?q=claude` | Buscador transversal de modelos y precios |
| `/api/value-leaders` | Índice Calidad/Precio (ELO vs $/1M tok) y modelos API `$0` |
| `/api/elo-history?source=arena&category=Text` | Series temporales para gráficas |
| `/api/chart.svg?source=arena&category=Text` | Gráfico SVG dinámico listo para incrustar en README o web |
| `/api/badge/arena` | Endpoint JSON compatible con Shields.io (`https://img.shields.io/endpoint?url=...`) |
| `/feed.xml` | Feed RSS 2.0 de cambios detectados (conecta gratis con Slack, Discord, Feedly o n8n) |

## 💸 Cómo publicarlo para todo el mundo a $0/mes (100% Herramientas Gratuitas)

1. **Base de datos sin pérdida en redeploys → [Turso](https://turso.tech) (Free Tier: 5 GB gratis)**:
   - Crea una BD gratuita en Turso y añade `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN` en tus variables de entorno. Así tus suscriptores e histórico sobreviven aunque el contenedor sea efímero.
2. **Alta disponibilidad de IA → [Google AI Studio](https://aistudio.google.com/apikey) + [Groq Cloud](https://console.groq.com/keys) (Gratis)**:
   - Configura `GEMINI_API_KEY` y añade `GROQ_API_KEY` (y opcionalmente `OPENROUTER_API_KEY`). Si un proveedor llega al rate limit gratuito, el bot salta al siguiente sin interrupción.
3. **Escala ilimitada de alertas → Canal Público de Telegram**:
   - Crea un canal público (ej. `@RankingIA_ES`), mete al bot como administrador y define `PUBLIC_CHANNEL_ID=@RankingIA_ES`. Publicar ahí consume **1 sola llamada** aunque tengas 50.000 lectores.
4. **Activar Modo Inline y Mini App en `@BotFather` (Gratis, 1 minuto)**:
   - Envía `/setinline` a `@BotFather` para habilitar `@TuBot <modelo>` en cualquier grupo.
   - Define `PUBLIC_URL=https://tu-dominio-https` para que aparezca automáticamente el botón **📱 Abrir Dashboard Interactivo** dentro de Telegram.
5. **Hosting gratuito recomendado**:
   - **GitHub Actions** (`.github/workflows/daily_scrape.yml`): ejecuta el respaldo estructurado en la rama `data` con runners `ubuntu-24.04` gratuitos.
   - **Hugging Face Spaces (Docker CPU Basic: 2 vCPU y 16 GB RAM gratis)** o **Render Free / Koyeb Free** para el proceso del bot + dashboard.

## 📄 Licencia

[MIT](LICENSE)
