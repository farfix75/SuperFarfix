"""Test della vista radar.

Serve un QApplication anche solo per disegnare fuori schermo: QImage e
QPainter appartengono al modulo GUI di Qt. La piattaforma «offscreen» lo rende
possibile su un computer senza schermo, come quello dell'integrazione continua.
"""
from __future__ import annotations

import math
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    from core.map_view import render_scope, scope_html, _project
    _QT = True
except Exception:                                   # pragma: no cover
    _QT = False


@unittest.skipUnless(_QT, "PyQt6 non disponibile")
class MapViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    # ── proiezione ───────────────────────────────────────────────────────────

    def test_north_is_up_and_east_is_right(self):
        size = 400
        centro = size / 2
        nord = _project(0, 50, 100, size)
        est = _project(90, 50, 100, size)
        self.assertAlmostEqual(nord.x(), centro, delta=0.5)
        self.assertLess(nord.y(), centro)          # più in alto
        self.assertGreater(est.x(), centro)        # più a destra
        self.assertAlmostEqual(est.y(), centro, delta=0.5)

    def test_distance_is_proportional_not_arbitrary(self):
        size = 400
        centro = size / 2
        vicino = _project(90, 25, 100, size)
        lontano = _project(90, 50, 100, size)
        d1 = vicino.x() - centro
        d2 = lontano.x() - centro
        # Il doppio della distanza reale = il doppio dal centro.
        self.assertAlmostEqual(d2 / d1, 2.0, delta=0.05)

    # ── disegno ──────────────────────────────────────────────────────────────

    def test_renders_an_image_of_the_requested_size(self):
        img = render_scope([{"km": 10, "bearing": 45}], radius_km=60,
                           centre_label="prova", size=300)
        self.assertEqual(img.width(), 300)
        self.assertEqual(img.height(), 300)

    def test_target_beyond_the_radius_is_not_drawn_outside_the_scope(self):
        # Un bersaglio oltre il raggio finirebbe fuori dai cerchi, dando
        # l'impressione che sia dentro l'area mostrata.
        vuoto = render_scope([], radius_km=60, centre_label="x", size=300)
        fuori = render_scope([{"km": 5000, "bearing": 45, "label": "LONTANO"}],
                             radius_km=60, centre_label="x", size=300)
        self.assertEqual(vuoto.constBits().asstring(vuoto.sizeInBytes()),
                         fuori.constBits().asstring(fuori.sizeInBytes()))

    def test_empty_target_list_still_draws_the_scope(self):
        img = render_scope([], radius_km=60, centre_label="nessun contatto",
                           size=300)
        self.assertFalse(img.isNull())

    def test_malformed_target_does_not_crash_the_drawing(self):
        # I dati arrivano da servizi esterni: un valore inatteso è questione di
        # quando, non di se. Un bersaglio sporco deve saltare se stesso, e
        # soprattutto non deve lasciare il pittore aperto — Qt in quel caso
        # abbatte l'intero processo, non solo la mappa.
        for cattivo in ([{"km": "non-un-numero"}], [None], [{}],
                        [{"km": 5, "bearing": None}], [{"km": None}]):
            img = render_scope(cattivo + [{"km": 10, "bearing": 45}],
                               radius_km=60, centre_label="x", size=300)
            self.assertFalse(img.isNull())

    # ── conversione in HTML ──────────────────────────────────────────────────

    def test_html_embeds_the_image_without_a_temporary_file(self):
        img = render_scope([{"km": 10, "bearing": 0}], radius_km=60,
                           centre_label="x", size=200)
        html = scope_html(img, "didascalia")
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("didascalia", html)
        self.assertNotIn("file://", html)
        # Se il buffer perdesse il riferimento all'array, qui il programma
        # morirebbe con un segmentation fault invece di fallire un'asserzione.
        self.assertGreater(len(html), 1000)

    def test_html_survives_repeated_calls(self):
        # Il guasto da riferimento perduto si manifesta in modo saltuario:
        # ripetere l'operazione lo fa emergere.
        img = render_scope([{"km": 1, "bearing": 10}], radius_km=10,
                           centre_label="x", size=120)
        for _ in range(25):
            self.assertIn("base64", scope_html(img))


if __name__ == "__main__":
    unittest.main()
