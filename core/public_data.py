"""Fondamenta condivise per le azioni che leggono dati pubblici.

Perché esiste
------------
Ogni fonte pubblica (aerei, terremoti, meteo, spazio) ha bisogno delle stesse
tre cose: trasformare «Roma» in coordinate, fare una richiesta HTTP senza
esagerare con la frequenza, e misurare una distanza. Scriverle quattro volte
significa correggere quattro volte lo stesso errore.

Le due regole che contano
-------------------------
1. **Limite di frequenza per servizio.** Ogni API pubblica ne ha uno, e sono
   diversi fra loro: OpenSky vuole una richiesta ogni 5 secondi, Launch
   Library circa 15 all'ora, Nominatim una al secondo. Superarli non dà un
   errore chiaro: dà 429 e nessun dato, oppure il blocco dell'indirizzo IP.
   `http_json` tiene un orologio separato per ogni host.
2. **Cache breve.** Chiedere due volte di fila «e adesso?» non deve consumare
   due richieste. I dati restano validi per il tempo che ha senso per quella
   fonte: pochi secondi per gli aerei, minuti per i terremoti, un'ora per i
   lanci spaziali.

Nessuna di queste funzioni solleva eccezioni per problemi di rete: restituisce
`None`, e chi chiama decide cosa dire all'utente. Un assistente vocale che si
zittisce per un timeout è peggio di uno che dice «non riesco a leggerlo adesso».
"""

from __future__ import annotations

import math
import threading
import time

import requests

__all__ = ["geocode", "http_json", "haversine_km", "bearing_word",
           "compass_from", "KNOWN_CITIES"]

UA = "SuperFarfix/1.0 (assistente vocale personale)"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"

# Quanto si è disposti ad attendere dentro una risposta vocale.
_MAX_WAIT = 1.5

_lock = threading.Lock()
_last_call: dict[str, float] = {}
_cache: dict[tuple, tuple[float, object]] = {}
_geo_cache: dict[str, tuple[float, float, str]] = {}

# Città più comuni, per evitare un viaggio di rete sul caso frequente — e per
# far funzionare le prove senza rete.
KNOWN_CITIES = {
    "roma": (41.9028, 12.4964), "milano": (45.4642, 9.1900),
    "napoli": (40.8518, 14.2681), "torino": (45.0703, 7.6869),
    "palermo": (38.1157, 13.3615), "genova": (44.4056, 8.9463),
    "bologna": (44.4949, 11.3426), "firenze": (43.7696, 11.2558),
    "bari": (41.1171, 16.8719), "catania": (37.5079, 15.0830),
    "venezia": (45.4408, 12.3155), "verona": (45.4384, 10.9916),
    "pisa": (43.7228, 10.4017), "cagliari": (39.2238, 9.1217),
    "trieste": (45.6495, 13.7768), "perugia": (43.1107, 12.3908),
    "ancona": (43.6158, 13.5189), "reggio calabria": (38.1105, 15.6613),
    "parigi": (48.8566, 2.3522), "londra": (51.5074, -0.1278),
    "berlino": (52.5200, 13.4050), "madrid": (40.4168, -3.7038),
    "new york": (40.7128, -74.0060), "tokyo": (35.6762, 139.6503),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in chilometri sulla superficie terrestre."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_POINTS = ["nord", "nord-est", "est", "sud-est", "sud", "sud-ovest",
           "ovest", "nord-ovest"]


def bearing_word(deg: float | None) -> str:
    """Da gradi bussola a parola italiana. Stringa vuota se il dato manca."""
    if deg is None:
        return ""
    return _POINTS[int((deg % 360) / 45 + 0.5) % 8]


def compass_from(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """In che direzione si trova il secondo punto, visto dal primo."""
    dl = math.radians(lon2 - lon1)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return bearing_word(math.degrees(math.atan2(y, x)))


def geocode(place: str) -> tuple[float, float, str] | None:
    """Da nome di luogo a (latitudine, longitudine, nome pulito)."""
    key = (place or "").strip().lower()
    if not key:
        return None
    if key in _geo_cache:
        return _geo_cache[key]
    if key in KNOWN_CITIES:
        lat, lon = KNOWN_CITIES[key]
        found = (lat, lon, place.strip().title())
        _geo_cache[key] = found
        return found
    data = http_json(_NOMINATIM, params={"q": place, "format": "json", "limit": 1},
                     min_interval=1.1, cache_ttl=86400.0)
    if not data:
        return None
    try:
        found = (float(data[0]["lat"]), float(data[0]["lon"]),
                 str(data[0].get("display_name", place)).split(",")[0].strip())
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    _geo_cache[key] = found
    return found


def http_json(url: str, *, params: dict | None = None, timeout: float = 15.0,
              min_interval: float = 1.0, cache_ttl: float = 30.0,
              headers: dict | None = None):
    """GET che restituisce JSON, con cache e un limite di frequenza per host.

    Restituisce `None` su qualunque problema: rete assente, timeout, 429,
    risposta non JSON. Il chiamante formula il messaggio per l'utente, che
    cambia da fonte a fonte.
    """
    host = url.split("/")[2] if "//" in url else url
    key = (url, tuple(sorted((params or {}).items())))

    with _lock:
        hit = _cache.get(key)
        if hit and (time.monotonic() - hit[0]) < cache_ttl:
            return hit[1]

        # Attenzione al valore di ripiego: `time.monotonic()` conta dall'avvio
        # del sistema, non dall'epoca. Usare 0.0 come "mai chiamato" significa
        # che nei primi minuti dopo l'accensione la differenza è piccola e
        # l'attesa calcolata enorme: la PRIMA richiesta in assoluto a una
        # fonte con limite lungo veniva rifiutata. `None` distingue davvero
        # "mai chiamato" da "chiamato all'istante zero".
        precedente = _last_call.get(host)
        wait = 0.0 if precedente is None else min_interval - (time.monotonic() - precedente)
        if wait > 0:
            # Un dato leggermente vecchio batte un 429: se esiste una risposta
            # in cache, anche scaduta, vale più di nessuna risposta.
            if hit:
                return hit[1]
            # Attendere è accettabile solo per frazioni di secondo. Alcune
            # fonti hanno limiti lunghissimi — Launch Library consente circa
            # 15 richieste all'ora — e dormire quattro minuti dentro una
            # risposta vocale significa congelare l'assistente mentre l'utente
            # aspetta davanti allo schermo. Oltre la soglia si rinuncia e si
            # dice che il dato non è disponibile adesso.
            if wait > _MAX_WAIT:
                return None
            time.sleep(wait)

        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": UA, **(headers or {})})
            _last_call[host] = time.monotonic()
            if r.status_code == 429:
                return hit[1] if hit else None
            r.raise_for_status()
            data = r.json()
        except Exception:
            _last_call[host] = time.monotonic()
            return hit[1] if hit else None

        _cache[key] = (time.monotonic(), data)
        return data


def reset_state() -> None:
    """Svuota cache e orologi. Serve alle prove, non al funzionamento."""
    with _lock:
        _cache.clear()
        _last_call.clear()
        _geo_cache.clear()
