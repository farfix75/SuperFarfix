"""Globo terrestre disegnato — la Terra vista dallo spazio, con i bersagli sopra.

Come si regge senza Cesium
--------------------------
Un globo fotorealistico vero vuole WebGL, una GPU e centinaia di megabyte di
tessere satellitari. Qui la Terra è **disegnata**: una sfera illuminata, le
coste del mondo in un file di 24 KB (Natural Earth, dominio pubblico) e una
proiezione ortografica — esattamente la geometria di una fotografia scattata da
molto lontano. È il motivo per cui il risultato *sembra* un globo 3D pur
essendo qualche migliaio di segmenti disegnati una volta sola.

Costo: le stesse primitive di QPainter del volto, qualche millisecondo per
immagine. Nessuna rete, nessuna GPU, nessun processo in sottofondo.

La proiezione
-------------
Ortografica, centrata sul luogo di cui hai chiesto. Con `radius_km` la vista si
avvicina finché quel raggio riempie il disco: chiedendo gli aerei su Roma vedi
l'Italia centrale, non l'emisfero. Con `radius_km=None` resta il globo intero,
che è quello che serve per i terremoti nel mondo.

L'orizzonte è reale: i bersagli e le coste sulla faccia nascosta della sfera
non vengono disegnati, perché da quel punto di vista non si vedrebbero.
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath,
                         QPen, QPolygonF, QRadialGradient)

__all__ = ["render_globe", "globe_html"]

_COAST_FILE = Path(__file__).resolve().parent.parent / "data" / "coastlines.json"
_coast_cache: list | None = None
_R_EARTH_KM = 6371.0


def _coastlines() -> list:
    """Le coste, caricate una volta sola e tenute in memoria."""
    global _coast_cache
    if _coast_cache is None:
        try:
            raw = json.loads(_COAST_FILE.read_text(encoding="utf-8"))
            # Il file è in decimi di grado interi: pesa un quarto rispetto ai
            # gradi decimali e la differenza non si vede su un disco di 400 px.
            _coast_cache = [[(lon / 10.0, lat / 10.0) for lon, lat in anello]
                            for anello in raw]
        except Exception:
            _coast_cache = []
    return _coast_cache


def _blend(a: QColor, b: QColor, amount: int) -> QColor:
    t = max(0.0, min(1.0, amount / 255.0))
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


class _Ortho:
    """Proiezione ortografica centrata su un punto, con avvicinamento."""

    def __init__(self, lat0: float, lon0: float, r_px: float, zoom: float,
                 cx: float, cy: float):
        self.s0 = math.sin(math.radians(lat0))
        self.c0 = math.cos(math.radians(lat0))
        self.lon0 = math.radians(lon0)
        self.k = r_px * zoom
        self.cx, self.cy = cx, cy
        self.r_px = r_px

    def __call__(self, lat: float, lon: float):
        """→ (x, y, visibile). `visibile` è False sulla faccia nascosta."""
        la = math.radians(lat)
        dl = math.radians(lon) - self.lon0
        sla, cla = math.sin(la), math.cos(la)
        cos_c = self.s0 * sla + self.c0 * cla * math.cos(dl)
        x = self.cx + self.k * cla * math.sin(dl)
        y = self.cy - self.k * (self.c0 * sla - self.s0 * cla * math.cos(dl))
        return x, y, cos_c > 0.0


# Sotto questo raggio l'ingrandimento non viene più aumentato. Le coste che
# abbiamo sono semplificate a circa 30 km: spingersi oltre non mostra più
# dettaglio, mostra solo un poligono gigante che riempie lo schermo di verde.
# Meglio una vista un po' più larga, dove si riconoscono la costa e il mare.
_MIN_RADIUS_KM = 320.0


def _zoom_for(radius_km: float | None) -> float:
    """Avvicinamento tale che `radius_km` riempia il disco."""
    if not radius_km or radius_km <= 0:
        return 1.0
    radius_km = max(float(radius_km), _MIN_RADIUS_KM)
    # Raggio angolare della calotta vista; oltre un quarto di globo tanto vale
    # mostrare il globo intero.
    theta = min(radius_km / _R_EARTH_KM, math.pi / 2)
    if theta >= 1.2:
        return 1.0
    return max(1.0, 1.0 / max(math.sin(theta), 1e-3))


def render_globe(targets, *, centre_lat: float, centre_lon: float,
                 radius_km: float | None = None, label: str = "",
                 subtitle: str = "", caption_targets: int = 0,
                 primary: str = "#00d4ff", accent: str = "#ff6b00",
                 bg: str = "#00060a", size: int = 460) -> QImage:
    """Disegna il globo con i bersagli e restituisce l'immagine.

    `targets`: dizionari con `lat`, `lon` e, facoltativi, `kind`
    ('aircraft' | 'quake' | 'point'), `heading`, `size` (0–1), `label`.
    """
    pri, acc, back = QColor(primary), QColor(accent), QColor(bg)
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(back)
    p = QPainter(img)
    try:
        _draw_globe(p, targets, centre_lat, centre_lon, radius_km, label,
                    subtitle, pri, acc, back, size)
    finally:
        # Un'eccezione con il pittore aperto fa abbattere il processo da Qt.
        if p.isActive():
            p.end()
    return img


def _draw_globe(p, targets, lat0, lon0, radius_km, label, subtitle,
                pri, acc, back, size):
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    r_px = size / 2 - 16
    cx = cy = size / 2
    zoom = _zoom_for(radius_km)
    proj = _Ortho(lat0, lon0, r_px, zoom, cx, cy)
    centro = QPointF(cx, cy)

    disco = QPainterPath()
    disco.addEllipse(centro, r_px, r_px)

    # ── oceano: sfera illuminata da sinistra-alto, come nelle foto dallo
    # spazio. Il gradiente spostato dal centro è ciò che rende la palla
    # rotonda invece che un cerchio piatto.
    mare_chiaro = QColor("#1d4f8c")
    mare_scuro = QColor("#050e1c")
    g = QRadialGradient(cx - r_px * 0.35, cy - r_px * 0.38, r_px * 1.45)
    g.setColorAt(0.00, _blend(mare_chiaro, QColor("#4a90d9"), 90))
    g.setColorAt(0.45, mare_chiaro)
    g.setColorAt(0.80, _blend(mare_chiaro, mare_scuro, 150))
    g.setColorAt(1.00, mare_scuro)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(centro, r_px, r_px)

    p.save()
    p.setClipPath(disco)

    # ── terre emerse ────────────────────────────────────────────────────────
    terra = QColor("#3f6b3a")
    terra_bordo = QColor("#6f9c5a")
    for anello in _coastlines():
        # Un anello che attraversa l'orizzonte va spezzato: unire i punti
        # visibili ai due lati disegnerebbe un continente attraverso la sfera.
        tratto: list[QPointF] = []
        for lon, lat in anello:
            x, y, vis = proj(lat, lon)
            if vis:
                tratto.append(QPointF(x, y))
            elif tratto:
                if len(tratto) > 2:
                    p.setBrush(QBrush(terra))
                    p.setPen(QPen(terra_bordo, 0.9))
                    p.drawPolygon(QPolygonF(tratto))
                tratto = []
        if len(tratto) > 2:
            p.setBrush(QBrush(terra))
            p.setPen(QPen(terra_bordo, 0.9))
            p.drawPolygon(QPolygonF(tratto))

    # ── reticolo: meridiani e paralleli ogni 15°, molto tenui ───────────────
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 26), 0.8))
    for lon in range(-180, 180, 15):
        punti = [QPointF(*proj(la, lon)[:2]) for la in range(-90, 91, 5)
                 if proj(la, lon)[2]]
        if len(punti) > 1:
            p.drawPolyline(QPolygonF(punti))
    for lat in range(-75, 76, 15):
        punti = [QPointF(*proj(lat, lo)[:2]) for lo in range(-180, 181, 5)
                 if proj(lat, lo)[2]]
        if len(punti) > 1:
            p.drawPolyline(QPolygonF(punti))

    # ── bersagli ────────────────────────────────────────────────────────────
    occupati: list[QRectF] = []
    def _peso(z) -> float:
        """Ordinamento tollerante: un bersaglio illeggibile non deve far
        fallire l'ordinamento, che è il posto meno utile dove fallire."""
        try:
            return -float(z.get("size", 0.5))
        except (TypeError, ValueError, AttributeError):
            return 0.0

    for t in sorted(targets or [], key=_peso):
        try:
            x, y, vis = proj(float(t["lat"]), float(t["lon"]))
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if not vis:
            continue                       # dall'altra parte del pianeta
        pt = QPointF(x, y)
        kind = str(t.get("kind", "point"))
        try:
            peso = max(0.0, min(1.0, float(t.get("size", 0.5))))
        except (TypeError, ValueError):
            peso = 0.5

        if kind == "aircraft":
            # Il triangolo punta dove punta l'aereo. Il nord sullo schermo
            # ruota con la proiezione, quindi la rotta va misurata rispetto
            # al nord *proiettato* in quel punto, non rispetto all'alto
            # dell'immagine: altrimenti ai bordi del globo le frecce mentono.
            try:
                heading = float(t.get("heading", 0) or 0)
            except (TypeError, ValueError):
                heading = 0.0
            nx, ny, nvis = proj(min(float(t["lat"]) + 0.5, 90.0), float(t["lon"]))
            nord = math.atan2(ny - y, nx - x) if nvis else -math.pi / 2
            ang = nord + math.radians(heading)
            l, w = 7.5, 4.0
            punta = QPointF(x + l * math.cos(ang), y + l * math.sin(ang))
            base = ang + math.pi
            a1 = QPointF(x + w * math.cos(base - 0.55), y + w * math.sin(base - 0.55))
            a2 = QPointF(x + w * math.cos(base + 0.55), y + w * math.sin(base + 0.55))
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.1))
            p.setBrush(QBrush(_blend(QColor("#ffffff"), pri, 150)))
            p.drawPolygon(QPolygonF([punta, a1, a2]))
        elif kind == "quake":
            rr = 3.0 + 10.0 * peso
            p.setBrush(Qt.BrushStyle.NoBrush)
            for k, alpha in ((1.0, 230), (0.6, 140), (0.3, 80)):
                p.setPen(QPen(_blend(QColor("#ffffff"), acc, 255 - alpha // 4), 1.3))
                col = QColor(acc); col.setAlpha(alpha)
                p.setPen(QPen(col, 1.3))
                p.drawEllipse(pt, rr * k, rr * k)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(acc))
            p.drawEllipse(pt, 2.0, 2.0)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_blend(QColor("#ffffff"), pri, 120)))
            p.drawEllipse(pt, 3.2, 3.2)

        etichetta = str(t.get("label") or "")
        if etichetta:
            # Bersagli vicini scrivevano le etichette una sull'altra. Si cerca
            # una riga libera; se non c'è, si rinuncia all'etichetta — il
            # simbolo resta e i nomi sono comunque nella risposta parlata.
            p.setFont(QFont("Courier New", max(7, size // 58)))
            for dy in (-7, 5, -19, 17, -31, 29):
                riquadro = QRectF(x + 8, y + dy, 76, 12)
                if any(riquadro.intersects(r) for r in occupati):
                    continue
                p.setPen(QPen(QColor(255, 255, 255, 190)))
                p.drawText(riquadro, Qt.AlignmentFlag.AlignLeft, etichetta[:12])
                occupati.append(riquadro)
                break

    # ── l'area di cui hai chiesto ───────────────────────────────────────────
    # Quando il raggio richiesto è più stretto della vista mostrata, senza un
    # riferimento sembrerebbe che i bersagli siano sparsi su tutta l'immagine.
    if radius_km and radius_km < _MIN_RADIUS_KM:
        anello = []
        for grado in range(0, 361, 6):
            b = math.radians(grado)
            d = float(radius_km) / _R_EARTH_KM
            la1 = math.radians(lat0)
            la2 = math.asin(math.sin(la1) * math.cos(d)
                            + math.cos(la1) * math.sin(d) * math.cos(b))
            lo2 = math.radians(lon0) + math.atan2(
                math.sin(b) * math.sin(d) * math.cos(la1),
                math.cos(d) - math.sin(la1) * math.sin(la2))
            x, y, vis = proj(math.degrees(la2), math.degrees(lo2))
            if vis:
                anello.append(QPointF(x, y))
        if len(anello) > 3:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_blend(QColor("#ffffff"), pri, 120), 1.0,
                          Qt.PenStyle.DashLine))
            p.drawPolygon(QPolygonF(anello))

    # ── il punto di vista: dove sei tu ──────────────────────────────────────
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 200), 1.4))
    p.drawEllipse(centro, 5, 5)
    p.drawLine(QPointF(cx - 9, cy), QPointF(cx - 6, cy))
    p.drawLine(QPointF(cx + 6, cy), QPointF(cx + 9, cy))
    p.drawLine(QPointF(cx, cy - 9), QPointF(cx, cy - 6))
    p.drawLine(QPointF(cx, cy + 6), QPointF(cx, cy + 9))
    p.restore()

    # ── atmosfera: un alone sottile appena fuori dal bordo ──────────────────
    alone = QRadialGradient(centro, r_px * 1.14)
    alone.setColorAt(0.86, QColor(0, 0, 0, 0))
    alone.setColorAt(0.90, _blend(back, pri, 90))
    alone.setColorAt(1.00, QColor(back.red(), back.green(), back.blue(), 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(alone))
    p.drawEllipse(centro, r_px * 1.14, r_px * 1.14)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_blend(back, pri, 150), 1.2))
    p.drawEllipse(centro, r_px, r_px)

    # ── intestazioni ────────────────────────────────────────────────────────
    p.setPen(QPen(_blend(back, pri, 210)))
    p.setFont(QFont("Courier New", max(8, size // 46), QFont.Weight.Bold))
    p.drawText(QRectF(10, 6, size - 20, 16), Qt.AlignmentFlag.AlignLeft,
               label.upper()[:34])
    if subtitle:
        p.setPen(QPen(_blend(back, pri, 140)))
        p.setFont(QFont("Courier New", max(7, size // 54)))
        p.drawText(QRectF(10, size - 20, size - 20, 14),
                   Qt.AlignmentFlag.AlignLeft, subtitle[:64])


def globe_html(img: QImage, caption: str = "") -> str:
    """L'immagine come HTML per il pannello contenuti (data URI, nessun file)."""
    # L'array deve avere un nome: passato come temporaneo, Python lo libera
    # mentre il buffer C++ ci scrive ancora, e il processo muore.
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    b64 = base64.b64encode(bytes(data)).decode("ascii")
    parti = [f'<div style="text-align:center;">'
             f'<img src="data:image/png;base64,{b64}" width="{img.width()}">']
    if caption:
        parti.append(f'<div style="font-family:Courier New; font-size:10px;'
                     f' color:#7a8a94; margin-top:4px;">{caption}</div>')
    parti.append("</div>")
    return "".join(parti)
