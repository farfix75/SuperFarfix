"""Registro strutturato degli scambi — le fondamenta dei dati.

A cosa serve
------------
Tutto quello che un giorno vorrai fare — addestrare un modello sul tuo stile,
misurare se una modifica ha migliorato o peggiorato le risposte, capire quali
comandi falliscono più spesso — richiede *dati di dialoghi reali*. E quei dati
si possono raccogliere solo mentre le conversazioni avvengono: una
conversazione non registrata è persa per sempre. È per questo che questo è il
primo passo e non l'ultimo, anche se è il meno appariscente.

Il registro precedente (`_session_log`) è una lista di stringhe «User: …» /
«FARFIX: …» che vive in memoria e sparisce alla chiusura. Va benissimo per
riassumere la sessione, ma non è un insieme di dati: non dice quanto ci ha
messo a rispondere, quale modello ha risposto, quali azioni ha eseguito e se
sono riuscite, se l'hai interrotto. Sono proprio quelle colonne a rendere i
dati utili in seguito.

Formato
-------
Un file JSONL al giorno in `memory/interactions/`, una riga per scambio. JSONL
perché è l'unico formato che si può scrivere in coda senza rileggere il file,
resiste a un'interruzione a metà riga (si perde una riga, non l'archivio) e si
legge riga per riga senza caricare tutto in memoria.

Privacy
-------
Questo file contiene tutto quello che dici al computer. È già escluso da git,
resta solo sul tuo disco e non viene inviato da nessuna parte. Si disattiva con
`"interaction_log": false` in `config/api_keys.json`, e si cancella con:

    python -m core.interaction_log purge

Le stringhe che somigliano a chiavi API o password vengono sostituite prima di
essere scritte: non è una garanzia assoluta — nessun filtro di questo tipo lo
è — ma evita l'errore più comune, cioè dettare una chiave a voce e ritrovarla
in chiaro nell'archivio.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

__all__ = ["InteractionLog", "LOG_DIR"]

_BASE = Path(__file__).resolve().parent.parent
LOG_DIR = _BASE / "memory" / "interactions"

# Sequenze che non devono mai finire nell'archivio: chiavi dei principali
# provider e frasi in cui l'utente detta una password.
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),            # OpenAI
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),           # Google
    re.compile(r"\bxai-[A-Za-z0-9]{16,}"),             # xAI
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),             # GitHub
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),        # Anthropic
    re.compile(r"(?i)\b(?:password|passphrase|pin)\b\s*(?:è|e|is|:)?\s*\S{4,}"),
]


def _redact(text: str) -> str:
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[RIMOSSO]", text)
    return text


class InteractionLog:
    """Raccoglie uno scambio alla volta e lo scrive quando il turno si chiude.

    Pensato per essere impossibile da far fallire in modo rumoroso: ogni
    metodo pubblico inghiotte le proprie eccezioni. Un registro che rompe la
    conversazione che sta registrando sarebbe un pessimo affare, e questi dati
    valgono molto meno del fatto che l'assistente risponda.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        self.session_id = uuid.uuid4().hex[:12]
        self._turn_index = 0
        self._model = ""
        self._reset_turn()

    # ── ciclo di vita ────────────────────────────────────────────────────────

    def start_session(self, model: str = "") -> None:
        self.session_id = uuid.uuid4().hex[:12]
        self._turn_index = 0
        self._model = model or self._model
        self._reset_turn()

    def _reset_turn(self) -> None:
        self._t_user_end: float | None = None
        self._t_first_audio: float | None = None
        self._tools: list[dict] = []
        self._interrupted: str | None = None
        self._error: str | None = None

    # ── segnali raccolti durante il turno ────────────────────────────────────

    def user_stopped_speaking(self) -> None:
        """Segna «l'utente stava ancora parlando adesso».

        Chiamata a ogni frammento di trascrizione, quindi l'ultima chiamata
        prima della risposta è, di fatto, la fine del turno dell'utente. Nota
        onesta sulla misura: la trascrizione arriva con un piccolo ritardo
        rispetto alla voce, quindi il tempo calcolato sottostima di poco
        quello percepito. È però lo stesso scarto in ogni misura, e quindi i
        confronti fra prima e dopo una modifica restano validi — che è
        esattamente l'uso previsto.
        """
        if self.enabled:
            self._t_user_end = time.monotonic()

    def first_audio_received(self) -> None:
        """È arrivato il primo campione audio della risposta."""
        if self.enabled and self._t_first_audio is None:
            self._t_first_audio = time.monotonic()

    def tool_used(self, name: str, ok: bool, ms: float, note: str = "") -> None:
        if not self.enabled:
            return
        try:
            self._tools.append({
                "name": name,
                "ok": bool(ok),
                "ms": round(float(ms), 1),
                **({"note": _redact(note)[:200]} if note else {}),
            })
        except Exception:
            pass

    def interrupted(self, kind: str = "voice") -> None:
        if self.enabled:
            self._interrupted = kind

    def failed(self, message: str) -> None:
        if self.enabled:
            self._error = _redact(str(message))[:300]

    # ── scrittura ────────────────────────────────────────────────────────────

    def exchange(self, user_text: str, assistant_text: str,
                 model: str = "") -> None:
        """Chiude il turno e ne scrive la riga. Chiamare a turno completato."""
        if not self.enabled:
            self._reset_turn()
            return
        try:
            user_text = _redact((user_text or "").strip())
            assistant_text = _redact((assistant_text or "").strip())
            # Un turno senza né domanda né risposta non è uno scambio: sono i
            # turn_complete tecnici (saluti di sistema, conferme silenziose di
            # uno strumento) e sporcherebbero i dati senza aggiungere nulla.
            if not user_text and not assistant_text and not self._tools:
                self._reset_turn()
                return

            self._turn_index += 1
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "session": self.session_id,
                "turn": self._turn_index,
                "model": model or self._model,
                "user": user_text,
                "assistant": assistant_text,
            }
            if self._t_user_end is not None and self._t_first_audio is not None:
                delta = (self._t_first_audio - self._t_user_end) * 1000.0
                # Un valore negativo o assurdo significa che i due tempi
                # appartengono a turni diversi: meglio nessun dato che un dato
                # inventato, perché su queste misure si prenderanno decisioni.
                if 0.0 <= delta < 60_000.0:
                    record["reply_ms"] = round(delta)
            if self._tools:
                record["tools"] = self._tools
            if self._interrupted:
                record["interrupted"] = self._interrupted
            if self._error:
                record["error"] = self._error

            self._append(record)
        except Exception:
            pass
        finally:
            self._reset_turn()

    def _append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        path = LOG_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"
        with self._lock:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


