"""
core/local_llm.py — LLM interna locale per SuperFarfix8
=========================================================

Una LLM leggera sempre carica in RAM per:

  1. **Intent detection rapida** (<400 ms): capisce "apri Chrome", "cerca meteo",
     "scrivi codice" senza aspettare Gemini Live.

  2. **Fallback testuale**: quando Gemini Live è offline, risponde in modo
     autonomo a domande semplici.

  3. **Pre-processing comandi**: normalizza e classifica i comandi prima di
     inviarli alla pipeline Live.

Backend:
  - Usa Ollama se disponibile (modello configurato, default: llama3.2)
  - Fallback su OpenAI-compatible (LM Studio, LocalAI) se provider=openai
  - Se nessuno è disponibile, `is_available()` restituisce False e tutte
    le chiamate sollevano LocalLLMUnavailable (non crashano l'app)

Performance:
  - Warm-up automatico al primo import (carica il modello in VRAM/RAM)
  - Cache LRU per risposte ripetute (evita round-trip per la stessa domanda)
  - num_predict limitato: risposte brevi in 1-3 frasi

Usage:
    from core.local_llm import quick_intent, quick_text, is_available

    if is_available():
        intent = quick_intent("apri spotify e metti musica jazz")
        # --> {"action": "open_app", "target": "spotify", "context": "jazz music"}
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

_CONFIG_PATH = _get_base_dir() / "config" / "api_keys.json"

_DEFAULTS: dict[str, Any] = {
    "llm_url":      "http://localhost:11434",
    "llm_model":    "llama3.2",
    "llm_provider": "ollama",
}

# ---------------------------------------------------------------------------
# LRU response cache
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_MAX   = 64
_CACHE_TTL_S = 300.0   # 5 min
_CACHE_LOCK  = threading.Lock()


def _load_cfg() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _settings() -> tuple[str, str, str]:
    """Returns (base_url, model, provider)."""
    cfg      = _load_cfg()
    url      = cfg.get("llm_url",      _DEFAULTS["llm_url"]).rstrip("/")
    model    = cfg.get("llm_model",    _DEFAULTS["llm_model"])
    raw_prov = cfg.get("llm_provider", _DEFAULTS["llm_provider"]).strip().lower()
    provider = "openai" if raw_prov in ("openai", "lmstudio", "localai", "jan", "llamacpp") else "ollama"
    return url, model, provider


def _cache_key(prompt: str, system: str | None) -> str:
    return hashlib.sha256(f"{system or ''}||{prompt}".encode()).hexdigest()[:16]


def _cache_get(key: str) -> str | None:
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        ts, text = item
        if time.monotonic() - ts > _CACHE_TTL_S:
            _CACHE.pop(key, None)
            return None
        return text


def _cache_set(key: str, text: str) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), text)
        if len(_CACHE) > _CACHE_MAX:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)

# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

_avail_cache: bool | None = None
_avail_lock   = threading.Lock()
_avail_last   = 0.0
_AVAIL_RECHECK_S = 30.0


class LocalLLMUnavailable(RuntimeError):
    """Raised when no local LLM server is reachable."""


def is_available() -> bool:
    """
    Returns True if the configured local LLM server is reachable.
    Result is cached for 30 s to avoid spamming health checks.
    """
    global _avail_cache, _avail_last
    with _avail_lock:
        if _avail_cache is not None and time.monotonic() - _avail_last < _AVAIL_RECHECK_S:
            return _avail_cache
    url, _, provider = _settings()
    try:
        if provider == "openai":
            ok = requests.get(f"{url}/v1/models", timeout=3).ok
        else:
            ok = requests.get(f"{url}/api/tags", timeout=3).ok
    except Exception:
        ok = False
    with _avail_lock:
        _avail_cache = ok
        _avail_last  = time.monotonic()
    return ok


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------

_warmed_up = threading.Event()


def _warmup_thread() -> None:
    """
    Pre-loads the model and primes Ollama's KV prefix cache.
    Runs once in a daemon thread so it never blocks the UI.
    """
    url, model, provider = _settings()
    try:
        if provider == "openai":
            if not requests.get(f"{url}/v1/models", timeout=5).ok:
                return
            payload = {
                "model":      model,
                "messages":   [{"role": "user", "content": "hi"}],
                "stream":     False,
                "max_tokens": 1,
            }
            requests.post(f"{url}/v1/chat/completions", json=payload, timeout=60)
        else:
            # Ollama: keep_alive=-1 keeps model in VRAM across calls
            payload = {
                "model":      model,
                "messages":   [{"role": "user", "content": "hi"}],
                "stream":     False,
                "keep_alive": -1,
                "options":    {"num_predict": 1, "num_gpu": 99},
            }
            requests.post(f"{url}/api/chat", json=payload, timeout=90)
        print(f"[LocalLLM] '{model}' warm and ready.")
        _warmed_up.set()
        # Update availability cache now that we confirmed it's up
        global _avail_cache, _avail_last
        with _avail_lock:
            _avail_cache = True
            _avail_last  = time.monotonic()
    except Exception as e:
        print(f"[LocalLLM] Warmup failed (non-fatal): {e}")


_warmup_started = False
_warmup_lock    = threading.Lock()


def _ensure_warmup() -> None:
    """Start the warmup thread once, silently. Idempotent."""
    global _warmup_started
    with _warmup_lock:
        if _warmup_started:
            return
        _warmup_started = True
    t = threading.Thread(target=_warmup_thread, daemon=True, name="local-llm-warmup")
    t.start()


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

_INTENT_SYSTEM = """\
You are a fast intent classifier for a voice/text assistant.
Extract the user intent in JSON with these fields:
  action   -- short snake_case verb: open_app, web_search, play_music, set_volume,
              check_weather, set_reminder, file_operation, code_task, general_chat, unknown
  target   -- main object/app/topic (string, or null)
  context  -- any extra detail relevant to the action (string, or null)

