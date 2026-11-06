# Imagen oficial con Python + Chromium y librerías del sistema ya preinstaladas.
# Arranca en <1 segundo sin descargar navegadores en caliente.
# Compatible con Hugging Face Spaces (16 GB RAM Free, uid=1000, puerto 7860) y Render / Koyeb.
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    SKIP_CHROMIUM_INSTALL=1 \
    UPDATE_INTERVAL_MINUTES=60 \
    PORT=7860

# Crear usuario no-root uid=1000 (requerido por Hugging Face Spaces) si no existe
RUN id -u 1000 >/dev/null 2>&1 || useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY src ./src
COPY data/rankings.json ./data/rankings.json

RUN chown -R 1000:1000 /app && chmod -R 775 /app/data

USER 1000

EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; p=os.environ.get('PORT','7860'); urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=5)"

CMD ["python", "-m", "src.main"]
