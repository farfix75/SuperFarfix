"""Meteo con dati reali — Open-Meteo, senza chiave.

Differenza dall'azione `weather_report`, che resta al suo posto: quella apre
una ricerca nel browser, questa legge i numeri e li dice a voce. Servono a due
momenti diversi — «che tempo fa?» mentre hai le mani occupate, contro «fammi
vedere il meteo della settimana».

Sui codici meteo: Open-Meteo restituisce un numero WMO, non una frase. La
tabella qui sotto lo traduce. È volutamente esplicita invece di essere
scaricata da qualche parte: sono trenta righe che non cambiano mai, e un
codice non tradotto diventerebbe un «codice 61» detto a voce.
"""

from __future__ import annotations

from core.public_data import bearing_word, geocode, http_json

_FORECAST = "https://api.open-meteo.com/v1/forecast"

# Codici meteo WMO → italiano.
_WMO = {
    0: "sereno", 1: "prevalentemente sereno", 2: "parzialmente nuvoloso",
    3: "coperto", 45: "nebbia", 48: "nebbia con brina",
    51: "pioviggine leggera", 53: "pioviggine", 55: "pioviggine intensa",
    56: "pioviggine gelata", 57: "pioviggine gelata intensa",
    61: "pioggia leggera", 63: "pioggia", 65: "pioggia forte",
    66: "pioggia gelata", 67: "pioggia gelata forte",
    71: "neve leggera", 73: "neve", 75: "neve abbondante",
    77: "granelli di neve",
    80: "rovesci leggeri", 81: "rovesci", 82: "rovesci violenti",
    85: "rovesci di neve", 86: "rovesci di neve intensi",
    95: "temporale", 96: "temporale con grandine",
    99: "temporale con grandine forte",
}


def _beaufort(kmh: float) -> str:
    """Il vento in parole: «22 km/h» dice poco, «brezza tesa» dice tutto."""
    if kmh < 2:
        return "calma"
    if kmh < 12:
        return "brezza leggera"
    if kmh < 29:
        return "brezza tesa"
    if kmh < 50:
        return "moderato"
    if kmh < 75:
        return "forte"
    if kmh < 103:
        return "burrasca"
    return "tempesta"


def weather_now(parameters: dict, player=None, session_memory=None) -> str:
    place = str(parameters.get("location") or parameters.get("city") or "").strip()
    if not place:
        return "Di quale città vuoi il meteo?"
    giorni = parameters.get("days")
    try:
        giorni = int(giorni) if giorni else 0
    except (TypeError, ValueError):
        giorni = 0
    giorni = max(0, min(giorni, 7))

    found = geocode(place)
    if not found:
        return f"Non sono riuscito a trovare dove si trova {place}."
    lat, lon, label = found

    params = {
        "latitude": lat, "longitude": lon,
        "current": ("temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,weather_code,wind_speed_10m,"
                    "wind_direction_10m,wind_gusts_10m"),
        "timezone": "auto",
    }
    if giorni:
        params["daily"] = ("weather_code,temperature_2m_max,temperature_2m_min,"
                           "precipitation_probability_max,wind_speed_10m_max")
        params["forecast_days"] = giorni

    data = http_json(_FORECAST, params=params, cache_ttl=300.0, min_interval=1.0)
    if not data or "current" not in data:
        return f"Non riesco a leggere i dati meteo per {label} adesso."

    c = data["current"]
    cielo = _WMO.get(int(c.get("weather_code") or -1), "condizioni non classificate")
    temp = c.get("temperature_2m")
    perc = c.get("apparent_temperature")
    vento = c.get("wind_speed_10m") or 0
    raffica = c.get("wind_gusts_10m") or 0
    direzione = bearing_word(c.get("wind_direction_10m"))

    msg = f"A {label}: {cielo}, {temp:.0f} gradi"
    # La temperatura percepita si dice solo quando si discosta davvero:
    # ripeterla identica a quella reale è rumore.
    if perc is not None and abs(perc - (temp or 0)) >= 2:
        msg += f", percepiti {perc:.0f}"
    msg += f". Vento {_beaufort(vento)}, {vento:.0f} km/h"
    if direzione:
        msg += f" da {direzione}"
    if raffica and raffica > vento * 1.5:
        msg += f", con raffiche fino a {raffica:.0f}"
    msg += "."
    if (c.get("precipitation") or 0) > 0:
        msg += f" Sta piovendo: {c['precipitation']} millimetri nell'ultima ora."

    if giorni and data.get("daily"):
        d = data["daily"]
        righe = []
        for i in range(min(giorni, len(d.get("time", [])))):
            righe.append(
                f"{d['time'][i]}: "
                f"{_WMO.get(int(d['weather_code'][i]), '—')}, "
                f"{d['temperature_2m_min'][i]:.0f}–{d['temperature_2m_max'][i]:.0f}°, "
                f"pioggia {d['precipitation_probability_max'][i]}%, "
                f"vento fino a {d['wind_speed_10m_max'][i]:.0f} km/h")
        if player:
            try:
                player.show_content(f"Meteo {label} — {giorni} giorni",
                                    "\n".join(righe) + "\n\nFonte: Open-Meteo")
            except Exception:
                pass
        primi = righe[:3]
        msg += " Nei prossimi giorni: " + "; ".join(
            r.split(": ", 1)[1].split(", pioggia")[0] for r in primi) + "."

    print(f"[Meteo] {label}: {cielo}, {temp}°")
    return msg


TOOL = {
    "name": "weather_now",
    "description": (
        "Meteo con dati reali detti a voce: temperatura, percepita, cielo, "
        "vento e raffiche, più la previsione dei prossimi giorni se richiesta. "
        "Usalo quando l'utente chiede che tempo fa o che tempo farà. Usa "
        "weather_report solo se chiede espressamente di APRIRE o VEDERE il "
        "meteo nel browser."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "location": {"type": "STRING", "description": "Città, es. 'Roma'"},
            "days": {
                "type": "NUMBER",
                "description": ("Giorni di previsione (0-7). Ometti o 0 per le "
                                "sole condizioni attuali."),
            },
        },
        "required": ["location"],
    },
    "handler": weather_now,
}
