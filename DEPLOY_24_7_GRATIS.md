# 🟢 Guía Rápida: Cómo tener el Bot Siempre Despierto 24/7 Gratis ($0/mes)

Esta guía explica cómo desplegar **Ranking AI Agent** para que **nunca se duerma**, revise los rankings **cada 1 hora** (`UPDATE_INTERVAL_MINUTES=60`) y te avise en Telegram en cuanto haya cambios reales, sin gastar un euro ni necesitar tarjeta de crédito.

---

## 🧠 ¿Por qué se dormían antes los bots gratuitos (y cómo ya está blindado)?

1. **GitHub Actions `cron` sufre retrasos de varias horas**: un cron de `*/10` en GitHub Actions puede tardar 4–8 horas en ejecutarse cuando hay carga en GitHub, por lo que **no sirve por sí solo** para evitar el *sleep* de 15 minutos de Render Free.
2. **El arranque bloqueaba el puerto web**: antes el código descargaba Chromium (~180 MB) **antes** de abrir el puerto HTTP, provocando timeouts de 2 minutos al despertar.
   - **Solucionado**: ahora el servidor HTTP arranca en **<100 ms** en primer lugar y el `Dockerfile` trae Chromium **ya preinstalado** (`SKIP_CHROMIUM_INSTALL=1`).
3. **Doble red de seguridad automática**:
   - **Capa 1 (Principal 24/7)**: Tu contenedor en **Hugging Face Spaces (16 GB RAM Gratis)** o **Render Free** + un ping puntual gratuito cada 10 min desde **cron-job.org**.
   - **Capa 2 (Failover inteligente en GitHub Actions)**: `.github/workflows/daily_scrape.yml` corre cada hora; si ve que tu bot 24/7 está vivo (`/health` = `200 OK`), solo guarda respaldo silencioso sin duplicar mensajes. **Si algún día el hosting cae, GitHub Actions toma el relevo automáticamente y te envía él mismo las alertas a Telegram.**

---

## 🏆 Opción Recomendada #1: Hugging Face Spaces (16 GB RAM Gratis · Sin tarjeta)

Es con diferencia el mejor hosting gratuito actual para este proyecto porque te da **2 vCPU + 16 GB de RAM gratis** (Chromium jamás muere por falta de memoria como en los 512 MB de Render) y **no se duerme cada 15 minutos** (aguanta hasta 48 h sin tráfico).

### Pasos (5 minutos):
1. Crea una cuenta gratuita en [huggingface.co](https://huggingface.co) y pulsa **New Space**:
   - **Space name**: `ranking-ia` (o el que prefieras)
   - **SDK**: selecciona **Docker** → elige plantilla **Blank**
   - **Hardware**: **CPU basic · 2 vCPU · 16 GB · FREE**
   - **Visibility**: **Public** (los Spaces privados no permiten ping externo sin token)
2. En la pestaña **Settings → Variables and secrets** del Space:
   - Añade como **Secrets**:
     - `TELEGRAM_TOKEN` = *(tu token de BotFather)*
     - `GEMINI_API_KEY` = *(tu clave gratuita de Google AI Studio)*
     - `CHAT_ID` = *(tu ID numérico de Telegram)*
     - *(Opcional recomendado)* `GROQ_API_KEY`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
   - Añade como **Variables**:
     - `UPDATE_INTERVAL_MINUTES` = `60`
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

## ⚡ Paso Clave para 100% Uptime (Vale tanto para HF Spaces como para Render Free): `cron-job.org`

A diferencia de GitHub Actions, **[cron-job.org](https://cron-job.org)** es un servicio gratuito especializado que dispara peticiones HTTP con precisión de reloj suizo cada X minutos:

1. Crea una cuenta gratuita en [console.cron-job.org](https://console.cron-job.org) (0€, sin tarjeta).
2. Pulsa **Create Cronjob**:
   - **Title**: `Keep Ranking IA Awake`
   - **URL**: `https://<tu-dominio-en-hf-o-render>/health`
   - **Execution schedule**: **Every 10 minutes** (`*/10 * * * *`)
   - Activa **Save responses / Notify me when cronjob fails** (así te llega email si algún día cae).
3. Guarda también esa misma URL en tu repositorio de GitHub (`Settings → Secrets and variables → Actions → New repository secret`):
   - Nombre: `BOT_URL`
   - Valor: `https://<tu-dominio-en-hf-o-render>`

Con eso:
- **Render Free** recibe tráfico entrante real cada 10 min (nunca llega al umbral de 15 min de sleep).
- **Hugging Face Spaces** nunca llega a las 48 h de inactividad.
- **GitHub Actions** sabe que tu bot está vivo y evita enviar alertas duplicadas.

---

## 🔄 Opción #2: Si prefieres seguir en Render Free

Este repositorio ya incluye `render.yaml` configurado para usar **Docker** en vez del entorno nativo de Python:
1. En Render → **New + → Blueprint** (o en tu Web Service actual cambia Runtime a **Docker**).
2. Al usar nuestro `Dockerfile`, Chromium ya viene instalado en la imagen y el puerto abre en **<100 ms** (se acabaron los cuelgues de 2 minutos al despertar).
3. Rellena tus variables (`TELEGRAM_TOKEN`, `GEMINI_API_KEY`, `CHAT_ID`, `UPDATE_INTERVAL_MINUTES=60`) y conecta **cron-job.org** cada 10 minutos a `https://tu-servicio.onrender.com/health`.

> 💡 **Consejo extra para no perder datos al reiniciar contenedores gratuitos**: crea una base de datos gratuita en [turso.tech](https://turso.tech) y añade `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN`. Tus suscriptores y tu histórico de ELO de 30 días quedarán guardados para siempre en la nube.
