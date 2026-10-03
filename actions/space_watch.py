"""Spazio — posizione della ISS e prossimi lanci orbitali.

Due fonti pubbliche senza chiave:

* **wheretheiss.at** per la posizione della Stazione Spaziale Internazionale,
  aggiornata al secondo.
* **Launch Library 2** (The Space Devs) per il calendario dei lanci: è la
  banca dati dietro quasi tutte le app di countdown. Il limite anonimo è di
  circa 15 richieste all'ora, quindi la cache qui è lunga un'ora — un lancio
  previsto fra tre giorni non cambia ogni minuto.

Una cosa che questo modulo **non** fa: prevedere quando la ISS passerà sopra
di te. Quel calcolo richiede la propagazione orbitale (SGP4) e una libreria in
più; senza, l'unica alternativa sarebbe inventare un orario, che per una cosa
che l'utente andrebbe a verificare guardando il cielo è il modo peggiore di
sbagliare. Dice dov'è adesso e quanto è lontana: quello è misurato.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.public_data import compass_from, geocode, haversine_km, http_json

_ISS = "https://api.wheretheiss.at/v1/satellites/25544"
_LAUNCHES = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/"


def _fra_quanto(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    delta = t - datetime.now(timezone.utc)
    sec = delta.total_seconds()
    if sec < 0:
        return "in corso o appena avvenuto"
    ore = sec / 3600
    if ore < 1:
        return f"fra {int(sec // 60)} minuti"
    if ore < 48:
        return f"fra {int(ore)} ore"
    return f"fra {int(ore // 24)} giorni"


def _iss(place: str, player=None) -> str:
    data = http_json(_ISS, cache_ttl=10.0, min_interval=1.0)
    if not data:
        return "Non riesco a leggere la posizione della Stazione Spaziale adesso."
    lat, lon = data.get("latitude"), data.get("longitude")
    quota = data.get("altitude") or 0
    vel = data.get("velocity") or 0
    if lat is None or lon is None:
        return "Il dato sulla posizione della Stazione Spaziale è incompleto."

    msg = (f"La Stazione Spaziale è a {abs(lat):.1f}° "
           f"{'nord' if lat >= 0 else 'sud'}, {abs(lon):.1f}° "
           f"{'est' if lon >= 0 else 'ovest'}, "
           f"quota {quota:.0f} km, a {vel:.0f} km/h.")

    if place:
        found = geocode(place)
        if found:
            km = haversine_km(found[0], found[1], lat, lon)
            direzione = compass_from(found[0], found[1], lat, lon)
            msg += f" Da {found[2]} dista {km:,.0f} km".replace(",", ".")
            if km < 500:
                msg += ": è quasi sopra di te."
            elif km < 2000:
                msg += (f" verso {direzione}: abbastanza vicina da essere "
                        "sopra l'orizzonte, cielo permettendo.")
            else:
                msg += f", in direzione {direzione}."

    # Sopra terra o oceano? È la domanda che segue sempre, e la risposta
    # onesta è che da queste coordinate non si può dire senza una mappa.
    if player:
        try:
            player.show_content(
                "Stazione Spaziale Internazionale",
                f"Latitudine {lat:.4f}\nLongitudine {lon:.4f}\n"
                f"Quota {quota:.1f} km\nVelocità {vel:.0f} km/h\n"
                f"Visibilità: {data.get('visibility', 'n/d')}\n\n"
                "Fonte: wheretheiss.at — posizione al secondo.\n"
                "La previsione dei passaggi sopra un luogo richiede la "
                "propagazione orbitale e qui non viene calcolata.")
        except Exception:
            pass
    return msg


def _lanci(quanti: int, player=None) -> str:
    data = http_json(_LAUNCHES, params={"limit": max(1, min(quanti, 10)),
                                        "mode": "list"},
                     cache_ttl=3600.0, min_interval=240.0)
    if not data:
        return ("Non riesco a leggere il calendario dei lanci adesso. "
                "La fonte consente poche richieste all'ora.")
    risultati = data.get("results") or []
    if not risultati:
        return "Non risultano lanci orbitali programmati."

    voci = []
    for r in risultati:
        nome = str(r.get("name") or "lancio senza nome")
        net = str(r.get("net") or "")
        stato = ((r.get("status") or {}).get("abbrev")
                 or (r.get("status") or {}).get("name") or "")
        voci.append({"nome": nome, "quando": _fra_quanto(net),
                     "stato": stato, "net": net})

    if player:
        try:
            player.show_content(
                "Prossimi lanci orbitali",
                "\n".join(f"{v['nome']}\n   {v['net'][:16].replace('T', ' ')} UTC"
                          f" — {v['quando']} — stato: {v['stato']}"
                          for v in voci) +
                "\n\nFonte: Launch Library 2. Le date slittano spesso: "
                "«NET» significa non prima di.")
        except Exception:
            pass

    primo = voci[0]
    msg = f"Il prossimo lancio è {primo['nome']}, {primo['quando']}"
    if primo["stato"]:
        msg += f", stato {primo['stato']}"
    msg += "."
    if len(voci) > 1:
        msg += " Poi: " + "; ".join(f"{v['nome']} {v['quando']}"
                                    for v in voci[1:4]) + "."
    msg += " Le date sono NET, cioè non prima di: slittano spesso."
    return msg


def space_watch(parameters: dict, player=None, session_memory=None) -> str:
    cosa = str(parameters.get("what") or "iss").strip().lower()
    place = str(parameters.get("location") or "").strip()
    try:
        quanti = int(parameters.get("count") or 4)
    except (TypeError, ValueError):
        quanti = 4

    if cosa.startswith("lan") or "launch" in cosa or "razz" in cosa:
        return _lanci(quanti, player)
    return _iss(place, player)


TOOL = {
    "name": "space_watch",
    "description": (
        "Dove si trova adesso la Stazione Spaziale Internazionale (posizione, "
        "quota, velocità, distanza da una città) oppure quali sono i prossimi "
        "lanci orbitali programmati. Usalo quando l'utente chiede della ISS, "
        "di satelliti, di lanci o di razzi."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "what": {
                "type": "STRING",
                "description": "'iss' per la Stazione Spaziale, 'launches' per i lanci",
            },
            "location": {
                "type": "STRING",
                "description": "Città da cui misurare la distanza della ISS (opzionale)",
            },
            "count": {
                "type": "NUMBER",
                "description": "Quanti lanci elencare (1-10, predefinito 4)",
            },
        },
        "required": [],
    },
    "handler": space_watch,
}
