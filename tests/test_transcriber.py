"""Transcripción de audio: cascada Groq Whisper → Gemini, límites y validaciones."""

import pytest

from src import transcriber


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, headers=None, files=None, data=None):
        self.requests.append({"url": url, "headers": headers, "files": files, "data": data})
        return _FakeResponse({"text": "  hola desde el audio  "})


def test_groq_transcription_success(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(transcriber, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(transcriber, "GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setattr(transcriber.httpx, "Client", lambda *a, **k: fake_client)

    result = transcriber.transcribe_audio(b"fake-audio-bytes", "voice.ogg", "audio/ogg")

    assert result == "hola desde el audio"
    assert len(fake_client.requests) == 1
    req = fake_client.requests[0]
    assert req["url"] == transcriber.GROQ_TRANSCRIPTIONS_URL
    assert req["headers"]["Authorization"] == "Bearer test-key"
    assert req["data"]["model"] == "whisper-large-v3-turbo"
    assert req["files"]["file"][0] == "voice.ogg"


def test_mime_type_se_normaliza(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(transcriber, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(transcriber.httpx, "Client", lambda *a, **k: fake_client)

    transcriber.transcribe_audio(b"bytes", "voice.ogg", "audio/ogg; codecs=opus")

    assert fake_client.requests[0]["files"]["file"][2] == "audio/ogg"


def test_cae_a_gemini_si_groq_falla(monkeypatch):
    monkeypatch.setattr(transcriber, "_transcribe_groq", lambda *a: None)
    monkeypatch.setattr(transcriber, "_transcribe_gemini", lambda data, mime: "texto gemini")

    assert transcriber.transcribe_audio(b"audio", "a.ogg", "audio/ogg") == "texto gemini"


def test_groq_tiene_prioridad_sobre_gemini(monkeypatch):
    monkeypatch.setattr(transcriber, "_transcribe_groq", lambda *a: "texto groq")

    def _boom(*args):
        raise AssertionError("Gemini no debería invocarse si Groq transcribe")

    monkeypatch.setattr(transcriber, "_transcribe_gemini", _boom)

    assert transcriber.transcribe_audio(b"audio", "a.ogg", "audio/ogg") == "texto groq"


def test_sin_proveedores_configurados_devuelve_none(monkeypatch):
    monkeypatch.setattr(transcriber, "GROQ_API_KEY", None)
    monkeypatch.setattr(transcriber, "GEMINI_API_KEY", None)

    assert transcriber.transcribe_audio(b"audio", "a.ogg", "audio/ogg") is None
    assert transcriber.transcription_available() is False


def test_audio_vacio_devuelve_none():
    assert transcriber.transcribe_audio(b"", "a.ogg", "audio/ogg") is None


def test_audio_demasiado_grande_lanza_error(monkeypatch):
    monkeypatch.setattr(transcriber, "MAX_AUDIO_BYTES", 10)

    with pytest.raises(transcriber.AudioTooLargeError):
        transcriber.transcribe_audio(b"x" * 11, "a.ogg", "audio/ogg")
