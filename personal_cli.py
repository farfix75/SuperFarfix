#!/usr/bin/env python3
"""FARFIX — assistente personale in terminale (senza GUI)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Consenti import locali
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import core.win_subprocess  # noqa: E402,F401  (Windows subprocess fixes)

from core.ai_router import generate, get_config, health, provider_status_line
from memory.memory_manager import format_memory_for_prompt, load_memory, update_memory


SYSTEM = """Sei {name}, l'assistente personale di {user}.
Parli in italiano se l'utente parla italiano, altrimenti nella lingua dell'utente.
Sei diretto, utile e ricordi i fatti salvati in memoria.
Data e ora locali: {now}
Memoria:
{memory}
"""


def _names() -> tuple[str, str]:
    cfg_path = HERE / "config" / "api_keys.json"
    name, user = "FARFIX", "utente"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        name = data.get("assistant_name") or name
        user = data.get("user_name") or user
    except Exception:
        pass
    return name, user


def _system() -> str:
    name, user = _names()
    mem = ""
    try:
        mem = format_memory_for_prompt(load_memory()) or "(vuota)"
    except Exception:
        mem = "(non disponibile)"
    return SYSTEM.format(
        name=name, user=user,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
        memory=mem,
    )


def chat_once(text: str) -> str:
    return generate(text, system=_system())


def main() -> int:
    print("=" * 56)
    print("  FARFIX  ·  Assistente personale (CLI)")
    print("=" * 56)
    print(provider_status_line())
    h = health()
    online = [k for k, v in h.items() if v.get("configured")]
    print("Provider configurati:", ", ".join(online) or "(nessuno — aggiungi una API key)")
    print("Comandi: /memoria  /esci  /help")
    print()
    history: list[dict] = []
    while True:
        try:
            raw = input("Tu > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nA presto.")
            return 0
        if not raw:
            continue
        low = raw.lower()
        if low in ("/esci", "/exit", "/quit", "esci"):
            print("A presto.")
            return 0
        if low in ("/help", "/aiuto"):
            print("Scrivi qualsiasi cosa. /memoria mostra i fatti salvati. /esci chiude.")
            continue
        if low.startswith("/memoria"):
            try:
                print(format_memory_for_prompt(load_memory()) or "(memoria vuota)")
            except Exception as e:
                print("Memoria:", e)
            continue
        if raw.lower().startswith("ricorda ") and ":" in raw:
            # ricorda categoria.chiave: valore
            try:
                body = raw.split(" ", 1)[1]
                key, val = body.split(":", 1)
                parts = key.strip().split(".", 1)
                cat = parts[0] if len(parts) == 2 else "notes"
                k = parts[1] if len(parts) == 2 else parts[0]
                update_memory({cat: {k.strip(): {"value": val.strip()}}})
                print(f"(salvato in memoria: {cat}.{k.strip()})")
            except Exception as e:
                print("Non sono riuscito a salvare:", e)
            continue
        try:
            answer = generate(raw, system=_system(), history=history[-12:])
        except Exception as e:
            print("Errore AI:", e)
            print("Controlla le API key in config/api_keys.json o avvia Ollama.")
            continue
        history.append({"role": "user", "content": raw})
        history.append({"role": "assistant", "content": answer})
        print(f"FARFIX > {answer}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
