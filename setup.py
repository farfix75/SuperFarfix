"""FARFIX / SUPERFARFIX installer — graphical multi-AI configuration."""
from __future__ import annotations

import json
import platform
import subprocess

try:
    import core.win_subprocess  # noqa: F401  (Windows subprocess fixes)
except Exception:
    pass
import sys
from pathlib import Path

OS = platform.system()
HERE = Path(__file__).resolve().parent
MIN_PY = (3, 11)
CFG_PATH = HERE / "config" / "api_keys.json"


def run(label: str, args: list[str]) -> None:
    print(f"\n▶ {label}")
    subprocess.run(args, check=True)


def read_cfg() -> dict:
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_cfg(data: dict) -> None:
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CFG_PATH.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def _merge_ai(data: dict, updates: dict) -> dict:
    ai = data.get("farfix_ai") if isinstance(data.get("farfix_ai"), dict) else {}
    ai.update(updates)
    data["farfix_ai"] = ai
    return data


def configure_gui() -> None:
    import tkinter as tk
    from tkinter import ttk, messagebox

    data = read_cfg()
    ai = data.get("farfix_ai") if isinstance(data.get("farfix_ai"), dict) else {}

    win = tk.Tk()
    win.title("FARFIX AI ENGINE — Configurazione")
    win.geometry("760x820")
    win.minsize(700, 740)
    win.configure(bg="#050b12")

    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure("TLabel", background="#050b12", foreground="#dcecff", font=("Segoe UI", 10))
    style.configure("Title.TLabel", background="#050b12", foreground="#7cf0ff", font=("Segoe UI", 22, "bold"))
    style.configure("Sub.TLabel", background="#050b12", foreground="#8fa8bf", font=("Segoe UI", 9))
    style.configure("TLabelframe", background="#050b12", foreground="#7cf0ff")
    style.configure("TLabelframe.Label", background="#050b12", foreground="#7cf0ff", font=("Segoe UI", 10, "bold"))
    style.configure("TButton", font=("Segoe UI", 11, "bold"), padding=10)
    style.configure("TEntry", fieldbackground="#0b1824", foreground="#ffffff")
    style.configure("TCombobox", fieldbackground="#0b1824", foreground="#ffffff")

    canvas = tk.Canvas(win, bg="#050b12", highlightthickness=0)
    scroll = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
    frame = ttk.Frame(canvas, padding=24)
    frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=frame, anchor="nw")
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    ttk.Label(frame, text="FARFIX  ·  SUPERFARFIX", style="Title.TLabel").pack(anchor="w")
    ttk.Label(
        frame,
        text="Configura xAI Grok • OpenAI • Gemini Live • Claude • Ollama",
        style="Sub.TLabel",
    ).pack(anchor="w", pady=(0, 16))

    vars: dict[str, tk.StringVar] = {}

    def field(parent, label, key, default="", secret=False, source="root"):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 2))
        cur = data.get(key, "") if source == "root" else ai.get(key, default)
        if not cur:
            cur = default
        v = tk.StringVar(value=str(cur or ""))
        e = ttk.Entry(parent, textvariable=v, show="•" if secret else "", width=72)
        e.pack(fill="x")
        vars[key] = v
        return e

    box = ttk.LabelFrame(frame, text="1  Motore predefinito", padding=12)
    box.pack(fill="x", pady=6)
    pv = tk.StringVar(value=str(ai.get("provider", "auto")))
    vars["provider"] = pv
    ttk.Combobox(
        box,
        textvariable=pv,
        values=["auto", "grok", "openai", "google", "anthropic", "xkiro",
                "ollama", "compatible"],
        state="readonly",
    ).pack(fill="x")

    box = ttk.LabelFrame(frame, text="2  Chiavi API (almeno una è sufficiente)", padding=12)
    box.pack(fill="x", pady=6)
    field(box, "xAI / Grok API Key  (https://console.x.ai)", "grok_api_key", secret=True)
    field(box, "OpenAI / ChatGPT API Key  (https://platform.openai.com)", "openai_api_key", secret=True)

    box = ttk.LabelFrame(frame, text="3  Altre connessioni (opzionali)", padding=12)
    box.pack(fill="x", pady=6)
    field(box, "Google Gemini API Key (voce Live)", "gemini_api_key", secret=True)
    field(box, "Anthropic / Claude API Key", "anthropic_api_key", secret=True)
    field(box, "xKiro API Key — un gateway, tutti i modelli  (https://xkiro.com/dashboard/api/keys)", "xkiro_api_key", secret=True)
    field(box, "Ollama URL", "ollama_url", "http://localhost:11434", source="ai")
    field(box, "Ollama API Key (opzionale)", "ollama_api_key", secret=True)

    box = ttk.LabelFrame(frame, text="4  Modelli", padding=12)
    box.pack(fill="x", pady=6)
    field(box, "Modello Grok", "grok_model", "grok-4", source="ai")
    field(box, "Modello OpenAI", "openai_model", "gpt-4.1", source="ai")
    field(box, "Modello Gemini", "google_model", "gemini-3.8-flash", source="ai")
    field(box, "Modello Claude", "anthropic_model", "claude-sonnet-4-5", source="ai")
    field(box, "Modello Ollama", "ollama_model", "llama3.2", source="ai")
    # Sempre "vendor/modello": il nome nudo su xKiro non viene risolto.
    field(box, "Modello xKiro  (vendor/modello)", "xkiro_model",
          "openai/gpt-5.6-sol", source="ai")
    field(box, "URL OpenAI-compatible", "compatible_url", "http://localhost:1234", source="ai")
    field(box, "Modello OpenAI-compatible", "compatible_model", "local-model", source="ai")

    status = tk.StringVar(value="Le chiavi restano solo sul tuo computer (config/api_keys.json).")
    ttk.Label(frame, textvariable=status, style="Sub.TLabel").pack(anchor="w", pady=10)

    def save():
        grok = vars["grok_api_key"].get().strip()
        oai = vars["openai_api_key"].get().strip()
        if grok:
            data["grok_api_key"] = grok
        if oai:
            data["openai_api_key"] = oai
        if not any([
            grok, oai,
            vars["gemini_api_key"].get().strip(),
            vars["anthropic_api_key"].get().strip(),
            vars["xkiro_api_key"].get().strip(),
            vars["ollama_url"].get().strip(),
        ]):
            messagebox.showwarning(
                "FARFIX",
                "Configura almeno un motore: Gemini, OpenAI, Grok, Claude, xKiro oppure Ollama."
            )
            return
        gem = vars["gemini_api_key"].get().strip()
        if gem:
            data["gemini_api_key"] = gem
        ant = vars["anthropic_api_key"].get().strip()
        if ant:
            data["anthropic_api_key"] = ant
        ollama_key = vars["ollama_api_key"].get().strip()
        if ollama_key:
            data["ollama_api_key"] = ollama_key
        _merge_ai(data, {
            "provider": pv.get(),
            "grok_model": vars["grok_model"].get().strip() or "grok-4",
            "openai_model": vars["openai_model"].get().strip() or "gpt-4.1",
            "google_model": vars["google_model"].get().strip() or "gemini-3.8-flash",
            "anthropic_model": vars["anthropic_model"].get().strip() or "claude-sonnet-4-5",
            "ollama_model": vars["ollama_model"].get().strip() or "llama3.2",
            "ollama_url": vars["ollama_url"].get().strip() or "http://localhost:11434",
            "compatible_url": vars["compatible_url"].get().strip() or "http://localhost:1234",
            "compatible_model": vars["compatible_model"].get().strip() or "local-model",
            "timeout": 90,
        })
        if not data.get("os_system"):
            data["os_system"] = {"Darwin": "mac", "Windows": "windows"}.get(OS, "linux")
        write_cfg(data)
        status.set("Configurazione salvata.")
        messagebox.showinfo("FARFIX", "Configurazione salvata. Puoi avviare main.py.")
        win.destroy()

    ttk.Button(frame, text="SALVA CONFIGURAZIONE E CONTINUA", command=save).pack(fill="x", pady=(4, 16))
    win.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
    win.mainloop()


