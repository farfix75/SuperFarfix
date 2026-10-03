"""
FARFIX Multi-AI Router
======================
A provider-neutral text engine for SUPERFARFIX / FARFIX.

Providers:
  - google  : existing Gemini REST/SDK path (google-genai)
  - openai  : OpenAI API (ChatGPT models through the OpenAI API)
  - ollama  : local Ollama HTTP API, no cloud key required
  - anthropic: Claude Messages API
  - compatible: any OpenAI-compatible local/server endpoint

The router never claims a provider succeeded unless the HTTP/SDK call returned
usable text. It supports explicit selection and AUTO fallback.
Voice/Live Gemini remains untouched; this engine is for planning, coding,
analysis, structured tasks and other text workloads.
"""
from __future__ import annotations
from collections import OrderedDict
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"
# Keys created by the secure setup flow live here.  They are deliberately read
# without adding them to os.environ: subprocesses never inherit the credential.
ENV_FILE = BASE_DIR / ".env.local"

PROVIDERS = ("auto", "grok", "google", "openai", "anthropic", "xkiro", "ollama",
             "compatible")

DEFAULTS = {
    "provider": "auto",
    "google_model": "gemini-3.8-flash",
    "openai_model": "gpt-5.6-terra",
    "openai_fast_model": "gpt-5.6-luna",
    "openai_smart_model": "gpt-5.6-terra",
    "response_profile": "adaptive",
    "grok_model": "grok-4",
    "grok_url": "https://api.x.ai/v1",
    "anthropic_model": "claude-sonnet-4-5",
    # xKiro è un gateway: una sola chiave per raggiungere i modelli di più
    # fornitori. Gli identificativi sono sempre nella forma "vendor/modello" —
    # il nome nudo non viene risolto e restituisce 404.
    "xkiro_model": "openai/gpt-5.6-sol",
    "xkiro_url": "https://api.xkiro.com/v1",
    "ollama_model": "llama3.2",
    "compatible_model": "local-model",
    "ollama_url": "http://localhost:11434",
    "compatible_url": "http://localhost:1234",
    "timeout": 90,
    "fast_timeout": 18,
}

_ENV_CACHE: dict[str, str] = {}
_ENV_MTIME_NS: int | None = None
_CACHE_LOCK = threading.RLock()
_RESPONSE_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()
_CACHE_MAX_ITEMS = 48
_DYNAMIC_QUERY = re.compile(
    r"\b(oggi|adesso|ora|meteo|notizie|news|prezzo|quotazione|traffico|"
    r"today|now|weather|latest|current|price)\b", re.IGNORECASE)
_COMPLEX_QUERY = re.compile(
    r"\b(analizza|analisi|confronta|confronto|piano|strategia|progetta|"
    r"codice|debug|ricerca|riassumi|valuta|spiega.*perch|implement|"
    r"analy[sz]e|compare|plan|strategy|design|code|debug|research|"
    r"summari[sz]e|evaluate)\b", re.IGNORECASE)


def _load() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")


def get_config() -> dict:
    root = _load()
    cfg = root.get("farfix_ai")
    if not isinstance(cfg, dict):
        cfg = {}
    out = DEFAULTS.copy()
    out.update({k: v for k, v in cfg.items() if k in out})
    # Permit the legacy/global keys too.
    if root.get("llm_model") and out["ollama_model"] == DEFAULTS["ollama_model"]:
        out["ollama_model"] = root["llm_model"]
    return out


def save_config(**changes) -> dict:
    root = _load()
    cur = root.get("farfix_ai")
    if not isinstance(cur, dict):
        cur = {}
    for k, v in changes.items():
        if k in DEFAULTS:
            cur[k] = v
    root["farfix_ai"] = cur
    _save(root)
    _clear_response_cache()
    return get_config()


def get_provider() -> str:
    p = str(get_config()["provider"]).strip().lower()
    return p if p in PROVIDERS else "auto"


