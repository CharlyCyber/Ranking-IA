"""Utilidades del bot: splitting de mensajes y rate limit por usuario."""

from datetime import datetime, timedelta

import src.main as m


class TestSplitMessage:
    def test_mensaje_corto_intacto(self):
        assert m.split_message("hola") == ["hola"]

    def test_menaje_largo_se_parte(self):
        text = ("párrafo largo. " * 400).strip() + "\n\n" + "segundo párrafo"
        chunks = m.split_message(text, limit=4000)
        assert all(len(c) <= 4000 for c in chunks)
        assert len(chunks) > 1

    def test_piezas_forman_el_original(self):
        original = ("línea de contenido. " * 500).strip()
        chunks = m.split_message(original, limit=1000)
        assert " ".join(chunks).replace(" ", "") == original.replace(" ", "")

    def test_parrafo_gigante_corte_duro(self):
        chunks = m.split_message("x" * 9000, limit=4000)
        assert all(len(c) <= 4000 for c in chunks)
        assert "".join(chunks) == "x" * 9000


class TestRateLimit:
    def test_ventana_por_usuario(self):
        uid = 999001
        assert sum(m.allow_user_message(uid) for _ in range(25)) == m.USER_RATE_LIMIT
        assert m.allow_user_message(uid) is False
        # otro usuario no afecta
        assert m.allow_user_message(999002) is True

    def test_ventana_expira_tras_1h(self):
        uid = 999003
        for _ in range(m.USER_RATE_LIMIT):
            assert m.allow_user_message(uid)
        assert m.allow_user_message(uid) is False
        # expira la ventana: envejece los timestamps manualmente
        m._user_messages[uid] = deque_with_old_timestamp()
        assert m.allow_user_message(uid) is True


def deque_with_old_timestamp():
    from collections import deque

    old = datetime.now() - timedelta(hours=2)
    return deque([old] * m.USER_RATE_LIMIT)


class TestStripHtml:
    def test_quita_etiquetas_y_decodifica(self):
        assert m._strip_html("<b>Hola</b> &amp; <i>mundo</i>") == "Hola & mundo"
