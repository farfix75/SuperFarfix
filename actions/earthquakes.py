"""Terremoti recenti — dati sismici pubblici dello USGS.

La fonte è il servizio FDSN dello United States Geological Survey: nessuna
chiave, copertura mondiale, aggiornamento in pochi minuti dall'evento. È la
stessa fonte che alimenta quasi tutte le mappe sismiche in circolazione.

Due cose sulla magnitudo, perché la risposta non induca in errore:

* Sotto magnitudo 2,5 la rete rileva migliaia di eventi al giorno che nessuno
  avverte. Il filtro predefinito parte da 2,5 per non seppellire l'unica
  scossa che conta sotto duecento microsismi.
* La magnitudo è logaritmica: da 5 a 6 l'energia è circa trenta volte
  maggiore, non il venti per cento in più. Per questo la risposta aggiunge un
  giudizio in parole ai numeri, che da soli ingannano.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.public_data import compass_from, geocode, haversine_km, http_json

_USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def _severity(mag: float) -> str:
    """Giudizio in parole. La scala è logaritmica: i numeri da soli ingannano."""
    if mag < 3.0:
        return "appena strumentale, di norma non avvertito"
    if mag < 4.0:
        return "lieve, avvertito nelle vicinanze"
    if mag < 5.0:
        return "moderato, avvertito chiaramente"
    if mag < 6.0:
        return "forte, possibili danni a edifici vulnerabili"
    if mag < 7.0:
        return "molto forte, danni probabili in area abitata"
    return "maggiore, potenzialmente distruttivo"


def _when(ms: int) -> str:
    try:
        t = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return ""
    delta = datetime.now(timezone.utc) - t
    minuti = int(delta.total_seconds() // 60)
    if minuti < 1:
        return "pochi istanti fa"
    if minuti < 60:
        return f"{minuti} minuti fa"
    ore = minuti // 60
    if ore < 24:
        return f"{ore} ore fa"
    return f"{ore // 24} giorni fa"


def earthquakes(parameters: dict, player=None, session_memory=None) -> str:
    place = str(parameters.get("location") or "").strip()
    try:
        ore = float(parameters.get("hours") or 24)
    except (TypeError, ValueError):
        ore = 24.0
    ore = max(1.0, min(ore, 720.0))
    try:
        min_mag = float(parameters.get("min_magnitude") or 2.5)
    except (TypeError, ValueError):
        min_mag = 2.5
    min_mag = max(0.0, min(min_mag, 9.0))
    try:
        raggio = float(parameters.get("radius_km") or 800)
    except (TypeError, ValueError):
        raggio = 800.0
    raggio = max(10.0, min(raggio, 20000.0))

    params = {
        "format": "geojson",
        "starttime": (datetime.now(timezone.utc)
                      - timedelta(hours=ore)).strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_mag,
        "orderby": "time",
        "limit": 200,
    }

    origine = None
    if place:
        origine = geocode(place)
        if not origine:
            return f"Non sono riuscito a trovare dove si trova {place}."
        params.update({"latitude": origine[0], "longitude": origine[1],
                       "maxradiuskm": raggio})

    data = http_json(_USGS, params=params, cache_ttl=120.0, min_interval=2.0)
    if data is None:
        return "Non riesco a leggere i dati sismici adesso."

    eventi = []
    for f in (data.get("features") or []):
        try:
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1]
            mag = float(p.get("mag") or 0)
            eventi.append({
                "mag": mag,
                "dove": str(p.get("place") or "luogo non specificato"),
                "quando": _when(p.get("time")),
                "prof_km": round(float(f["geometry"]["coordinates"][2] or 0)),
                "lat": lat, "lon": lon,
                "tsunami": bool(p.get("tsunami")),
            })
        except (KeyError, IndexError, TypeError, ValueError):
            continue

    if not eventi:
        dove = f" entro {raggio:.0f} km da {origine[2]}" if origine else " nel mondo"
        return (f"Nessun terremoto di magnitudo {min_mag} o superiore{dove} "
                f"nelle ultime {ore:.0f} ore.")

    forti = sorted(eventi, key=lambda e: -e["mag"])
    recente = eventi[0]
    peggiore = forti[0]

    if origine:
        for e in eventi:
            e["km"] = round(haversine_km(origine[0], origine[1], e["lat"], e["lon"]))
            e["dir"] = compass_from(origine[0], origine[1], e["lat"], e["lon"])

    if player and origine:
        try:
            if hasattr(player, "show_map"):
                from core.public_data import bearing_word  # noqa: F401
                import math as _m

                def _az(lat2, lon2):
                    dl = _m.radians(lon2 - origine[1])
                    p1, p2 = _m.radians(origine[0]), _m.radians(lat2)
                    y = _m.sin(dl) * _m.cos(p2)
                    x = (_m.cos(p1) * _m.sin(p2)
                         - _m.sin(p1) * _m.cos(p2) * _m.cos(dl))
                    return (_m.degrees(_m.atan2(y, x)) + 360.0) % 360.0

                punti = [{
                    "km": e["km"], "bearing": _az(e["lat"], e["lon"]),
                    "kind": "quake",
                    # La magnitudo diventa la dimensione del simbolo: 2 è
                    # piccolo, 7 riempie il cerchio.
                    "size": max(0.05, min(1.0, (e["mag"] - 2.0) / 5.0)),
                    "label": f"M{e['mag']:.1f}",
                } for e in eventi[:14] if "km" in e]
                if punti:
                    player.show_map(
                        f"Terremoti — {origine[2]}", punti,
                        radius_km=raggio, centre_label=f"Terremoti — {origine[2]}",
                        subtitle=f"{len(eventi)} eventi · USGS · {ore:.0f} ore",
                        caption=f"Più forte: M{peggiore['mag']:.1f} a "
                                f"{peggiore['dove']} ({peggiore['quando']})")
        except Exception:
            pass

    if player:
        try:
            righe = []
            for e in eventi[:20]:
                riga = (f"M {e['mag']:.1f} — {e['dove']} — {e['quando']} — "
                        f"profondità {e['prof_km']} km")
                if origine:
                    riga += f" — {e['km']} km a {e['dir']}"
                if e["tsunami"]:
                    riga += "  ⚠ allerta tsunami"
                righe.append(riga)
            titolo = (f"Terremoti M{min_mag}+ vicino a {origine[2]}" if origine
                      else f"Terremoti M{min_mag}+ nel mondo")
            player.show_content(
                f"{titolo} — ultime {ore:.0f} ore",
                f"{len(eventi)} eventi\n\n" + "\n".join(righe) +
                "\n\nFonte: USGS. La magnitudo è logaritmica: +1 grado "
                "equivale a circa 30 volte l'energia.")
        except Exception:
            pass

    area = f"entro {raggio:.0f} km da {origine[2]}" if origine else "nel mondo"
    msg = (f"{len(eventi)} terremoti di magnitudo {min_mag} o superiore "
           f"{area} nelle ultime {ore:.0f} ore. ")
    msg += (f"Il più recente: magnitudo {recente['mag']:.1f} a {recente['dove']}, "
            f"{recente['quando']}")
    if origine and "km" in recente:
        msg += f", a {recente['km']} km a {recente['dir']}"
    msg += ". "
    if peggiore is not recente and peggiore["mag"] > recente["mag"]:
        msg += (f"Il più forte è stato magnitudo {peggiore['mag']:.1f} a "
                f"{peggiore['dove']}, {peggiore['quando']}: "
                f"{_severity(peggiore['mag'])}. ")
    else:
        msg += f"Intensità: {_severity(peggiore['mag'])}. "
    if any(e["tsunami"] for e in eventi):
        msg += "Almeno un evento ha fatto scattare una valutazione tsunami."

    print(f"[Sismi] {len(eventi)} eventi, max M{peggiore['mag']:.1f}")
    return msg.strip()


TOOL = {
    "name": "earthquakes",
    "description": (
        "Terremoti realmente avvenuti di recente, dai dati pubblici USGS: "
        "magnitudo, luogo, profondità e quanto tempo fa. Usalo quando "
        "l'utente chiede se c'è stato un terremoto, se ha sentito una scossa, "
        "o vuole sapere l'attività sismica di una zona o del mondo."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "location": {
                "type": "STRING",
                "description": ("Città o zona attorno a cui cercare. Ometti "
                                "per cercare in tutto il mondo."),
            },
            "hours": {
                "type": "NUMBER",
                "description": "Quante ore indietro guardare (1-720, predefinito 24)",
            },
            "min_magnitude": {
                "type": "NUMBER",
                "description": ("Magnitudo minima (predefinito 2.5: sotto "
                                "questa soglia gli eventi sono migliaia e non "
                                "vengono avvertiti)"),
            },
            "radius_km": {
                "type": "NUMBER",
                "description": "Raggio attorno al luogo in km (predefinito 800)",
            },
        },
        "required": [],
    },
    "handler": earthquakes,
}
