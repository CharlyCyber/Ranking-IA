<div align="center">
  <img src="assets/readme-banner.svg" alt="" width="100%" />
  <h1>🏆 Ranking AI Agent v4 — Open &amp; Free AI Leaderboard Hub</h1>
  <p>
  <a href="https://github.com/CharlyCyber/Ranking-IA/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/CharlyCyber/Ranking-IA/ci.yml?branch=main&amp;style=flat-square&amp;label=CI&amp;color=a8f01c&amp;labelColor=171c13" /></a>
  <img alt="Top language" src="https://img.shields.io/github/languages/top/CharlyCyber/Ranking-IA?style=flat-square&amp;color=a8f01c&amp;labelColor=171c13" />
  <img alt="Stars" src="https://img.shields.io/github/stars/CharlyCyber/Ranking-IA?style=flat-square&amp;color=a8f01c&amp;labelColor=171c13" />
  <img alt="Last commit" src="https://img.shields.io/github/last-commit/CharlyCyber/Ranking-IA?style=flat-square&amp;color=a8f01c&amp;labelColor=171c13" />
  </p>
</div>


Plataforma abierta y gratuita (Bot de Telegram + Dashboard Web / Mini App + API Abierta + RSS) que revisa automáticamente **cada minuto (objetivo configurable) rankings, precios y tendencias de modelos de IA**; la frescura real depende de cuándo publiquen las fuentes upstream (avisando únicamente cuando hay cambios reales), con **briefing matutino cacheado** (`/hoy`), **menús instantáneos a 0 tokens** y **Modo Inline** para compartir comparativas en cualquier grupo.

> 🟢 **¿Quieres desplegarlo con planes gratuitos?** Consulta [**DEPLOY_24_7_GRATIS.md**](DEPLOY_24_7_GRATIS.md). Los límites, suspensión e inclusiones de cada hosting/proveedor pueden cambiar: ningún plan gratuito garantiza uptime ni coste cero permanente.

## ✨ Novedades de la v4.4

- **Scraping y APIs resilientes (2026+)**:
  - **LMSYS / Arena**: soporte dual de tablas clásicas y tarjetas modernas (12 categorías: `Text`, `Agent`, `WebDev`, `Vision`, `Document`, `Search`, imagen y vídeo).
  - **OpenRouter**: uso semanal de tokens (con soporte de crecimientos `>999%` y negativos) + cruce automático con la **API pública gratuita `/api/v1/models`** para precios `$/1M tokens`, contexto y modelos `:free`.
  - **Design Arena**, **GitHub Search API**, **Hugging Face Trending (Open-Weights)** y **Hacker News** (Algolia + fallback Firebase).
- **Ingeniería de IA segura y sin coste descontrolado**:
  - **Menús interactivos y buscador a 0 tokens** (`/menu`, `/buscar`, `/chart`): las consultas habituales responden en `<50 ms` sin gastar llamadas al LLM.
  - **Caché global de `/hoy`**: el briefing diario se redacta **1 sola vez por snapshot** y se sirve desde caché a miles de usuarios.
  - **Failover multi-proveedor para usar free tiers cuando estén disponibles**: cascada automática **Google Gemini** → **Groq** → **OpenRouter `openrouter/free`** (router gratuito dinámico; modelos configurables por variables de entorno).
  - **Defensa Anti-Prompt-Injection & Sanitizador HTML**: separación estricta de `system_instruction`, aislamiento `<untrusted_data>` para textos de GitHub/HN y whitelist HTML con cierre automático de etiquetas.
- **Base técnica para uso público; escala según los límites de hosting y proveedores**:
  - **Persistencia remota opcional con Turso/libSQL** vía HTTP API sin dependencias extra (permite persistir suscriptores en despliegues con disco efímero si el servicio remoto está disponible y configurado).
  - **Difusión en Canal Público** (`PUBLIC_CHANNEL_ID`): una sola publicación llega a todos los lectores del canal; los chats con preferencias individuales se envían por separado y están sujetos a límites de Telegram.
  - **Preferencias por usuario** (`/alertas`): elige recibir *Todas*, *Solo Nuevo #1*, *Solo Coding/UI* o *Silenciar*.
  - **Modo Inline viral (`@TuBot modelo`)**: comparte fichas de cualquier modelo en cualquier chat o grupo a coste cero.
  - **Open Data Hub**: Dashboard adaptable como **Telegram Mini App**, feed **RSS 2.0** (`/feed.xml`), **gráficas SVG dinámicas** (`/api/chart.svg`) y **badges Shields.io** (`/api/badge/arena`).

