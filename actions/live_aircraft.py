"""Aerei in volo nelle vicinanze — dati reali dai transponder ADS-B.

Da dove arrivano i dati
-----------------------
OpenSky Network, la stessa fonte pubblica usata da God's Eye View: una rete di
ricevitori volontari che ascolta i transponder degli aerei. Nessuna chiave
necessaria, nessun server da avviare, nessun globo 3D da disegnare — solo una
richiesta HTTP e un po' di aritmetica. Costa CPU soltanto nei decimi di secondo
in cui la stai usando.

I limiti, detti chiaramente
---------------------------
* L'accesso anonimo è limitato: circa **una richiesta ogni 5 secondi** e un
  tetto giornaliero. Questo modulo li rispetta da solo (vedi `_MIN_INTERVAL` e
  la cache): chiedere «e adesso?» tre volte di fila non fa tre richieste.
* La copertura dipende dai ricevitori volontari: ottima su Europa e Nord
  America, più rada altrove. Nessun aereo trovato può significare «cielo
  vuoto» oppure «nessun ricevitore in zona», e la risposta lo dice invece di
  far credere la prima.
* I transponder danno posizione, quota, velocità e rotta. **Non** danno
  partenza e destinazione: quelle qui non compaiono, perché inventarle sarebbe
  il modo più facile di rendere inutile tutto il resto.

Con un account gratuito OpenSky i limiti salgono. Se in
`config/api_keys.json` ci sono `opensky_client_id` e `opensky_client_secret`,
vengono usati; altrimenti si procede in anonimo senza disturbare l'utente.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import requests

from core.public_data import compass_from

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"

_STATES_URL = "https://opensky-network.org/api/states/all"
_TOKEN_URL = ("https://auth.opensky-network.org/auth/realms/opensky-network/"
              "protocol/openid-connect/token")
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_UA = "SuperFarfix/1.0 (assistente vocale personale)"

# Il tetto anonimo documentato è una richiesta ogni 5 secondi. Si tiene un
# margine: superarlo significa 429 e nessun dato, che è peggio che aspettare.
_MIN_INTERVAL = 6.0
_CACHE_TTL = 20.0

_lock = threading.Lock()
_last_call = 0.0
_cache: dict[tuple, tuple[float, list]] = {}
_geo_cache: dict[str, tuple[float, float, str]] = {}

# Città italiane più comuni, per evitare un viaggio di rete sul caso frequente.
_KNOWN = {
    "roma": (41.9028, 12.4964), "milano": (45.4642, 9.1900),
    "napoli": (40.8518, 14.2681), "torino": (45.0703, 7.6869),
    "palermo": (38.1157, 13.3615), "genova": (44.4056, 8.9463),
    "bologna": (44.4949, 11.3426), "firenze": (43.7696, 11.2558),
    "bari": (41.1171, 16.8719), "catania": (37.5079, 15.0830),
    "venezia": (45.4408, 12.3155), "verona": (45.4384, 10.9916),
    "pisa": (43.7228, 10.4017), "cagliari": (39.2238, 9.1217),
}


def _config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _token() -> str:
    """Token OAuth2, se l'utente ha un account. Silenzioso se non ce l'ha."""
    cfg = _config()
    cid = str(cfg.get("opensky_client_id", "")).strip()
    secret = str(cfg.get("opensky_client_secret", "")).strip()
    if not cid or not secret:
        return ""
    try:
        r = requests.post(_TOKEN_URL, timeout=10, data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": secret,
        })
        r.raise_for_status()
        return str(r.json().get("access_token", ""))
    except Exception:
        # Credenziali sbagliate o servizio di autenticazione giù non devono
        # impedire la risposta: l'accesso anonimo funziona comunque.
        return ""


def _geocode(place: str) -> tuple[float, float, str] | None:
    key = place.strip().lower()
    if key in _geo_cache:
        return _geo_cache[key]
    if key in _KNOWN:
        lat, lon = _KNOWN[key]
        found = (lat, lon, place.strip().title())
        _geo_cache[key] = found
        return found
    try:
        r = requests.get(_NOMINATIM_URL, timeout=10,
                         headers={"User-Agent": _UA},
                         params={"q": place, "format": "json", "limit": 1})
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        found = (float(data[0]["lat"]), float(data[0]["lon"]),
                 str(data[0].get("display_name", place)).split(",")[0])
        _geo_cache[key] = found
        return found
    except Exception:
        return None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Azimut in gradi dal centro al bersaglio (0 = nord)."""
    dl = math.radians(lon2 - lon1)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _bearing_word(deg: float | None) -> str:
    if deg is None:
        return ""
    points = ["nord", "nord-est", "est", "sud-est", "sud", "sud-ovest",
              "ovest", "nord-ovest"]
    return points[int((deg % 360) / 45 + 0.5) % 8]