def configure_cli() -> None:
    data = read_cfg()
    ai = data.get("farfix_ai") if isinstance(data.get("farfix_ai"), dict) else {}
    print("\n" + "=" * 72)
    print(" FARFIX AI ENGINE — CONFIGURAZIONE GROK + OPENAI")
    print("=" * 72)

    def ask(label, key, default="", secret=False, store="root"):
        import getpass
        cur = data.get(key, "") if store == "root" else ai.get(key, "")
        shown = " [gia configurata]" if cur else ""
        try:
            raw = (getpass.getpass if secret else input)(f"{label}{shown}: ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        return raw or cur or default

    grok = ask("xAI / Grok API key", "grok_api_key", secret=True)
    oai = ask("OpenAI / ChatGPT API key", "openai_api_key", secret=True)
    if grok:
        data["grok_api_key"] = grok
    if oai:
        data["openai_api_key"] = oai
    if not any([grok, oai, data.get("gemini_api_key"), ai.get("ollama_url")]):
        raise SystemExit("Configura almeno un motore AI.")
    gem = ask("Google Gemini API key (opzionale, voce Live)", "gemini_api_key", secret=True)
    if gem:
        data["gemini_api_key"] = gem
    _merge_ai(data, {
        "provider": ask("Motore [auto/grok/openai/google/anthropic/ollama]", "provider", "auto", store="ai"),
        "grok_model": ask("Modello Grok", "grok_model", "grok-4", store="ai"),
        "openai_model": ask("Modello OpenAI", "openai_model", "gpt-4.1", store="ai"),
        "google_model": ask("Modello Gemini", "google_model", "gemini-3.8-flash", store="ai"),
        "ollama_url": ask("Ollama URL", "ollama_url", "http://localhost:11434", store="ai"),
        "ollama_model": ask("Modello Ollama", "ollama_model", "llama3.2", store="ai"),
        "timeout": 90,
    })
    if not data.get("os_system"):
        data["os_system"] = {"Darwin": "mac", "Windows": "windows"}.get(OS, "linux")
    write_cfg(data)


def main() -> None:
    v = sys.version_info[:2]
    print(f"FARFIX / SUPERFARFIX setup — {OS}, Python {v[0]}.{v[1]}")
    if v < MIN_PY:
        raise SystemExit("Python 3.11+ richiesto.")
    run("Installing Python dependencies", [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    try:
        run("Installing Playwright browsers", [sys.executable, "-m", "playwright", "install", "chromium", "firefox"])
    except Exception as e:
        print(f"Playwright non installato: {e}")
    try:
        configure_gui()
    except Exception as e:
        print(f"GUI installer non disponibile ({e}); uso configurazione testuale.")
        configure_cli()
    print("\nINSTALLAZIONE COMPLETATA")
    print("   Avvia con: python main.py")


if __name__ == "__main__":
    main()