def _env_values() -> dict[str, str]:
    """Read .env.local without exporting its values to child processes.

    The file is parsed only when it changes.  This keeps key access cheap on a
    live session while allowing the user to rotate a key without restarting.
    """
    global _ENV_CACHE, _ENV_MTIME_NS
    try:
        stamp = ENV_FILE.stat().st_mtime_ns
    except OSError:
        stamp = None
    if stamp == _ENV_MTIME_NS:
        return _ENV_CACHE

    values: dict[str, str] = {}
    if stamp is not None:
        try:
            for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                key, sep, value = line.partition("=")
                key = key.strip()
                if not sep or not key:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key] = value
        except (OSError, UnicodeError):
            values = {}

    _ENV_CACHE = values
    _ENV_MTIME_NS = stamp
    return values


def _key(name: str) -> str:
    """Return a credential without logging or persisting it.

    A process environment variable remains the explicit administrator
    override.  Otherwise the secure local env file is preferred over the
    legacy JSON configuration so existing keys remain valid and untouched.
    """
    return os.getenv(name, "") or _env_values().get(name, "") or str(_load().get({
        "OPENAI_API_KEY": "openai_api_key",
        "GROK_API_KEY": "grok_api_key",
        "XAI_API_KEY": "grok_api_key",
        "GEMINI_API_KEY": "gemini_api_key",
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "OLLAMA_API_KEY": "ollama_api_key",
        "XKIRO_API_KEY": "xkiro_api_key",
    }.get(name, ""), "")).strip()


def _clear_response_cache() -> None:
    with _CACHE_LOCK:
        _RESPONSE_CACHE.clear()