def _fetch_states(lat: float, lon: float, radius_km: float) -> list:
    """Stati grezzi nel riquadro, con limitazione di frequenza e cache."""
    global _last_call
    dlat = radius_km / 111.0
    dlon = radius_km / max(1.0, 111.0 * math.cos(math.radians(lat)))
    bbox = (round(lat - dlat, 2), round(lat + dlat, 2),
            round(lon - dlon, 2), round(lon + dlon, 2))

    with _lock:
        hit = _cache.get(bbox)
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL:
            return hit[1]

        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            # Meglio un dato di venti secondi fa che un 429 e nessun dato.
            stale = max((v for v in _cache.values()), key=lambda v: v[0], default=None)
            if stale:
                return stale[1]
            time.sleep(min(wait, _MIN_INTERVAL))

        headers = {"User-Agent": _UA}
        token = _token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = requests.get(_STATES_URL, timeout=15, headers=headers, params={
            "lamin": bbox[0], "lamax": bbox[1],
            "lomin": bbox[2], "lomax": bbox[3],
        })
        _last_call = time.monotonic()
        if r.status_code == 429:
            raise RuntimeError("limite di richieste raggiunto")
        r.raise_for_status()
        states = r.json().get("states") or []
        _cache[bbox] = (time.monotonic(), states)
        return states


def _parse(states: list, lat: float, lon: float, radius_km: float) -> list[dict]:
    out = []
    for s in states:
        try:
            s_lon, s_lat = s[5], s[6]
            if s_lat is None or s_lon is None:
                continue
            dist = _haversine_km(lat, lon, s_lat, s_lon)
            if dist > radius_km:
                # Il riquadro è quadrato, il raggio è un cerchio: gli angoli
                # vanno scartati, altrimenti «entro 50 km» non è vero.
                continue
            out.append({
                # L'azimut serve alla vista radar: senza, i bersagli finirebbero
                # tutti nella stessa direzione e la mappa mentirebbe.
                "bearing": _bearing_deg(lat, lon, s_lat, s_lon),
                "heading": s[10] or 0.0,
                "callsign": (s[1] or "").strip() or "sconosciuto",
                "paese": s[2] or "",
                "km": round(dist, 1),
                "quota_m": round(s[7] or s[13] or 0),
                "velocita_kmh": round((s[9] or 0) * 3.6),
                "rotta": _bearing_word(s[10]),
                "a_terra": bool(s[8]),
                "salita_ms": round(s[11] or 0, 1),
                "icao24": (s[0] or "").strip(),
            })
        except (IndexError, TypeError):
            continue
    out.sort(key=lambda a: a["km"])
    return out


def _describe(a: dict) -> str:
    if a["a_terra"]:
        return f"{a['callsign']} a terra, a {a['km']} km"
    bits = [f"{a['callsign']} a {a['km']} km"]
    if a["quota_m"]:
        bits.append(f"quota {a['quota_m']:,} metri".replace(",", "."))
    if a["velocita_kmh"]:
        bits.append(f"{a['velocita_kmh']} km/h")
    if a["rotta"]:
        bits.append(f"diretto a {a['rotta']}")
    if a["salita_ms"] > 2:
        bits.append("in salita")
    elif a["salita_ms"] < -2:
        bits.append("in discesa")
    return ", ".join(bits)


