"""Test delle azioni su dati pubblici: terremoti, meteo, spazio.

Nessuna richiesta reale. Le risposte sono finte ma nella forma esatta che
restituiscono i servizi veri, e i limiti di frequenza sono verificati contando
le chiamate: un'azione che li ignora fa bloccare l'indirizzo IP dell'utente.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from core import public_data as pd
from actions import earthquakes as eq
from actions import weather_now as wn
from actions import space_watch as sw


def _fake(json_obj, status=200):
    m = mock.Mock(status_code=status)
    m.json.return_value = json_obj
    return m


class PublicDataTests(unittest.TestCase):
    def setUp(self):
        pd.reset_state()

    def test_known_city_needs_no_network(self):
        with mock.patch.object(pd.requests, "get") as get:
            self.assertEqual(pd.geocode("Roma")[2], "Roma")
            get.assert_not_called()

    def test_repeated_call_is_cached(self):
        with mock.patch.object(pd.requests, "get",
                               return_value=_fake({"ok": 1})) as get:
            pd.http_json("https://example.com/a", cache_ttl=60)
            pd.http_json("https://example.com/a", cache_ttl=60)
        self.assertEqual(get.call_count, 1)

    def test_network_failure_returns_none_not_exception(self):
        with mock.patch.object(pd.requests, "get", side_effect=OSError("giù")):
            self.assertIsNone(pd.http_json("https://example.com/b"))

    def test_rate_limited_response_does_not_poison_the_cache(self):
        with mock.patch.object(pd.requests, "get", return_value=_fake({}, 429)):
            self.assertIsNone(pd.http_json("https://example.com/c"))

    def test_first_call_after_boot_is_not_refused(self):
        # time.monotonic() conta dall'avvio del sistema: nei primi minuti dopo
        # l'accensione vale poche decine di secondi. Con 0.0 come valore di
        # ripiego, una fonte con limite di 4 minuti rifiutava la prima
        # richiesta in assoluto — proprio quella dell'utente appena acceso.
        with mock.patch.object(pd.requests, "get",
                               return_value=_fake({"ok": 1})) as get:
            esito = pd.http_json("https://fresh.example.com/x",
                                 min_interval=240.0)
        self.assertEqual(esito, {"ok": 1})
        self.assertEqual(get.call_count, 1)

    def test_a_long_rate_limit_never_freezes_the_answer(self):
        # Launch Library consente ~15 richieste all'ora: se il limitatore
        # dormisse per rispettarlo, l'assistente resterebbe muto per minuti.
        import time as _t
        with mock.patch.object(pd.requests, "get", return_value=_fake({"ok": 1})):
            pd.http_json("https://slow.example.com/x", min_interval=240.0)
            inizio = _t.monotonic()
            esito = pd.http_json("https://slow.example.com/y", min_interval=240.0)
            durata = _t.monotonic() - inizio
        self.assertIsNone(esito)          # rinuncia invece di aspettare
        self.assertLess(durata, 2.0)      # e lo fa subito

    def test_distance_and_bearing(self):
        # Milano è circa 480 km a nord-ovest di Roma.
        km = pd.haversine_km(41.9028, 12.4964, 45.4642, 9.1900)
        self.assertTrue(440 < km < 520, km)
        self.assertIn("nord", pd.compass_from(41.9028, 12.4964, 45.4642, 9.1900))


class EarthquakeTests(unittest.TestCase):
    def setUp(self):
        pd.reset_state()

    def _quake(self, mag, place, lat, lon, minuti_fa=10, tsunami=0, prof=10):
        t = datetime.now(timezone.utc) - timedelta(minutes=minuti_fa)
        return {"properties": {"mag": mag, "place": place,
                               "time": int(t.timestamp() * 1000),
                               "tsunami": tsunami},
                "geometry": {"coordinates": [lon, lat, prof]}}

    def test_reports_most_recent_and_strongest(self):
        payload = {"features": [
            self._quake(3.1, "Norcia", 42.79, 13.09, minuti_fa=5),
            self._quake(5.4, "Creta", 35.2, 25.1, minuti_fa=300),
        ]}
        with mock.patch.object(pd.requests, "get", return_value=_fake(payload)):
            msg = eq.earthquakes({"hours": 24})
        self.assertIn("2 terremoti", msg)
        self.assertIn("Norcia", msg)        # il più recente
        self.assertIn("5.4", msg)           # il più forte

    def test_magnitude_gets_a_plain_language_judgement(self):
        payload = {"features": [self._quake(6.3, "Cile", -33.4, -70.6)]}
        with mock.patch.object(pd.requests, "get", return_value=_fake(payload)):
            msg = eq.earthquakes({})
        self.assertIn("danni", msg.lower())

    def test_quiet_period_is_stated_clearly(self):
        with mock.patch.object(pd.requests, "get",
                               return_value=_fake({"features": []})):
            msg = eq.earthquakes({"location": "Roma", "hours": 6})
        self.assertIn("Nessun terremoto", msg)
        self.assertIn("Roma", msg)

    def test_distance_is_given_when_a_place_is_named(self):
        payload = {"features": [self._quake(4.0, "Norcia", 42.79, 13.09)]}
        with mock.patch.object(pd.requests, "get", return_value=_fake(payload)):
            msg = eq.earthquakes({"location": "Roma"})
        self.assertIn("km", msg)

    def test_broken_feature_is_skipped_not_fatal(self):
        payload = {"features": [{"properties": {}}, {"nonsense": True},
                                self._quake(4.0, "Norcia", 42.79, 13.09)]}
        with mock.patch.object(pd.requests, "get", return_value=_fake(payload)):
            msg = eq.earthquakes({})
        self.assertIn("Norcia", msg)


class WeatherNowTests(unittest.TestCase):
    def setUp(self):
        pd.reset_state()

    def _payload(self, code=3, temp=18.0, perc=18.5, vento=14.0, raffica=20.0,
                 pioggia=0.0):
        return {"current": {"weather_code": code, "temperature_2m": temp,
                            "apparent_temperature": perc,
                            "relative_humidity_2m": 60,
                            "precipitation": pioggia, "wind_speed_10m": vento,
                            "wind_direction_10m": 225, "wind_gusts_10m": raffica}}

    def test_speaks_conditions_in_words_not_codes(self):
        with mock.patch.object(pd.requests, "get",
                               return_value=_fake(self._payload())):
            msg = wn.weather_now({"location": "Roma"})
        self.assertIn("coperto", msg)          # codice 3 tradotto
        self.assertNotIn("weather_code", msg)
        self.assertIn("Roma", msg)

    def test_apparent_temperature_only_when_it_differs(self):
        with mock.patch.object(pd.requests, "get",
                               return_value=_fake(self._payload(temp=18, perc=18.4))):
            uguale = wn.weather_now({"location": "Roma"})
        pd.reset_state()
        with mock.patch.object(pd.requests, "get",
                               return_value=_fake(self._payload(temp=32, perc=38))):
            diverso = wn.weather_now({"location": "Roma"})
        self.assertNotIn("percepit", uguale)
        self.assertIn("percepiti 38", diverso)

    def test_wind_is_described_not_just_numbered(self):
        with mock.patch.object(pd.requests, "get",
                               return_value=_fake(self._payload(vento=60, raffica=95))):
            msg = wn.weather_now({"location": "Roma"})
        self.assertIn("vento forte", msg.lower())
        self.assertIn("raffiche", msg)

    def test_unknown_place_is_reported(self):
        with mock.patch.object(pd, "geocode", return_value=None):
            msg = wn.weather_now({"location": "Atlantide"})
        self.assertIn("non", msg.lower())


class SpaceTests(unittest.TestCase):
    def setUp(self):
        pd.reset_state()

    def test_iss_position_and_distance(self):
        payload = {"latitude": 42.0, "longitude": 12.0, "altitude": 420.5,
                   "velocity": 27600.0, "visibility": "daylight"}
        with mock.patch.object(pd.requests, "get", return_value=_fake(payload)):
            msg = sw.space_watch({"what": "iss", "location": "Roma"})
        self.assertIn("420", msg)
        self.assertIn("Roma", msg)

    def test_iss_never_invents_a_pass_time(self):
        # Prevedere i passaggi richiede la propagazione orbitale: se comparisse
        # un orario sarebbe inventato.
        payload = {"latitude": 42.0, "longitude": 12.0, "altitude": 420.0,
                   "velocity": 27600.0}
        with mock.patch.object(pd.requests, "get", return_value=_fake(payload)):
            msg = sw.space_watch({"what": "iss", "location": "Roma"}).lower()
        for parola in ("passerà", "passaggio alle", "sarà visibile alle"):
            self.assertNotIn(parola, msg)

    def test_launches_list(self):
        fra_tre_giorni = (datetime.now(timezone.utc)
                          + timedelta(days=3, hours=2)).isoformat().replace("+00:00", "Z")
        payload = {"results": [
            {"name": "Falcon 9 | Starlink", "net": fra_tre_giorni,
             "status": {"abbrev": "Go"}},
            {"name": "Ariane 6 | test", "net": fra_tre_giorni,
             "status": {"abbrev": "TBD"}},
        ]}
        with mock.patch.object(pd.requests, "get", return_value=_fake(payload)):
            msg = sw.space_watch({"what": "launches"})
        self.assertIn("Falcon 9", msg)
        self.assertIn("fra 3 giorni", msg)
        self.assertIn("NET", msg)          # avvisa che le date slittano

    def test_launch_rate_limit_is_explained(self):
        with mock.patch.object(pd.requests, "get", side_effect=OSError("giù")):
            msg = sw.space_watch({"what": "launches"})
        self.assertIn("richieste", msg)


class ToolContractTests(unittest.TestCase):
    def test_all_tools_declare_handler_and_distinct_names(self):
        tools = [eq.TOOL, wn.TOOL, sw.TOOL]
        nomi = [t["name"] for t in tools]
        self.assertEqual(len(nomi), len(set(nomi)))
        for t in tools:
            self.assertTrue(callable(t["handler"]))
            self.assertIn("parameters", t)

    def test_weather_tools_tell_the_model_them_apart(self):
        # Due strumenti meteo: la descrizione deve dire quando usare quale,
        # altrimenti il modello sceglie a caso.
        self.assertIn("weather_report", wn.TOOL["description"])


if __name__ == "__main__":
    unittest.main()