def _cache_key(prompt: str, system: str | None, history, provider: str) -> str:
    payload = json.dumps({
        "prompt": prompt,
        "system": system or "",
        "history": history or [],
        "provider": provider,
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cached_response(key: str, ttl: float = 75.0) -> str | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        item = _RESPONSE_CACHE.get(key)
        if not item:
            return None
        created, text = item
        if now - created > ttl:
            _RESPONSE_CACHE.pop(key, None)
            return None
        _RESPONSE_CACHE.move_to_end(key)
        return text


def _remember_response(key: str, text: str) -> None:
    with _CACHE_LOCK:
        _RESPONSE_CACHE[key] = (time.monotonic(), text)
        _RESPONSE_CACHE.move_to_end(key)
        while len(_RESPONSE_CACHE) > _CACHE_MAX_ITEMS:
            _RESPONSE_CACHE.popitem(last=False)


def _compact_history(history, budget: int = 24_000) -> list[dict]:
    """Keep the newest useful turns inside a predictable request budget."""
    if not history:
        return []
    kept: list[dict] = []
    used = 0
    for item in reversed(history):
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        text = str(item.get("content", "")).strip()
        if not text:
            continue
        text = text[-8_000:]
        if kept and used + len(text) > budget:
            break
        kept.append({"role": item["role"], "content": text})
        used += len(text)
    return list(reversed(kept))


def _messages(prompt: str, system: str | None, history=None) -> list[dict]:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(_compact_history(history))
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _openai_chat(base_url: str, api_key: str, model: str, prompt: str,
                 system: str | None, history=None, timeout=90,
                 reasoning_effort: str | None = None) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": _messages(prompt, system, history),
        "temperature": 0.2,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    res = requests.post(url, headers=headers, json=payload, timeout=timeout)
    res.raise_for_status()
    data = res.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("provider returned no choices")
    msg = choices[0].get("message") or {}
    text = msg.get("content")
    if isinstance(text, list):
        text = "".join(x.get("text","") if isinstance(x,dict) else str(x) for x in text)
    if not str(text or "").strip():
        raise RuntimeError("provider returned empty text")
    return str(text).strip()


def _ollama(prompt, system, history, cfg):
    base = str(cfg.get("ollama_url") or "http://localhost:11434").rstrip("/")
    # Ollama native chat API (not /chat/completions).
    url = base + "/api/chat"
    payload = {
        "model": cfg["ollama_model"],
        "messages": _messages(prompt, system, history),
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    key = _key("OLLAMA_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    res = requests.post(url, headers=headers, json=payload, timeout=int(cfg["timeout"]))
    res.raise_for_status()
    data = res.json()
    text = ((data.get("message") or {}).get("content")) or data.get("response")
    if not str(text or "").strip():
        # Fallback: OpenAI-compatible /v1 on the same host
        return _openai_chat(base + "/v1", key or "ollama", cfg["ollama_model"],
                            prompt, system, history, int(cfg["timeout"]))
    return str(text).strip()


def _compatible(prompt, system, history, cfg):
    return _openai_chat(cfg["compatible_url"], "", cfg["compatible_model"],
                         prompt, system, history, int(cfg["timeout"]))


def _google(prompt, system, history, cfg):
    from google import genai
    key = _key("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Gemini API key is not configured")
    from google.genai import types
    # With a deadline: the SDK otherwise waits forever on a stuck request.
    client = genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=int(max(10, float(cfg.get("timeout", 90))) * 1000)))
    contents = []
    if history:
        for item in _compact_history(history):
            contents.append({"role": item.get("role","user"),
                             "parts":[{"text":str(item.get("content",""))}]})
    contents.append({"role":"user","parts":[{"text":prompt}]})
    gen_cfg = types.GenerateContentConfig(
        system_instruction=system or None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    model = cfg["google_model"]
    try:
        resp = client.models.generate_content(model=model, contents=contents, config=gen_cfg)
    except Exception as e:
        # A retired model name saved in the user's config answers 404. Fall
        # back to the current default instead of failing every request.
        msg = str(e)
        if ("404" in msg or "NOT_FOUND" in msg) and model != DEFAULTS["google_model"]:
            print(f"[AI] {model} is not available — using {DEFAULTS['google_model']}")
            resp = client.models.generate_content(
                model=DEFAULTS["google_model"], contents=contents, config=gen_cfg)
        else:
            raise
    text = getattr(resp, "text", None)
    if not str(text or "").strip():
        raise RuntimeError("Gemini returned empty text")
    return str(text).strip()


def _anthropic(prompt, system, history, cfg):
    key = _key("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Claude/Anthropic API key is not configured")
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError("Pacchetto anthropic non installato") from e
    client = Anthropic(api_key=key)
    messages = []
    if history:
        for item in _compact_history(history):
            role = item.get("role") if isinstance(item, dict) else None
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": str(item.get("content", ""))})
    messages.append({"role": "user", "content": prompt})
    kwargs = {
        "model": cfg["anthropic_model"],
        "max_tokens": 4096,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)
    parts = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("Claude returned empty text")
    return text


def _openai(prompt, system, history, cfg, *, model: str | None = None,
            timeout: int | None = None, reasoning_effort: str | None = None):
    key = _key("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OpenAI API key is not configured")
    return _openai_chat("https://api.openai.com/v1", key,
                        model or cfg["openai_model"], prompt, system, history,
                        int(timeout or cfg["timeout"]), reasoning_effort)


def _xkiro(prompt, system, history, cfg):
    """Gateway xKiro: formato OpenAI Chat Completions, una chiave per tutti i modelli."""
    key = _key("XKIRO_API_KEY")
    if not key:
        raise RuntimeError("XKIRO_API_KEY mancante")
    model = str(cfg.get("xkiro_model") or DEFAULTS["xkiro_model"])
    if "/" not in model:
        # Errore facilissimo da fare e diagnosi poco ovvia: il gateway
        # risponderebbe 404 not_found, che sembra un modello inesistente.
        raise RuntimeError(
            f"Il modello xKiro deve essere nella forma 'vendor/modello' "
            f"(per esempio 'openai/gpt-5.6-sol'); ricevuto '{model}'")
    return _openai_chat(cfg.get("xkiro_url") or DEFAULTS["xkiro_url"], key, model,
                        prompt, system, history, cfg["timeout"])


def _grok(prompt, system, history, cfg):
    key = _key("GROK_API_KEY") or _key("XAI_API_KEY")
    if not key:
        raise RuntimeError("xAI Grok API key is not configured")
    return _openai_chat(cfg.get("grok_url") or "https://api.x.ai/v1", key,
                        cfg.get("grok_model") or "grok-4",
                        prompt, system, history, int(cfg["timeout"]))


def health() -> dict[str, dict[str, Any]]:
    cfg = get_config()
    result = {}
    # Keys/config are checked without making an expensive generation request.
    result["google"] = {"configured": bool(_key("GEMINI_API_KEY")),
                        "model": cfg["google_model"]}
    result["openai"] = {"configured": bool(_key("OPENAI_API_KEY")),
                        "model": cfg["openai_model"]}
    result["grok"] = {"configured": bool(_key("GROK_API_KEY") or _key("XAI_API_KEY")),
                      "model": cfg.get("grok_model", "grok-4")}
    result["anthropic"] = {"configured": bool(_key("ANTHROPIC_API_KEY")),
                            "model": cfg["anthropic_model"]}
    result["xkiro"] = {"configured": bool(_key("XKIRO_API_KEY")),
                       "model": cfg.get("xkiro_model", DEFAULTS["xkiro_model"]),
                       "url": cfg.get("xkiro_url", DEFAULTS["xkiro_url"])}
    try:
        result["ollama"] = {
            "configured": requests.get(cfg["ollama_url"].rstrip("/")+"/api/tags",
                                       timeout=3).ok,
            "model": cfg["ollama_model"],
            "url": cfg["ollama_url"],
        }
    except Exception as e:
        result["ollama"] = {"configured": False, "model": cfg["ollama_model"],
                            "error": str(e)}
    try:
        result["compatible"] = {
            "configured": requests.get(cfg["compatible_url"].rstrip("/")+"/v1/models",
                                       timeout=3).ok,
            "model": cfg["compatible_model"],
            "url": cfg["compatible_url"],
        }
    except Exception as e:
        result["compatible"] = {"configured": False, "model": cfg["compatible_model"],
                                "error": str(e)}
    return result


def list_ollama_models() -> list[str]:
    cfg = get_config()
    r = requests.get(cfg["ollama_url"].rstrip("/") + "/api/tags", timeout=5)
    r.raise_for_status()
    return [m.get("name") for m in r.json().get("models", []) if m.get("name")]


def list_xkiro_models() -> list[str]:
    """Il catalogo aggiornato del gateway.

    Elencarlo evita il problema pratico di xKiro: gli identificativi cambiano
    nel tempo e un nome sbagliato non dà un errore parlante, ma un 404.
    """
    key = _key("XKIRO_API_KEY")
    if not key:
        return []
    cfg = get_config()
    r = requests.get((cfg.get("xkiro_url") or DEFAULTS["xkiro_url"]).rstrip("/") + "/models",
                     headers={"Authorization": f"Bearer {key}"}, timeout=10)
    r.raise_for_status()
    return sorted(x.get("id") for x in r.json().get("data", []) if x.get("id"))


def list_openai_models() -> list[str]:
    key = _key("OPENAI_API_KEY")
    if not key:
        return []
    r = requests.get("https://api.openai.com/v1/models",
                     headers={"Authorization": f"Bearer {key}"}, timeout=10)
    r.raise_for_status()
    return sorted([x.get("id") for x in r.json().get("data", []) if x.get("id")],
                  key=lambda x: (not x.startswith("gpt"), x))


def _order(selected: str) -> list[str]:
    if selected != "auto":
        return [selected] + [p for p in ("openai","grok","google","xkiro","ollama",
                                        "anthropic","compatible")
                             if p != selected]
    # OpenAI is the reliable, low-latency path when a secure local key exists.
    # Missing keys fail immediately, so this does not slow users who only use a
    # different provider.  Google remains available for the live voice engine.
    # xKiro sta dopo i fornitori diretti e prima di quelli locali: raggiunge gli
    # stessi modelli passando per un intermediario, quindi ha senso come rete di
    # sicurezza quando una chiave diretta manca o il fornitore è in difficoltà.
    return ["openai", "grok", "google", "xkiro", "ollama", "anthropic", "compatible"]


def _is_complex(prompt: str, history) -> bool:
    """Choose extra reasoning only when it adds value to the answer."""
    return len(prompt) > 500 or len(_compact_history(history)) >= 8 or bool(
        _COMPLEX_QUERY.search(prompt))


def _openai_profile(prompt: str, history, cfg: dict) -> tuple[str, str, int]:
    """Return model, reasoning effort and timeout for the current request."""
    profile = str(cfg.get("response_profile") or "adaptive").lower()
    complex_turn = profile == "smart" or (
        profile == "adaptive" and _is_complex(prompt, history))
    if complex_turn:
        return (
            str(cfg.get("openai_smart_model") or cfg["openai_model"]),
            "medium",
            int(cfg["timeout"]),
        )
    return (
        str(cfg.get("openai_fast_model") or cfg["openai_model"]),
        "none",
        int(cfg.get("fast_timeout") or cfg["timeout"]),
    )


def generate(prompt: str, *, system: str | None = None,
             provider: str | None = None, history=None) -> str:
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("Il prompt non può essere vuoto")
    cfg = get_config()
    selected = (provider or cfg["provider"]).strip().lower()
    if selected not in PROVIDERS:
        selected = "auto"
    compact_history = _compact_history(history)
    cache_key = _cache_key(prompt, system, compact_history, selected)
    can_cache = not _DYNAMIC_QUERY.search(prompt)
    if can_cache:
        cached = _cached_response(cache_key)
        if cached is not None:
            return cached

    errors = []
    for p in _order(selected):
        try:
            if p == "google":
                answer = _google(prompt, system, compact_history, cfg)
            elif p == "openai":
                model, effort, timeout = _openai_profile(prompt, compact_history, cfg)
                answer = _openai(prompt, system, compact_history, cfg, model=model,
                                 timeout=timeout, reasoning_effort=effort)
            elif p == "grok":
                answer = _grok(prompt, system, compact_history, cfg)
            elif p == "anthropic":
                answer = _anthropic(prompt, system, compact_history, cfg)
            elif p == "xkiro":
                answer = _xkiro(prompt, system, compact_history, cfg)
            elif p == "ollama":
                answer = _ollama(prompt, system, compact_history, cfg)
            elif p == "compatible":
                answer = _compatible(prompt, system, compact_history, cfg)
            else:
                continue
            if can_cache:
                _remember_response(cache_key, answer)
            return answer
        except Exception as e:
            errors.append(f"{p}: {type(e).__name__}: {str(e)[:180]}")
    raise RuntimeError("Nessun motore AI disponibile. " + " | ".join(errors))


def stream_generate(prompt: str, **kwargs):
    # Stable non-streaming fallback: callers can adopt this API without changing
    # their integration later. It yields once, so no fake streaming is claimed.
    yield generate(prompt, **kwargs)


def provider_status_line() -> str:
    cfg = get_config()
    p = cfg["provider"].upper()
    return (f"FARFIX AI: {p} | Grok={cfg.get('grok_model','grok-4')} | "
            f"xKiro={cfg.get('xkiro_model', DEFAULTS['xkiro_model'])} | "
            f"OpenAI={cfg['openai_model']} | Ollama={cfg['ollama_model']} | "
            f"Claude={cfg['anthropic_model']} | Gemini={cfg['google_model']}")
