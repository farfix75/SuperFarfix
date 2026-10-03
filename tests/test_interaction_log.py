"""Test del registro strutturato degli scambi."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import interaction_log
from core.interaction_log import InteractionLog


class InteractionLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)
        self._patch = mock.patch.object(interaction_log, "LOG_DIR", self._dir)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def _records(self):
        out = []
        for f in sorted(self._dir.glob("*.jsonl")):
            out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        return out

    def test_exchange_is_written_with_its_fields(self):
        log = InteractionLog(); log.start_session("modello-x")
        log.tool_used("open_app", ok=True, ms=12.0)
        log.exchange("apri chrome", "Fatto.")
        r, = self._records()
        self.assertEqual(r["user"], "apri chrome")
        self.assertEqual(r["assistant"], "Fatto.")
        self.assertEqual(r["model"], "modello-x")
        self.assertEqual(r["tools"][0]["name"], "open_app")

    def test_latency_is_measured_from_the_last_speech_tick(self):
        # La fine del turno è l'ultimo frammento di trascrizione, non il primo:
        # misurare dal primo gonfierebbe il tempo di tutta la durata della frase.
        log = InteractionLog()
        log.user_stopped_speaking()
        time.sleep(0.15)
        log.user_stopped_speaking()      # l'utente stava ancora parlando
        time.sleep(0.05)
        log.first_audio_received()
        log.exchange("ciao", "Ciao.")
        r, = self._records()
        self.assertLess(r["reply_ms"], 120)

    def test_nonsensical_latency_is_omitted_rather_than_guessed(self):
        log = InteractionLog()
        log.first_audio_received()       # audio prima del parlato: turni diversi
        time.sleep(0.02)
        log.user_stopped_speaking()
        log.exchange("ciao", "Ciao.")
        r, = self._records()
        self.assertNotIn("reply_ms", r)

    def test_secrets_are_redacted(self):
        log = InteractionLog()
        log.exchange("la chiave è sk-abcdefghijklmnopqrst1234", "ok")
        r, = self._records()
        self.assertNotIn("sk-abcdefghijklmnopqrst1234", r["user"])
        self.assertIn("[RIMOSSO]", r["user"])

    def test_empty_technical_turns_are_not_recorded(self):
        log = InteractionLog()
        log.exchange("", "")
        self.assertEqual(self._records(), [])

    def test_disabled_writes_nothing(self):
        log = InteractionLog(enabled=False)
        log.user_stopped_speaking(); log.first_audio_received()
        log.exchange("ciao", "Ciao.")
        self.assertFalse(list(self._dir.glob("*.jsonl")))

    def test_turn_state_does_not_leak_into_the_next_turn(self):
        log = InteractionLog()
        log.tool_used("a", ok=True, ms=1.0)
        log.interrupted("voice")
        log.exchange("uno", "risposta uno")
        log.exchange("due", "risposta due")
        first, second = self._records()
        self.assertIn("tools", first)
        self.assertNotIn("tools", second)
        self.assertNotIn("interrupted", second)

    def test_a_broken_line_does_not_destroy_the_archive(self):
        log = InteractionLog()
        log.exchange("uno", "risposta")
        path = next(self._dir.glob("*.jsonl"))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"ts": "interrotto a met\n')   # riga troncata
        log.exchange("due", "risposta")
        recuperati = list(interaction_log._iter_records())
        self.assertEqual(len(recuperati), 2)

    def test_logging_never_breaks_the_conversation(self):
        # Se il disco è pieno o la cartella non è scrivibile, l'assistente deve
        # continuare a rispondere: questi dati valgono meno del servizio.
        log = InteractionLog()
        with mock.patch.object(InteractionLog, "_append",
                               side_effect=OSError("disco pieno")):
            log.exchange("ciao", "Ciao.")      # non deve sollevare


if __name__ == "__main__":
    unittest.main()
