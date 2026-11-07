"""Utilidades seguras de HTML para Telegram y sanitización de salidas de LLM.

Garantiza que:
1. Datos externos no confiables (GitHub, HN, nombres de modelos) se escapen siempre.
2. URLs en ``<a href="...">`` solo permitan esquemas ``http://`` o ``https://``.
3. Salidas del LLM solo contengan el subconjunto HTML soportado por Telegram,
   cerrando automáticamente etiquetas huérfanas.
4. Mensajes largos (>4000 chars) se dividan sin dejar etiquetas HTML abiertas.
"""

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "code", "pre", "a"}
BLOCK_BREAK_TAGS = {"br", "p", "div", "li", "tr"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def escape_html(value: object) -> str:
    """Escapa cualquier valor como texto seguro para HTML."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def safe_url(url: object) -> str | None:
    """Valida que una URL sea http(s) bien formada y la devuelve escapada para href."""
    if not isinstance(url, str):
        return None
    cleaned = url.strip().replace('"', "%22").replace("'", "%27").replace("<", "%3C").replace(">", "%3E")
    if not cleaned or any(ch in cleaned for ch in ("\r", "\n", "\t", " ")):
        return None
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return html.escape(cleaned, quote=True)


class _TelegramHTMLSanitizer(HTMLParser):
    """Filtra HTML arbitrario dejando únicamente etiquetas válidas para Telegram."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "br":
            self.out.append("\n")
            return
        if tag in HEADING_TAGS:
            self.out.append("\n<b>")
            self.stack.append("b")
            return
        if tag == "li":
            self.out.append("\n• ")
            return
        if tag not in ALLOWED_TAGS:
            return

        if tag == "a":
            href = dict(attrs).get("href")
            safe = safe_url(href)
            if not safe:
                return
            self.out.append(f'<a href="{safe}">')
            self.stack.append("a")
            return

        self.out.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in HEADING_TAGS:
            tag = "b"
        elif tag in BLOCK_BREAK_TAGS:
            self.out.append("\n")
            return

        if tag not in ALLOWED_TAGS or tag not in self.stack:
            return

        # Cierra en orden LIFO hasta encontrar la etiqueta correspondiente
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        char = html.unescape(f"&{name};")
        self.out.append(html.escape(char, quote=False))

    def handle_charref(self, name: str) -> None:
        char = html.unescape(f"&#{name};")
        self.out.append(html.escape(char, quote=False))

    def get_html(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        result = "".join(self.out)
        # Colapsar excesos de saltos de línea generados al limpiar bloques
        return re.sub(r"\n{3,}", "\n\n", result).strip()


def sanitize_telegram_html(text: str) -> str:
    """Convierte HTML potencialmente roto o inseguro en HTML válido para Telegram."""
    if not text:
        return ""
    # Escapar cualquier '<' huérfano que no tenga '>' de cierre antes del siguiente '<' o fin de texto,
    # evitando que HTMLParser descarte el resto del mensaje si un modelo escribe '<texto' sin cerrar.
    safe_input = re.sub(r"<(?=[^<>]*(?:<|$))", "&lt;", text)
    parser = _TelegramHTMLSanitizer()
    try:
        parser.feed(safe_input)
        parser.close()
        return parser.get_html()
    except Exception:
        return escape_html(strip_html(text))


def strip_html(text: str) -> str:
    """Elimina todas las etiquetas HTML y decodifica entidades."""
    if not text:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def split_html_message(text: str, limit: int = 4000) -> list[str]:
    """Parte un mensaje en trozos <= limit respetando párrafos y cerrando HTML por chunk."""
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    raw_chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            raw_chunks.append(current)
            current = ""
        if len(paragraph) <= limit:
            current = paragraph
            continue
        # Párrafo largo: intentar partir por líneas antes de corte duro
        for line in paragraph.split("\n"):
            cand_line = f"{current}\n{line}" if current else line
            if len(cand_line) <= limit:
                current = cand_line
                continue
            if current:
                raw_chunks.append(current)
                current = ""
            while len(line) > limit:
                raw_chunks.append(line[:limit])
                line = line[limit:]
            current = line

    if current:
        raw_chunks.append(current)

    if not raw_chunks:
        return [text[:limit]]

    # Si el texto contiene etiquetas HTML, asegurar balanceo en cada fragmento
    if "<" in text and ">" in text:
        safe_chunks = []
        for chunk in raw_chunks:
            sanitized = sanitize_telegram_html(chunk)
            safe_chunks.append(sanitized if len(sanitized) <= limit + 64 else strip_html(chunk)[:limit])
        return safe_chunks

    return raw_chunks
