"""Test del globo terrestre disegnato."""
from __future__ import annotations

import math
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    from core import globe_view as gv
    _QT = True
except Exception:                                    # pragma: no cover
    _QT = False


@unittest.skipUnless(_QT, "PyQt6 non disponibile")
class GlobeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    # ── dati delle coste ─────────────────────────────────────────────────────

    def test_coastlines_are_present_and_plausible(self):
        anelli = gv._coastlines()
        self.assertGreater(len(anelli), 50)
        for lon, lat in anelli[0][:20]:
            self.assertTrue(-180.5 <= lon <= 180.5, lon)
            self.assertTrue(-90.5 <= lat <= 90.5, lat)

    # ── proiezione ───────────────────────────────────────────────────────────

    def test_far_side_of_the_planet_is_not_drawn(self):
        # Centrato su Roma, gli antipodi (Pacifico meridionale) sono dietro la
        # sfera: disegnarli significherebbe vedere attraverso la Terra.
        proj = gv._Ortho(41.9, 12.5, 200, 1.0, 200, 200)
        self.assertTrue(proj(41.9, 12.5)[2])            # il centro si vede
        self.assertFalse(proj(-41.9, -167.5)[2])        # gli antipodi no

    def test_centre_projects_to_the_middle(self):
        proj = gv._Ortho(41.9, 12.5, 200, 1.0, 200, 200)
        x, y, _ = proj(41.9, 12.5)
        self.assertAlmostEqual(x, 200, delta=0.5)
        self.assertAlmostEqual(y, 200, delta=0.5)

    def test_north_of_centre_is_drawn_higher(self):
        proj = gv._Ortho(41.9, 12.5, 200, 1.0, 200, 200)
        _, y_centro, _ = proj(41.9, 12.5)
        _, y_nord, _ = proj(46.9, 12.5)
        self.assertLess(y_nord, y_centro)

    def test_zoom_is_capped_to_what_the_data_supports(self):
        # Le coste sono semplificate a ~30 km: ingrandire su 5 km mostrerebbe
        # un poligono gigante, non dettaglio.
        stretto = gv._zoom_for(5)
        soglia = gv._zoom_for(gv._MIN_RADIUS_KM)
        self.assertAlmostEqual(stretto, soglia, places=6)
        self.assertEqual(gv._zoom_for(None), 1.0)      # globo intero
        self.assertGreater(gv._zoom_for(400), 1.0)

    # ── disegno ──────────────────────────────────────────────────────────────

    def test_renders_requested_size(self):
        img = gv.render_globe([], centre_lat=41.9, centre_lon=12.5, size=300)
        self.assertEqual((img.width(), img.height()), (300, 300))

    def test_target_on_the_far_side_changes_nothing(self):
        vuoto = gv.render_globe([], centre_lat=41.9, centre_lon=12.5, size=260)
        nascosto = gv.render_globe(
            [{"lat": -41.9, "lon": -167.5, "kind": "aircraft", "label": "X"}],
            centre_lat=41.9, centre_lon=12.5, size=260)
        self.assertEqual(vuoto.constBits().asstring(vuoto.sizeInBytes()),
                         nascosto.constBits().asstring(nascosto.sizeInBytes()))

    def test_visible_target_does_change_the_image(self):
        vuoto = gv.render_globe([], centre_lat=41.9, centre_lon=12.5, size=260)
        con = gv.render_globe(
            [{"lat": 42.0, "lon": 12.6, "kind": "aircraft", "heading": 90}],
            centre_lat=41.9, centre_lon=12.5, size=260)
        self.assertNotEqual(vuoto.constBits().asstring(vuoto.sizeInBytes()),
                            con.constBits().asstring(con.sizeInBytes()))

    def test_malformed_targets_never_kill_the_process(self):
        # Un'eccezione con il pittore aperto fa abbattere il processo da Qt:
        # non è una mappa mancata, è la sessione persa.
        for cattivo in ([None], [{}], [{"lat": "x", "lon": 1}],
                        [{"lat": 1}], [{"lat": 1, "lon": 2, "size": "grande"}],
                        [{"lat": 1, "lon": 2, "kind": "aircraft",
                          "heading": "nord"}]):
            img = gv.render_globe(cattivo + [{"lat": 42.0, "lon": 12.6}],
                                  centre_lat=41.9, centre_lon=12.5, size=200)
            self.assertFalse(img.isNull())

    # ── HTML ─────────────────────────────────────────────────────────────────

    def test_html_embeds_the_image(self):
        img = gv.render_globe([{"lat": 42.0, "lon": 12.6}],
                              centre_lat=41.9, centre_lon=12.5, size=200)
        html = gv.globe_html(img, "didascalia")
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("didascalia", html)

    def test_html_survives_repetition(self):
        img = gv.render_globe([], centre_lat=0, centre_lon=0, size=120)
        for _ in range(25):
            self.assertIn("base64", gv.globe_html(img))


if __name__ == "__main__":
    unittest.main()
