"""Transcripción de notas de voz / archivos de audio a texto.

Cascada de proveedores, coherente con la filosofía de tiers gratuitos del proyecto:
1. **Groq Whisper** (endpoint OpenAI-compatible) — usa `GROQ_API_KEY`; modelo configurable
   con `GROQ_TRANSCRIPTION_MODEL` (por defecto `whisper-large-v3-turbo`).
2. **Gemini** (audio inline) — usa `GEMINI_API_KEY` y los `GEMINI_MODELS` configurados.

Telegram entrega las notas de voz como OGG/Opus y los archivos de audio con su MIME
original; ambos proveedores aceptan esos formatos directamente, sin convertir.
"""

import logging

import httpx

from src.config import (
    GEMINI_API_KEY,
    GEMINI_MODELS,
    GROQ_API_KEY,
    GROQ_TRANSCRIPTION_MODEL,
)

logger = logging.getLogger(__name__)

GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Límite de subida de Groq Whisper (25 MB); la Bot API de Telegram descarga hasta 20 MB.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

_TRANSCRIBE_PROMPT = (
    "Transcribe este audio verbatim en su idioma original. "
    "Devuelve ÚNICAMENTE el texto transcrito, sin comentarios, comillas ni etiquetas."
)


class AudioTooLargeError(ValueError):
    """El archivo de audio supera el límite permitido."""


def _transcribe_groq(data: bytes, filename: str, mime_type: str) -> str | None:
    if not GROQ_API_KEY:
        return None
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                GROQ_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": (filename, data, mime_type)},
                data={"model": GROQ_TRANSCRIPTION_MODEL, "response_format": "json"},
            )
            resp.raise_for_status()
            payload = resp.json()
        text = (payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        if text:
            return text
        logger.warning("Groq Whisper devolvió una transcripción vacía.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error transcribiendo con Groq Whisper: %s", exc)
    return None


def _transcribe_gemini(data: bytes, mime_type: str) -> str | None:
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        for model_name in GEMINI_MODELS:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Part.from_bytes(data=data, mime_type=mime_type),
                        _TRANSCRIBE_PROMPT,
                    ],
                )
                text = (response.text or "").strip() if response else ""
                if text:
                    return text
                logger.warning("Gemini (%s) devolvió una transcripción vacía.", model_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error transcribiendo con Gemini (%s): %s", model_name, str(exc)[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo inicializar google-genai para transcripción: %s", exc)
    return None


def transcription_available() -> bool:
    """Indica si hay al menos un proveedor de transcripción configurado."""
    return bool(GROQ_API_KEY or GEMINI_API_KEY)


def transcribe_audio(data: bytes, filename: str = "audio.ogg", mime_type: str = "audio/ogg") -> str | None:
    """Transcribe audio a texto probando Groq Whisper y luego Gemini.

    Devuelve el texto transcrito, o ``None`` si ningún proveedor pudo transcribirlo
    (incluido el caso de audio inaudible o sin voz). Lanza ``AudioTooLargeError`` si el
    archivo supera ``MAX_AUDIO_BYTES``.
    """
    if not data:
        return None
    if len(data) > MAX_AUDIO_BYTES:
        raise AudioTooLargeError(f"Audio de {len(data)} bytes supera el límite de {MAX_AUDIO_BYTES} bytes.")
    filename = filename or "audio.ogg"
    mime_type = (mime_type or "audio/ogg").split(";")[0].strip() or "audio/ogg"
    return _transcribe_groq(data, filename, mime_type) or _transcribe_gemini(data, mime_type)
