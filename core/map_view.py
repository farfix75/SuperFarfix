"""Vista radar — una mappa disegnata, non un globo 3D.

Perché un radar e non una mappa vera
------------------------------------
Un globo fotorealistico (Cesium e simili) vuole WebGL, una GPU, un motore di
rendering dentro l'applicazione e diverse centinaia di megabyte di tessere
scaricate: il costo che hai giustamente rifiutato. Anche una mappa a tessere
più modesta significa scaricare immagini da un server esterno a ogni domanda.

Questa vista disegna solo ciò che conta davvero nella risposta: **dove sono i
bersagli rispetto a te**. Cerchi di distanza, punti cardinali, un simbolo per
ogni aereo o terremoto, orientati correttamente attorno al centro. Nessuna
tessera, nessuna rete, nessuna GPU — le stesse primitive di QPainter con cui è
disegnato il volto, e lo stesso costo: qualche millisecondo, una volta sola,
quando fai la domanda.

Il vantaggio secondario è che sembra parte dell'interfaccia invece di una
finestra estranea incollata dentro: usa i colori del tema che hai scelto.

La proiezione
-------------
Azimutale equidistante centrata su di te: la distanza dal centro
dell'immagine è proporzionale alla distanza reale in chilometri, e l'angolo è
l'azimut reale. Su raggi di qualche centinaio di chilometri la deformazione è
trascurabile, ed è la proiezione che risponde alla domanda vera — «quanto
lontano e in che direzione» — meglio di qualunque rettangolo di mappa.
"""

from __future__ import annotations

import base64
import math
from io import BytesIO

from PyQt6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath,
                         QPen, QPolygonF, QRadialGradient)

__all__ = ["render_scope", "scope_html"]


def _blend(a: QColor, b: QColor, amount: int) -> QColor:
    t = max(0.0, min(1.0, amount / 255.0))
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def _project(bearing_deg: float, km: float, radius_km: float, size: int):
    """Azimut e distanza → punto sull'immagine."""
    r = (km / max(radius_km, 1e-6)) * (size / 2 - 26)
    a = math.radians(bearing_deg - 90.0)     # 0° = nord = in alto
    return QPointF(size / 2 + r * math.cos(a), size / 2 + r * math.sin(a))


def render_scope(targets, *, radius_km: float, centre_label: str,
                 primary: str = "#00d4ff", accent: str = "#ff6b00",
                 bg: str = "#00060a", size: int = 460,
                 subtitle: str = "") -> QImage:
    """Disegna la vista e restituisce l'immagine.

    `targets`: lista di dizionari con `km`, `bearing` (gradi, 0 = nord) e,
    facoltativi, `label`, `kind` ('aircraft' | 'quake' | 'point'), `size`
    (0–1, importanza relativa: la magnitudo di un sisma, per esempio) e
    `heading` (la rotta, per gli aerei).
    """
    pri = QColor(primary)
    acc = QColor(accent)
    back = QColor(bg)

    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(back)
    p = QPainter(img)
    try:
        _draw(p, img, targets, radius_km, centre_label, subtitle, pri, acc,
              back, size)
    finally:
        # Senza questo, un'eccezione lascia il pittore aperto e Qt abbatte il
        # processo con «Cannot destroy paint device that is being painted».
        # Una mappa mancata deve costare la mappa, non la sessione.
        if p.isActive():
            p.end()
    return img