def live_aircraft(parameters: dict, player=None, session_memory=None) -> str:
    place = str(parameters.get("location") or "").strip()
    if not place:
        place = str(_config().get("home_city") or "").strip()
    if not place:
        return ("Sopra quale città vuoi che guardi? Puoi anche impostare "
                "\"home_city\" nella configurazione per non doverlo ripetere.")

    try:
        radius = float(parameters.get("radius_km") or 60)
    except (TypeError, ValueError):
        radius = 60.0
    radius = max(5.0, min(radius, 250.0))
    try:
        limit = int(parameters.get("max_results") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 15))

    found = _geocode(place)
    if not found:
        return f"Non sono riuscito a trovare dove si trova {place}."
    lat, lon, label = found

    try:
        states = _fetch_states(lat, lon, radius)
    except Exception as e:
        msg = f"Non riesco a leggere i dati di volo adesso: {e}"
        _log(msg, player)
        return msg

    planes = _parse(states, lat, lon, radius)
    if not planes:
        return (f"Nessun aereo rilevato entro {radius:.0f} km da {label}. "
                "Può voler dire cielo libero, oppure che in zona non ci sono "
                "ricevitori che li ascoltano.")

    airborne = [p for p in planes if not p["a_terra"]]
    shown = planes[:limit]

    if player:
        righe = [f"{i+1}. {_describe(p)}  [{p['paese']}]"
                 for i, p in enumerate(planes[:15])]
        testo = (f"{len(planes)} velivoli rilevati ({len(airborne)} in volo)\n\n"
                 + "\n".join(righe) +
                 "\n\nFonte: OpenSky Network (transponder ADS-B). "
                 "Partenza e destinazione non sono trasmesse dal transponder.")
        disegnata = False
        try:
            # La mappa c'è solo se l'interfaccia la sa disegnare: le azioni
            # devono funzionare anche da riga di comando e dalla dashboard.
            if hasattr(player, "show_map"):
                disegnata = player.show_map(
                    f"Aerei — {label}",
                    [{"km": p["km"], "bearing": p["bearing"],
                      "kind": "aircraft", "heading": p["heading"],
                      "label": p["callsign"]} for p in planes[:12]],
                    radius_km=radius, centre_label=f"Aerei — {label}",
                    subtitle=f"{len(planes)} velivoli · OpenSky",
                    caption=" · ".join(_describe(p) for p in planes[:3]))
        except Exception:
            disegnata = False
        if not disegnata:
            try:
                player.show_content(f"Aerei entro {radius:.0f} km da {label}", testo)
            except Exception:
                pass

    testa = (f"Entro {radius:.0f} km da {label} ci sono {len(planes)} velivoli, "
             f"{len(airborne)} in volo.")
    corpo = " Il più vicino è " + _describe(shown[0]) + "."
    if len(shown) > 1:
        corpo += " Poi " + "; poi ".join(_describe(p) for p in shown[1:]) + "."
    msg = testa + corpo
    _log(f"{len(planes)} velivoli entro {radius:.0f} km da {label}", player)
    return msg


def _log(message: str, player=None) -> None:
    print(f"[Aerei] {message}")
    if player:
        try:
            player.write_log(f"SYS: {message}")
        except Exception:
            pass


# ── Dichiarazione dello strumento (scoperta da core/action_loader.py) ────────
TOOL = {
    "name": "live_aircraft",
    "description": (
        "Aerei realmente in volo adesso vicino a una città, dai transponder "
        "ADS-B pubblici: distanza, quota, velocità e direzione. Usalo quando "
        "l'utente chiede che aerei ci sono sopra un posto, cosa sta volando "
        "vicino, o vuole tracciare il traffico aereo in tempo reale. NON per "
        "cercare o prenotare voli commerciali: per quello esiste flight_finder."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "location": {
                "type": "STRING",
                "description": "Città o luogo sopra cui guardare, es. 'Roma'",
            },
            "radius_km": {
                "type": "NUMBER",
                "description": "Raggio in km attorno al luogo (5-250, predefinito 60)",
            },
            "max_results": {
                "type": "NUMBER",
                "description": "Quanti velivoli elencare a voce (1-15, predefinito 5)",
            },
        },
        "required": ["location"],
    },
    "handler": live_aircraft,
}
