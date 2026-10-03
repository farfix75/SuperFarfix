# Dati di terze parti

## Linee di costa — `data/coastlines.json`

Derivate da **Natural Earth** (ne_110m_land), semplificate con l'algoritmo di
Douglas-Peucker a circa 0,3° e memorizzate in decimi di grado interi: da 138 KB
a 24 KB, senza differenze visibili su un globo di poche centinaia di pixel.

Natural Earth è di **pubblico dominio**: <https://www.naturalearthdata.com/about/terms-of-use/>

> "All versions of Natural Earth raster and vector map data found on this
> website are in the public domain."

Nessuna attribuzione è dovuta, ma è giusto dire da dove vengono i dati.

## Fonti interrogate in tempo reale

Nessuna di queste richiede una chiave; tutte hanno limiti di frequenza che
`core/public_data.py` rispetta.

| Fonte | Cosa fornisce | Condizioni |
|---|---|---|
| OpenSky Network | posizioni degli aerei (ADS-B) | uso non commerciale; ~1 richiesta / 5 s in anonimo |
| USGS | eventi sismici | pubblico dominio |
| Open-Meteo | meteo e vento | libero per uso non commerciale |
| wheretheiss.at | posizione della ISS | libero, ~1 richiesta / s |
| Launch Library 2 | lanci orbitali | libero, ~15 richieste / ora in anonimo |
| Nominatim (OSM) | da nome di luogo a coordinate | max 1 richiesta / s, User-Agent obbligatorio |