def _draw(p, img, targets, radius_km, centre_label, subtitle, pri, acc, back,
          size):
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    c = QPointF(size / 2, size / 2)
    usable = size / 2 - 26

    # Fondo: un alone appena percettibile, così il cerchio non è un buco nero.
    glow = QRadialGradient(c, usable)
    glow.setColorAt(0.0, _blend(back, pri, 26))
    glow.setColorAt(1.0, back)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(c, usable, usable)

    # Cerchi di distanza. Le etichette stanno sull'asse verticale, dove i
    # bersagli passano meno spesso.
    p.setBrush(Qt.BrushStyle.NoBrush)
    font = QFont("Courier New", max(7, size // 52))
    p.setFont(font)
    for frazione in (0.25, 0.5, 0.75, 1.0):
        rr = usable * frazione
        p.setPen(QPen(_blend(back, pri, 60 if frazione < 1.0 else 120),
                      1.0 if frazione < 1.0 else 1.4))
        p.drawEllipse(c, rr, rr)
        p.setPen(QPen(_blend(back, pri, 110)))
        p.drawText(QRectF(c.x() + 4, c.y() - rr - 12, 60, 12),
                   Qt.AlignmentFlag.AlignLeft,
                   f"{radius_km * frazione:.0f} km")

    # Assi e punti cardinali.
    p.setPen(QPen(_blend(back, pri, 45), 1.0))
    p.drawLine(QPointF(c.x(), c.y() - usable), QPointF(c.x(), c.y() + usable))
    p.drawLine(QPointF(c.x() - usable, c.y()), QPointF(c.x() + usable, c.y()))
    p.setPen(QPen(_blend(back, pri, 150)))
    for etichetta, dx, dy in (("N", 0, -usable - 14), ("S", 0, usable + 6),
                              ("E", usable + 6, 0), ("O", -usable - 14, 0)):
        p.drawText(QRectF(c.x() + dx - 6, c.y() + dy - 6, 16, 14),
                   Qt.AlignmentFlag.AlignCenter, etichetta)

    # Centro: dove sei tu.
    p.setPen(QPen(_blend(back, acc, 220), 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(c, 4, 4)
    p.drawLine(QPointF(c.x() - 7, c.y()), QPointF(c.x() + 7, c.y()))
    p.drawLine(QPointF(c.x(), c.y() - 7), QPointF(c.x(), c.y() + 7))

    # Bersagli. Si disegnano in ordine di distanza decrescente, così i più
    # vicini — quelli che contano — restano sopra agli altri.
    def _km(t) -> float:
        try:
            return float(t.get("km", 0))
        except (TypeError, ValueError, AttributeError):
            # I bersagli illeggibili finiscono in fondo e vengono scartati nel
            # ciclo: l'ordinamento non è il posto dove far fallire una mappa.
            return float("inf")

    ordinati = sorted(targets, key=lambda t: -_km(t))
    occupati: list[QRectF] = []
    for t in ordinati:
        # Un bersaglio con un campo sporco salta se stesso, non tutta la
        # mappa: i dati arrivano da servizi esterni e un valore inatteso è
        # una questione di quando, non di se.
        try:
            km = float(t.get("km", 0))
            pt = _project(float(t.get("bearing", 0)), km, radius_km, size)
            kind = str(t.get("kind", "point"))
            peso = max(0.0, min(1.0, float(t.get("size", 0.5))))
        except (TypeError, ValueError, AttributeError):
            continue
        if km > radius_km * 1.02:
            continue

        if kind == "aircraft":
            # Un triangolo orientato secondo la rotta: la direzione di volo si
            # legge senza doverla scrivere accanto.
            ang = math.radians(float(t.get("heading", 0)) - 90.0)
            l, w = 7.0, 4.2
            punta = QPointF(pt.x() + l * math.cos(ang), pt.y() + l * math.sin(ang))
            base = ang + math.pi
            a1 = QPointF(pt.x() + w * math.cos(base - 0.5),
                         pt.y() + w * math.sin(base - 0.5))
            a2 = QPointF(pt.x() + w * math.cos(base + 0.5),
                         pt.y() + w * math.sin(base + 0.5))
            p.setPen(QPen(_blend(back, pri, 210), 1.2))
            p.setBrush(QBrush(_blend(back, pri, 150)))
            p.drawPolygon(QPolygonF([punta, a1, a2]))
        elif kind == "quake":
            # Cerchi concentrici: il raggio cresce con la magnitudo, come le
            # onde che si propagano.
            rr = 3.0 + 9.0 * peso
            p.setBrush(Qt.BrushStyle.NoBrush)
            for k, alpha in ((1.0, 200), (0.62, 120), (0.3, 70)):
                p.setPen(QPen(_blend(back, acc, alpha), 1.2))
                p.drawEllipse(pt, rr * k, rr * k)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_blend(back, acc, 230)))
            p.drawEllipse(pt, 1.8, 1.8)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_blend(back, pri, 200)))
            p.drawEllipse(pt, 3.4, 3.4)

        etichetta = str(t.get("label") or "")
        if etichetta:
            # Due bersagli vicini scrivevano le etichette una sopra l'altra,
            # rendendole illeggibili entrambe. Si prova a spostare la seconda
            # sotto o sopra; se non c'è spazio si rinuncia a scriverla — il
            # simbolo resta, e l'elenco sotto la mappa ha comunque i nomi.
            posata = False
            for dy in (-7, 5, -19, 17, -31):
                riquadro = QRectF(pt.x() + 8, pt.y() + dy, 74, 12)
                if any(riquadro.intersects(r) for r in occupati):
                    continue
                p.setPen(QPen(_blend(back, pri, 170)))
                p.drawText(riquadro, Qt.AlignmentFlag.AlignLeft, etichetta[:12])
                occupati.append(riquadro)
                posata = True
                break
            if not posata:
                pass

    # Intestazione.
    p.setPen(QPen(_blend(back, pri, 200)))
    p.setFont(QFont("Courier New", max(8, size // 46), QFont.Weight.Bold))
    p.drawText(QRectF(10, 6, size - 20, 16), Qt.AlignmentFlag.AlignLeft,
               centre_label.upper()[:34])
    if subtitle:
        p.setPen(QPen(_blend(back, pri, 130)))
        p.setFont(font)
        p.drawText(QRectF(10, size - 20, size - 20, 14),
                   Qt.AlignmentFlag.AlignLeft, subtitle[:60])


def scope_html(img: QImage, caption: str = "") -> str:
    """L'immagine come HTML incorporabile nel pannello contenuti.

    L'immagine viaggia dentro l'HTML come data URI invece che come file
    temporaneo: nessun file da creare, da trovare e da ripulire, e nessuna
    corsa fra chi lo scrive e chi lo legge.
    """
    # L'array deve avere un nome proprio. Scritto come QBuffer(QByteArray())
    # il vettore è un oggetto temporaneo: Python lo libera appena la riga
    # finisce, mentre il buffer C++ continua a puntarci — e il programma
    # muore con un segmentation fault dentro img.save().
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