## 🏗️ Arquitectura

```text
src/
├── main.py        # Bot Telegram: menús inline (0 tokens), Modo Inline, /hoy cacheado, auth admin
├── brain.py       # Multi-proveedor configurable + respuestas fundamentadas, citas y guardrails
├── live_search.py # Búsqueda web opcional bajo demanda (Tavily) con URLs/citas validadas
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

## 🕒 Frescura, conversación y despliegue escalable

- El worker intenta revisar fuentes cada minuto (`UPDATE_INTERVAL_MINUTES=1`), pero **no puede garantizar que los datos upstream cambien cada minuto**. Cada respuesta conversacional incluye hora del snapshot, fuentes disponibles y aviso si el snapshot supera 5 minutos o contiene fuentes stale.
- Con `TAVILY_API_KEY`, las preguntas abiertas consultan la web bajo demanda, aportan hasta tres textos extraídos de página cuando el proveedor los devuelve (si no, fragmentos) como evidencia no confiable y enlazan solo citas `[S1]`…`[S5]` que devuelve el buscador; una consulta idéntica se cachea cinco minutos. Configura `LIVE_SEARCH_DAILY_LIMIT`; sin clave, con cuota agotada o si falla el proveedor, el bot lo indica y limita la respuesta a los datos disponibles. La pregunta se envía al proveedor de búsqueda y al proveedor LLM configurado: no envíes información sensible. Ver resultados no equivale a verificar la fuente primaria.
- El chat conserva hasta 20 mensajes por conversación y los limpia tras 7 días. `/olvidar` borra el historial del chat. Las respuestas se fundamentan en el snapshot disponible; si un dato no está ahí, el bot debe indicarlo en vez de inventarlo.
- Para desplegar varias réplicas web y un solo bot/worker: configura `APP_MODE=bot` en el worker y sirve el dashboard con `gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 src.keepalive:flask_app`. Configura Turso para compartir snapshots, historial y límites LLM/usuario entre procesos. La API tiene rate limit básico por proceso; para varias réplicas, añade límites globales en CDN/API gateway. Conserva una sola instancia bot que haga polling.
- El modo por defecto `APP_MODE=all` mantiene el despliegue antiguo de una instancia. `APP_MODE=web` queda disponible para pruebas locales; para producción, usa Gunicorn.
- `openrouter/free` enruta a un modelo gratuito disponible en el momento de la llamada; Gemini, Groq y los fallbacks son configurables con `GEMINI_MODELS`, `GROQ_MODELS` y `OPENROUTER_FREE_MODELS`. La cuota gratuita, disponibilidad, modelos concretos y condiciones dependen de cada proveedor.

## 🚀 Setup local rápido

```bash
# 1. Dependencias
pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium

# 2. Configuración
cp .env.example .env   # rellena TELEGRAM_TOKEN, GEMINI_API_KEY, CHAT_ID, ADMIN_USER_ID y opcionalmente TAVILY_API_KEY

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
| `/status` | **0 tokens** | Salud de fuentes, proveedores configurados y presupuesto interno LLM restante |
| `/subscribe` / `/unsubscribe` | **0 tokens** | Alta o baja de alertas automáticas |
| `/olvidar` | **0 tokens** | Borra el historial conversacional de este chat |
| `/forceupdate` | *Solo Admin* | Fuerza actualización manual protegida por `CHAT_ID` y lock de concurrencia |
| `@TuBot <consulta>` | **0 tokens** | **Modo Inline**: úsalo desde cualquier grupo de Telegram |
| *(texto libre)* | **1 llamada** | Pregunta abierta al analista IA con rate limit por usuario |

