"""Test dell'azione «aerei in volo».

Nessuna richiesta reale: OpenSky ha un limite di frequenza stretto e i test
devono poter girare mille volte di seguito. Le risposte sono finte, ma nella
forma esatta documentata dall'API (il vettore di stato è una lista posizionale).
"""
from __future__ import annotations

import unittest
from unittest import mock

from actions import live_aircraft as la


def _state(icao, callsign, lon, lat, alt=10000.0, vel=250.0, trk=90.0,
           vert=0.0, ground=False, country="Italy"):
    """Un vettore di stato OpenSky: 17 campi posizionali."""
    return [icao, callsign, country, 0, 0, lon, lat, alt, ground, vel, trk,
            vert, None, alt, None, False, 0]


class LiveAircraftTests(unittest.TestCase):
    def setUp(self):
        la._cache.clear()
        la._geo_cache.clear()
        la._last_call = 0.0
        # Roma è nella tabella locale: nessuna geocodifica di rete nei test.
        self.roma = (41.9028, 12.4964)

    # ── distanza e filtro circolare ──────────────────────────────────────────

    def test_corners_of_the_box_are_discarded(self):
        # Il riquadro interrogato è quadrato ma il raggio è un cerchio: un
        # aereo nell'angolo è dentro il riquadro e fuori dal raggio.
        lat, lon = self.roma
        vicino = _state("a1", "AZA1", lon, lat + 0.2)           # ~22 km
        angolo = _state("a2", "AZA2", lon + 0.55, lat + 0.55)   # ~70 km in diagonale
        out = la._parse([vicino, angolo], lat, lon, 50.0)
        self.assertEqual([a["callsign"] for a in out], ["AZA1"])

    def test_results_are_sorted_by_distance(self):
        lat, lon = self.roma
        states = [_state("a1", "LONTANO", lon, lat + 0.4),
                  _state("a2", "VICINO", lon, lat + 0.05)]
        out = la._parse(states, lat, lon, 100.0)
        self.assertEqual([a["callsign"] for a in out], ["VICINO", "LONTANO"])
        self.assertLess(out[0]["km"], out[1]["km"])

    def test_malformed_state_does_not_break_the_answer(self):
        lat, lon = self.roma
        rotto = ["x", "ROTTO"]                      # vettore troncato
        senza_posizione = _state("a3", "NOPOS", None, None)
        buono = _state("a4", "BUONO", lon, lat + 0.05)
        out = la._parse([rotto, senza_posizione, buono], lat, lon, 100.0)
        self.assertEqual([a["callsign"] for a in out], ["BUONO"])

    # ── limiti di frequenza ──────────────────────────────────────────────────

    def test_second_call_is_served_from_cache(self):
        lat, lon = self.roma
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"states": [_state("a1", "AZA1", lon, lat)]}
        with mock.patch.object(la.requests, "get", return_value=fake) as get:
            la._fetch_states(lat, lon, 60)
            la._fetch_states(lat, lon, 60)
        # Una sola richiesta: chiedere due volte di fila non consuma due crediti.
        self.assertEqual(get.call_count, 1)

    def test_rate_limit_error_is_reported_not_swallowed(self):
        lat, lon = self.roma
        fake = mock.Mock(status_code=429)
        with mock.patch.object(la.requests, "get", return_value=fake):
            with self.assertRaises(RuntimeError):
                la._fetch_states(lat, lon, 60)

    # ── risposta parlata ─────────────────────────────────────────────────────

    def test_spoken_answer_mentions_count_and_nearest(self):
        lat, lon = self.roma
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"states": [
            _state("a1", "AZA100", lon, lat + 0.05, alt=3200.0, vel=180.0),
            _state("a2", "RYR22", lon, lat + 0.3),
        ]}
        with mock.patch.object(la.requests, "get", return_value=fake):
            msg = la.live_aircraft({"location": "Roma", "radius_km": 60})
        self.assertIn("2 velivoli", msg)
        self.assertIn("AZA100", msg)
        self.assertIn("Roma", msg)

    def test_empty_sky_is_not_confused_with_no_coverage(self):
        lat, lon = self.roma
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"states": []}
        with mock.patch.object(la.requests, "get", return_value=fake):
            msg = la.live_aircraft({"location": "Roma"})
        self.assertIn("Nessun aereo", msg)
        self.assertIn("ricevitori", msg)      # l'altra spiegazione possibile

    def test_no_destination_is_ever_invented(self):
        # Il transponder non trasmette partenza e destinazione: se comparissero
        # nella risposta sarebbero inventate.
        lat, lon = self.roma
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"states": [_state("a1", "AZA100", lon, lat)]}
        with mock.patch.object(la.requests, "get", return_value=fake):
            msg = la.live_aircraft({"location": "Roma"}).lower()
        for parola in ("destinazione", "partenza", "atterra", "decolla"):
            self.assertNotIn(parola, msg)

    def test_missing_place_asks_instead_of_guessing(self):
        with mock.patch.object(la, "_config", return_value={}):
            msg = la.live_aircraft({})
        self.assertIn("quale città", msg.lower())

    def test_home_city_is_used_when_no_place_given(self):
        lat, lon = self.roma
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"states": [_state("a1", "AZA100", lon, lat)]}
        with mock.patch.object(la, "_config", return_value={"home_city": "Roma"}), \
             mock.patch.object(la.requests, "get", return_value=fake):
            msg = la.live_aircraft({})
        self.assertIn("Roma", msg)

    def test_network_failure_is_explained_not_crashed(self):
        with mock.patch.object(la.requests, "get",
                               side_effect=OSError("rete assente")):
            msg = la.live_aircraft({"location": "Roma"})
        self.assertIn("Non riesco", msg)

    # ── contratto dello strumento ────────────────────────────────────────────

    def test_tool_declaration_is_wired(self):
        self.assertEqual(la.TOOL["name"], "live_aircraft")
        self.assertIs(la.TOOL["handler"], la.live_aircraft)
        self.assertIn("flight_finder", la.TOOL["description"])   # niente confusione


if __name__ == "__main__":
    unittest.main()
