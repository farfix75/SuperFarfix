"""
Carry an existing install over from the assistant's old name to FARFIX.

The project used to call itself by another name, and that name ended up in a
few places OUTSIDE this folder: the auto-start entry, the browser profile
folder, the dashboard certificate, scheduled tasks, firewall rules and the
assistant name saved in config/api_keys.json. Renaming the code alone would
have orphaned all of them — auto-start registered twice (two copies launching
at login), browser logins lost, the phone asked to trust a new certificate.

This module is the ONLY place the old identifiers are written down, and it
only ever reads/moves what already exists. Everything it does is idempotent
and can never raise: on a fresh install it finds nothing and does nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_OLD = "jarvis"                 # the previous name — used for migration only
_OLD_UP = _OLD.upper()

NEW_NAME = "FARFIX"
CURRENT_TEXT_MODEL = "gemini-3.8-flash"

# Old identifiers still checked by the code that manages them.
LEGACY_GAME_TASK   = f"{_OLD_UP}_GameUpdater"
LEGACY_GAME_PLIST  = f"com.{_OLD}.gameupdater.plist"
LEGACY_FW_PROG     = f"{_OLD_UP} Dashboard Python"


def legacy_fw_port_rule(port: int) -> str:
    return f"{_OLD_UP} Dashboard Port {port}"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _migrate_config(log) -> None:
    cfg = _base_dir() / "config" / "api_keys.json"
    if not cfg.exists():
        return
    data = json.loads(cfg.read_text(encoding="utf-8"))
    changed = False
    if str(data.get("assistant_name", "")).strip().lower() == _OLD:
        data["assistant_name"] = NEW_NAME
        changed = True
        log(f"assistant name updated to {NEW_NAME}")

    # Text models that Google has retired or closed to new keys answer 404.
    # A config written by an older setup still names them.
    def _retired(m) -> bool:
        m = str(m or "").strip().removeprefix("models/")
        return (m.startswith(("gemini-1.", "gemini-2.0", "gemini-2.5"))
                or m == "gemini-3.1-flash")          # never existed
    if _retired(data.get("llm_model")):
        log(f"text model {data['llm_model']} → {CURRENT_TEXT_MODEL}")
        data["llm_model"] = CURRENT_TEXT_MODEL
        changed = True
    ai = data.get("farfix_ai")
    if isinstance(ai, dict) and _retired(ai.get("google_model")):
        log(f"Gemini model {ai['google_model']} → {CURRENT_TEXT_MODEL}")
        ai["google_model"] = CURRENT_TEXT_MODEL
        changed = True
    if changed:
        cfg.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def _move(old: Path, new: Path, log) -> None:
    if old.exists() and not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        log(f"moved {old.name} → {new.name}")


def _migrate_files(log) -> None:
    certs = _base_dir() / "config" / "certs"
    # Keeping the same certificate means a phone that already trusts the
    # dashboard is not asked to accept a new one.
    _move(certs / f"{_OLD}.key", certs / "farfix.key", log)
    _move(certs / f"{_OLD}.crt", certs / "farfix.crt", log)
    # Browser automation profiles hold the user's logins.
    _move(Path.home() / f".{_OLD}_profiles", Path.home() / ".farfix_profiles", log)


def _migrate_autostart(log) -> None:
    if sys.platform == "win32":
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS)
        try:
            try:
                value, kind = winreg.QueryValueEx(reg, f"{_OLD_UP}_AI")
            except FileNotFoundError:
                return
            try:
                winreg.QueryValueEx(reg, "FARFIX_AI")
            except FileNotFoundError:
                winreg.SetValueEx(reg, "FARFIX_AI", 0, kind, value)
            winreg.DeleteValue(reg, f"{_OLD_UP}_AI")
            log("auto-start entry renamed")
        finally:
            winreg.CloseKey(reg)
    elif sys.platform == "darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        old = agents / f"com.{_OLD}.assistant.plist"
        new = agents / "com.farfix.assistant.plist"
        if old.exists():
            if not new.exists():
                new.write_text(old.read_text(encoding="utf-8").replace(
                    f"com.{_OLD}.assistant", "com.farfix.assistant"), encoding="utf-8")
            old.unlink()
            log("auto-start entry renamed")
    else:
        auto = Path.home() / ".config" / "autostart"
        _move(auto / f"{_OLD}.desktop", auto / "farfix.desktop", log)


def migrate(log=print) -> None:
    """Run every step; each one is independent and failure-proof."""
    say = lambda m: log(f"[Migrate] {m}")
    for step in (_migrate_config, _migrate_files, _migrate_autostart):
        try:
            step(say)
        except Exception as e:
            say(f"{step.__name__} skipped: {e}")
