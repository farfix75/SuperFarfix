"""Verifica la correzione di core/win_subprocess.py.

Il guasto originale: su Windows in italiano i programmi da console scrivono in
cp850, Python li leggeva in cp1252, e un byte come 0x8d — presente in quasi
ogni messaggio accentato di `netsh` — faceva fallire la decodifica dentro un
thread interno di `subprocess`, fuori dalla portata di qualunque try/except.

La correzione vale solo su Windows, ma è verificabile ovunque: qui si forza
l'attivazione e si dà in pasto al decodificatore un byte non valido.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

from core import win_subprocess


def _emit_bad_byte():
    """Comando che stampa un byte non valido né in UTF-8 né in cp1252."""
    return [sys.executable, "-c",
            "import sys; sys.stdout.buffer.write(b'ok\\x8dfine')"]


class WinSubprocessTests(unittest.TestCase):
    def setUp(self):
        self._original_popen = subprocess.Popen
        win_subprocess._PATCHED = False

    def tearDown(self):
        subprocess.Popen = self._original_popen
        win_subprocess._subprocess.Popen = self._original_popen
        win_subprocess._PATCHED = False

    def _install_as_if_on_windows(self):
        # CREATE_NO_WINDOW non esiste fuori da Windows: va simulato, altrimenti
        # non è la correzione a fallire ma il test stesso.
        self._ctx = mock.patch.object(subprocess, "CREATE_NO_WINDOW", 0,
                                      create=True)
        self._ctx.start()
        self.addCleanup(self._ctx.stop)
        with mock.patch.object(win_subprocess._sys, "platform", "win32"):
            win_subprocess.install()

    def test_undecodable_output_is_fatal_without_the_fix(self):
        # La premessa: senza la correzione quel byte fa saltare la lettura.
        with self.assertRaises(UnicodeDecodeError):
            subprocess.run(_emit_bad_byte(), capture_output=True, text=True,
                           encoding="utf-8", timeout=30)

    def test_text_output_survives_a_bad_byte(self):
        self._install_as_if_on_windows()
        result = subprocess.run(_emit_bad_byte(), capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        # Il testo leggibile arriva comunque; il byte rotto diventa «\ufffd».
        self.assertIn("ok", result.stdout)
        self.assertIn("fine", result.stdout)

    def test_explicit_errors_argument_wins(self):
        # La correzione fornisce un valore di ripiego, non impone il proprio.
        self._install_as_if_on_windows()
        with self.assertRaises(UnicodeDecodeError):
            subprocess.run(_emit_bad_byte(), capture_output=True, text=True,
                           encoding="utf-8", errors="strict", timeout=30)

    def test_binary_mode_is_untouched(self):
        self._install_as_if_on_windows()
        result = subprocess.run(_emit_bad_byte(), capture_output=True,
                                timeout=30)
        self.assertEqual(result.stdout, b"ok\x8dfine")

    def test_install_is_idempotent(self):
        self._install_as_if_on_windows()
        after_first = subprocess.Popen
        with mock.patch.object(win_subprocess._sys, "platform", "win32"):
            win_subprocess.install()
        self.assertIs(subprocess.Popen, after_first)

    def test_no_effect_outside_windows(self):
        win_subprocess.install()          # piattaforma reale: Linux/macOS
        self.assertIs(subprocess.Popen, self._original_popen)


if __name__ == "__main__":
    unittest.main()