Return ONLY valid JSON, no explanation, no markdown fences.
Example: {"action": "open_app", "target": "spotify", "context": null}
"""

_FALLBACK_SYSTEM = """\
You are FARFIX, a concise personal AI assistant.
Answer in 1-2 sentences maximum. Be direct and helpful.
If you do not know something, say so honestly.
"""


def _call(
    prompt:     str,
    system:     str | None = None,
    max_tokens: int = 256,
    timeout:    int = 30,
    use_cache:  bool = True,
) -> str:
    """
    Internal call: routes to Ollama or OpenAI-compatible.
    Raises LocalLLMUnavailable if server is unreachable.
    """
    if not is_available():
        raise LocalLLMUnavailable("Local LLM server is not reachable.")

    key = _cache_key(prompt, system)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    url, model, provider = _settings()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        if provider == "openai":
            endpoint = f"{url}/v1/chat/completions"
            payload = {
                "model":       model,
                "messages":    messages,
                "stream":      False,
                "max_tokens":  max_tokens,
                "temperature": 0.1,
            }
            resp = requests.post(endpoint, json=payload, timeout=timeout)
            resp.raise_for_status()
            choices = resp.json().get("choices") or []
            if not choices:
                raise RuntimeError("No choices returned")
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
        else:
            # Ollama native
            endpoint = f"{url}/api/chat"
            payload = {
                "model":      model,
                "messages":   messages,
                "stream":     False,
                "keep_alive": -1,
                "options":    {"num_predict": max_tokens, "num_gpu": 99, "temperature": 0.1},
            }
            resp = requests.post(endpoint, json=payload, timeout=timeout)
            resp.raise_for_status()
            text = (resp.json().get("message", {}).get("content") or "").strip()

        if not text:
            raise RuntimeError("Empty response from local LLM")

        if use_cache:
            _cache_set(key, text)
        return text

    except requests.exceptions.ConnectionError as e:
        # Invalidate availability cache so next call re-checks immediately
        global _avail_cache
        with _avail_lock:
            _avail_cache = None
        raise LocalLLMUnavailable(f"Connection lost to local LLM: {e}") from e
    except LocalLLMUnavailable:
        raise
    except Exception as e:
        raise RuntimeError(f"Local LLM call failed: {e}") from e


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def quick_intent(text: str) -> dict:
    """
    Classify user intent in <400 ms.

    Returns a dict::

        {
            "action":  str,         # snake_case verb
            "target":  str | None,
            "context": str | None,
        }

    Falls back to {"action": "unknown", "target": None, "context": None}
    if the LLM is unavailable or returns non-JSON.

    Example::

        >>> quick_intent("apri spotify e metti jazz")
        {"action": "open_app", "target": "spotify", "context": "jazz music"}
    """
    _ensure_warmup()
    fallback: dict = {"action": "unknown", "target": None, "context": None}

    if not text or not text.strip():
        return fallback

    try:
        raw = _call(
            prompt=text.strip(),
            system=_INTENT_SYSTEM,
            max_tokens=80,
            timeout=15,
            use_cache=True,
        )
        # Strip markdown fences if the model added them
        raw = raw.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        if not isinstance(result, dict):
            return fallback
        return {
            "action":  str(result.get("action")  or "unknown"),
            "target":  result.get("target")  or None,
            "context": result.get("context") or None,
        }
    except LocalLLMUnavailable:
        return fallback
    except (json.JSONDecodeError, Exception) as e:
        print(f"[LocalLLM] intent parse error: {e}")
        return fallback


def quick_text(
    prompt:     str,
    system:     str | None = None,
    max_tokens: int = 256,
    timeout:    int = 30,
    use_cache:  bool = True,
) -> str:
    """
    Generate a short text response from the local LLM.

    Useful as Gemini-offline fallback for simple questions or when the
    Live session is being rebuilt after a connection drop.

    Raises:
        LocalLLMUnavailable -- if the server is not reachable.
        RuntimeError        -- on any other generation error.

    Example::

        >>> quick_text("Qual e' la capitale della Francia?")
        "Parigi e' la capitale della Francia."
    """
    _ensure_warmup()
    return _call(
        prompt=prompt,
        system=system or _FALLBACK_SYSTEM,
        max_tokens=max_tokens,
        timeout=timeout,
        use_cache=use_cache,
    )


def clear_cache() -> None:
    """Clear the LRU response cache (e.g. after a config change)."""
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Auto-warmup on import
# ---------------------------------------------------------------------------
# Importing this module starts the background warmup thread immediately,
# so the model is loaded in VRAM by the time the first real call arrives.
_ensure_warmup()