# ── riga di comando: statistiche, esportazione, cancellazione ────────────────

def _iter_records():
    if not LOG_DIR.exists():
        return
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Una riga troncata da un'interruzione: si salta quella,
                    # non si perde l'archivio. È il motivo per cui il formato
                    # è JSONL e non un unico oggetto JSON.
                    continue


def _stats() -> None:
    total = sessions = with_tools = interrupted = errors = 0
    latencies: list[int] = []
    tool_counts: dict[str, list[int]] = {}
    seen_sessions = set()

    for r in _iter_records():
        total += 1
        if r.get("session") not in seen_sessions:
            seen_sessions.add(r.get("session"))
            sessions += 1
        if "reply_ms" in r:
            latencies.append(int(r["reply_ms"]))
        if r.get("interrupted"):
            interrupted += 1
        if r.get("error"):
            errors += 1
        for t in r.get("tools", []):
            with_tools += 1
            ok, fail = tool_counts.setdefault(t.get("name", "?"), [0, 0])
            tool_counts[t["name"]] = [ok + bool(t.get("ok")),
                                      fail + (not t.get("ok"))]

    if total == 0:
        print("Nessuno scambio registrato finora.")
        print(f"Cartella: {LOG_DIR}")
        return

    print(f"Scambi registrati : {total}")
    print(f"Sessioni          : {sessions}")
    print(f"Con azioni        : {with_tools}")
    print(f"Interrotti        : {interrupted}")
    print(f"Con errore        : {errors}")

    if latencies:
        latencies.sort()
        mediana = latencies[len(latencies) // 2]
        p90 = latencies[int(len(latencies) * 0.9) - 1]
        print(f"\nTempo di risposta (dal tuo silenzio alla prima voce)")
        print(f"  mediana : {mediana} ms")
        print(f"  p90     : {p90} ms   (9 volte su 10 è più veloce di così)")

    if tool_counts:
        print("\nAzioni più usate")
        for name, (ok, fail) in sorted(tool_counts.items(),
                                       key=lambda kv: -(kv[1][0] + kv[1][1]))[:10]:
            tot = ok + fail
            quota = f"{fail}/{tot} fallite" if fail else "tutte riuscite"
            print(f"  {name:<24} {tot:>4}   ({quota})")

    print(f"\nCartella: {LOG_DIR}")


def _export(out_path: str) -> None:
    """Esporta in formato messaggi, quello che si usa per il fine-tuning."""
    n = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for r in _iter_records():
            if not r.get("user") or not r.get("assistant"):
                continue
            # Uno scambio interrotto è una risposta troncata: pessimo esempio
            # da cui imparare, perché insegnerebbe a fermarsi a metà frase.
            if r.get("interrupted") or r.get("error"):
                continue
            fh.write(json.dumps({"messages": [
                {"role": "user", "content": r["user"]},
                {"role": "assistant", "content": r["assistant"]},
            ]}, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} scambi esportati in {out_path}")
    if n < 1000:
        print("Nota: per un fine-tuning che produca un risultato percepibile\n"
              "servono di norma alcune migliaia di esempi. Continua a raccogliere.")


def _purge() -> None:
    if not LOG_DIR.exists():
        print("Niente da cancellare.")
        return
    files = list(LOG_DIR.glob("*.jsonl"))
    if not files:
        print("Niente da cancellare.")
        return
    print(f"Sto per cancellare {len(files)} file in {LOG_DIR}")
    if input("Scrivi 'si' per confermare: ").strip().lower() not in ("si", "sì"):
        print("Annullato.")
        return
    for f in files:
        f.unlink()
    print("Archivio cancellato.")


def main(argv=None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "stats"

    if cmd == "stats":
        _stats()
    elif cmd == "export":
        _export(argv[1] if len(argv) > 1 else "dialoghi_export.jsonl")
    elif cmd == "purge":
        _purge()
    else:
        print(__doc__.strip().splitlines()[0])
        print("\nUso:")
        print("  python -m core.interaction_log stats            statistiche")
        print("  python -m core.interaction_log export [file]    esporta per il fine-tuning")
        print("  python -m core.interaction_log purge            cancella tutto")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
