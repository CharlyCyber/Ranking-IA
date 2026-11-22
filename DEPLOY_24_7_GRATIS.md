# Guía de despliegue: opciones gratuitas y límites operativos

Esta guía explica cómo desplegar **Ranking AI Agent** usando opciones que pueden incluir tiers gratuitos. El worker intenta revisar rankings cada minuto (`UPDATE_INTERVAL_MINUTES=1`), pero la frescura efectiva depende de cada fuente, límites de API y hosting. Los servicios gratuitos pueden dormir, limitar o cambiar sus condiciones; no se garantiza uptime ni coste cero permanente.

---

## 🧠 Disponibilidad, límites y failover (sin garantías de uptime gratuito)

1. **Los cron jobs no son un servicio de uptime**: GitHub Actions y servicios externos pueden retrasarse, suspenderse o cambiar sus límites. Usa el ping solo como best-effort; no es una garantía de que el hosting permanezca despierto.
2. **El arranque bloqueaba el puerto web**: antes el código descargaba Chromium (~180 MB) **antes** de abrir el puerto HTTP, provocando timeouts de 2 minutos al despertar.
   - **Solucionado**: ahora el servidor HTTP arranca en **<100 ms** en primer lugar y el `Dockerfile` trae Chromium **ya preinstalado** (`SKIP_CHROMIUM_INSTALL=1`).
3. **Failover best-effort**: el workflow `.github/workflows/daily_scrape.yml` revisa el estado y puede actuar como respaldo horario. El comportamiento depende de que GitHub Actions, la base de datos y las credenciales estén disponibles; verifica logs y alertas después del despliegue.

---

## Opción de hosting: Hugging Face Spaces u otro proveedor compatible con Docker

Antes de desplegar, comprueba los recursos, límites de uso, política de suspensión y condiciones actuales del plan. Los planes y capacidades cambian; esta guía no presupone memoria, CPU ni uptime garantizados.

### Pasos generales:
1. Crea una cuenta en [huggingface.co](https://huggingface.co), revisa las condiciones vigentes y pulsa **New Space**:
   - **Space name**: `ranking-ia` (o el que prefieras)
   - **SDK**: selecciona **Docker** → elige plantilla **Blank**
   - **Hardware**: elige un perfil compatible con los requisitos actuales del contenedor y el presupuesto del proyecto
   - **Visibility**: **Public** (los Spaces privados no permiten ping externo sin token)
2. En la pestaña **Settings → Variables and secrets** del Space:
   - Añade como **Secrets**:
     - `TELEGRAM_TOKEN` = *(tu token de BotFather)*
     - `GEMINI_API_KEY` = *(tu clave de Google AI Studio, según las condiciones vigentes)*
     - `CHAT_ID` = *(tu ID numérico de Telegram)*
     - *(Opcional)* `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
   - `TAVILY_API_KEY` habilita búsqueda web para preguntas abiertas; consulta la cuota y condiciones actuales antes de habilitarla públicamente.
   - Añade como **Variables**:
     - `UPDATE_INTERVAL_MINUTES` = `1`
3. Sube o sincroniza tu repo de GitHub con el Space:
   - Añade en la parte superior del `README.md` del Space este encabezado YAML (o súbelo con `git push`):
     ```yaml
     ---
     title: Ranking IA Monitor
     emoji: 🏆
     colorFrom: blue
     colorTo: cyan
     sdk: docker
     app_port: 7860
     ---
     ```
   - El `Dockerfile` de este repositorio ya escucha en `7860`, usa el usuario `uid=1000` exigido por Hugging Face y arranca en `<1 segundo`.
4. Tu URL pública será:
   `https://<tu-usuario>-ranking-ia.hf.space`
   *(El Dashboard estará en `/dashboard` y la salud en `/health`).*

---

## Ping externo opcional (best-effort): `cron-job.org`

Un monitor externo puede reducir periodos de inactividad en algunos planes, pero no garantiza uptime ni evita suspensiones establecidas por el proveedor. Comprueba sus límites y términos antes de configurarlo.

1. Crea una cuenta gratuita en [console.cron-job.org](https://console.cron-job.org) (0€, sin tarjeta).
2. Pulsa **Create Cronjob**:
   - **Title**: `Keep Ranking IA Awake`
   - **URL**: `https://<tu-dominio-en-hf-o-render>/health`
   - **Execution schedule**: **Every 10 minutes** (`*/10 * * * *`)
   - Activa **Save responses / Notify me when cronjob fails** (así te llega email si algún día cae).
3. Guarda también esa misma URL en tu repositorio de GitHub (`Settings → Secrets and variables → Actions → New repository secret`):
   - Nombre: `BOT_URL`
   - Valor: `https://<tu-dominio-en-hf-o-render>`

Este ping solo comprueba que el endpoint responde en ese momento. No elimina los límites de suspensión del hosting ni garantiza que el bot o el worker estén ejecutándose.

---

## 🔄 Opción #2: Si prefieres seguir en Render Free

Este repositorio ya incluye `render.yaml` configurado para usar **Docker** en vez del entorno nativo de Python:
1. En Render → **New + → Blueprint** (o en tu Web Service actual cambia Runtime a **Docker**).
2. Al usar nuestro `Dockerfile`, Chromium ya viene instalado en la imagen y el puerto abre en **<100 ms** (se acabaron los cuelgues de 2 minutos al despertar).
3. Rellena tus variables (`TELEGRAM_TOKEN`, `GEMINI_API_KEY`, `CHAT_ID`, `UPDATE_INTERVAL_MINUTES=1`) y conecta **cron-job.org** cada 10 minutos a `https://tu-servicio.onrender.com/health`.

> 💡 **Persistencia en despliegues efímeros**: configura una base de datos remota compatible con Turso/libSQL y añade `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN`. Verifica el estado del servicio y conserva copias de seguridad; la persistencia depende de su disponibilidad y del plan.

## Separar la web del worker de Telegram

Para una topología con web y worker en servicios distintos:

1. En el servicio de bot configura `APP_MODE=bot` y arranca `python -m src.main`.
2. En el servicio web ejecuta `gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 src.keepalive:flask_app`.
3. Configura Turso/libSQL con la misma URL y token en ambos servicios. El worker actualiza un heartbeat compartido; `/health` lo declara activo solo mientras llega dentro de la ventana de tolerancia.
4. Mantén **un solo worker de polling** de Telegram. Añadir réplicas de bot sin coordinar el polling puede duplicar procesos y trabajos.
5. El limitador de API es por proceso. Si escalas la web a varias réplicas, configura límites globales en el CDN/API gateway.
