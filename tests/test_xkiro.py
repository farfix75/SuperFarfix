"""Test del provider xKiro (gateway compatibile OpenAI).

Il gateway viene simulato in locale: i test verificano la forma della
richiesta — percorso, intestazione di autorizzazione, identificativo del
modello — senza toccare la rete né consumare credito.
"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from core import ai_router


class _Handler(BaseHTTPRequestHandler):
    received: dict = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _Handler.received = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": json.loads(self.rfile.read(n)),
        }
        self._json({"choices": [{"message": {"content": "Risposta da xKiro."}}]})

    def do_GET(self):
        self._json({"data": [{"id": "openai/gpt-5.6-sol"},
                             {"id": "anthropic/claude-x"}]})

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class XkiroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.cfg = dict(ai_router.DEFAULTS, xkiro_url=self.base,
                        xkiro_model="openai/gpt-5.6-sol", timeout=10)
        self._key = mock.patch.object(
            ai_router, "_key",
            lambda name: "sk-xt-finta" if name == "XKIRO_API_KEY" else "")
        self._key.start()
        self.addCleanup(self._key.stop)

    def test_request_shape(self):
        answer = ai_router._xkiro("ciao", "sistema", None, self.cfg)
        self.assertEqual(answer, "Risposta da xKiro.")
        self.assertEqual(_Handler.received["path"], "/v1/chat/completions")
        self.assertEqual(_Handler.received["auth"], "Bearer sk-xt-finta")
        self.assertEqual(_Handler.received["body"]["model"], "openai/gpt-5.6-sol")

    def test_bare_model_name_gives_a_readable_error(self):
        # Il gateway risponderebbe 404 not_found, che sembra un modello
        # inesistente: l'errore vero è il prefisso mancante.
        with self.assertRaises(RuntimeError) as ctx:
            ai_router._xkiro("ciao", None, None,
                             dict(self.cfg, xkiro_model="gpt-5.6-sol"))
        self.assertIn("vendor/modello", str(ctx.exception))

    def test_missing_key_is_reported(self):
        with mock.patch.object(ai_router, "_key", lambda name: ""):
            with self.assertRaises(RuntimeError):
                ai_router._xkiro("ciao", None, None, self.cfg)

    def test_catalog_listing(self):
        with mock.patch.object(ai_router, "get_config", lambda: self.cfg):
            self.assertEqual(ai_router.list_xkiro_models(),
                             ["anthropic/claude-x", "openai/gpt-5.6-sol"])

    def test_provider_is_registered_everywhere(self):
        self.assertIn("xkiro", ai_router.PROVIDERS)
        self.assertIn("xkiro", ai_router._order("auto"))
        # Se lo scegli esplicitamente deve essere il primo tentativo.
        self.assertEqual(ai_router._order("xkiro")[0], "xkiro")

    def test_health_reports_xkiro(self):
        status = ai_router.health()
        self.assertIn("xkiro", status)
        self.assertTrue(status["xkiro"]["configured"])