`/forceupdate` requiere que `ADMIN_USER_ID` contenga el ID numérico de tu cuenta de Telegram. `CHAT_ID` es el chat privado al que se envían avisos administrativos; ambos valores pueden coincidir en un chat privado, pero no son necesariamente lo mismo si usas grupos.

### Activar Turso en Render

La integración ya está implementada en el código y usa la API HTTP de libSQL, así que no hay que instalar una dependencia adicional. Para activarla:

1. Crea una base de datos en tu cuenta de Turso y genera un **database auth token** con permisos de lectura y escritura.
2. Copia la URL de la base (normalmente con esquema `libsql://`) y guarda URL y token como secretos en Render → servicio → **Environment**:
   - `TURSO_DATABASE_URL` = URL de la base
   - `TURSO_AUTH_TOKEN` = token generado (secreto; no lo compartas ni lo pongas en el repositorio)
3. Guarda los cambios y despliega el código que contiene esta integración. Tras el reinicio, `/status` debe decir que Turso responde a una prueba de conexión; “configurado” por sí solo no confirma conectividad. Si no responde, el bot mantiene SQLite local como fallback y lo indicará.

La integración crea el esquema al arrancar y replica las nuevas escrituras. No importa automáticamente todo el histórico SQLite preexistente a Turso; conserva una copia antes de cambiar o eliminar el servicio local. Comprueba el estado después del despliegue.

## 🌐 Endpoints Públicos (Dashboard & Open Data)

| Ruta | Descripción |
|------|-------------|
| `/dashboard` | Monitor interactivo con buscador, pestañas, gráfica ELO y radar Gratis/Precio (compatible con Telegram Mini App) |
| `/api/rankings` | Último snapshot completo normalizado en JSON |
| `/api/freshness` | Hora de captura y edad de la última captura válida por fuente (no equivale a fecha upstream) |
| `/api/models/search?q=claude` | Buscador transversal de modelos y precios |
| `/api/value-leaders` | Índice Calidad/Precio (ELO vs $/1M tok) y modelos API `$0` |
| `/api/elo-history?source=arena&category=Text` | Series temporales para gráficas |
| `/api/chart.svg?source=arena&category=Text` | Gráfico SVG dinámico listo para incrustar en README o web |
| `/api/badge/arena` | Endpoint JSON compatible con Shields.io (`https://img.shields.io/endpoint?url=...`) |
| `/feed.xml` | Feed RSS 2.0 de cambios detectados (conecta gratis con Slack, Discord, Feedly o n8n) |

## 💸 Publicación con tiers gratuitos (sujetos a límites y cambios)

1. **Persistencia remota → [Turso](https://turso.tech) u otra base compatible**:
   - Revisa los planes actuales y configura `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN`. Es necesario para compartir datos entre réplicas; conserva respaldos.
2. **Failover de IA → Gemini, Groq y OpenRouter**:
   - Configura las claves de los proveedores que decidas usar. La cascada intenta alternativas, pero no puede asegurar disponibilidad ni evitar que todos los free tiers se agoten.
3. **Difusión eficiente → Canal Público de Telegram**:
   - Crea un canal, añade el bot como administrador y define `PUBLIC_CHANNEL_ID`. Una publicación evita enviar un mensaje individual a cada lector del canal; las alertas personalizadas sí tienen coste de envío por chat y límites de Telegram.
4. **Activar Modo Inline y Mini App en `@BotFather`**:
   - Envía `/setinline` a `@BotFather` para habilitar `@TuBot <modelo>` en cualquier grupo.
   - Define `PUBLIC_URL=https://tu-dominio-https` para que aparezca automáticamente el botón **📱 Abrir Dashboard Interactivo** dentro de Telegram.
5. **Hosting**:
   - El workflow `.github/workflows/daily_scrape.yml` es un respaldo horario best-effort, no un servicio de uptime.
   - Elige un proveedor Docker según sus condiciones, recursos y políticas actuales. Para crecer, separa un único worker Telegram y las réplicas web; configura Turso y un CDN/API gateway.

## 📄 Licencia

[MIT](LICENSE)


## 👨‍💻 Autor

Desarrollado por **Charli Rodríguez — Charli AI**.

- Portafolio: https://charli-ai-portfolio.vercel.app
- GitHub: https://github.com/CharlyCyber
