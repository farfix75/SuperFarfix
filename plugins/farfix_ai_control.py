"""
FARFIX AI CONTROL — provider/model selector.

The settings form is rendered automatically by SUPERFARFIX's plugin settings
system. This plugin deliberately keeps credentials as password fields and
stores the provider configuration separately from the core assistant state.
"""
from core.ai_router import health, list_ollama_models, list_openai_models, save_config, get_config

PLUGIN = {
    "name": "farfix_ai_control",
    "description": (
        "Manage the FARFIX multi-AI engine. Use when the user asks to change "
        "AI provider/model, inspect AI availability, or switch between Gemini, "
        "OpenAI, xKiro and Ollama."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("status, use_ollama, use_openai, use_gemini, "
                                "use_xkiro, or use_auto")
            }
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "farfix_ai",
    "title": "FARFIX AI ENGINE — MULTI MODEL",
    "fields": [
        {"key":"provider","label":"Motore attivo","type":"choice",
         "options":["auto","grok","openai","xkiro","ollama","anthropic","google",
                    "compatible"],"default":"auto"},
        {"key":"ollama_model","label":"Modello Ollama","type":"text",
         "placeholder":"es. qwen3, llama3.2, gemma3"},
        {"key":"anthropic_model","label":"Modello Claude","type":"text",
         "default":"claude-sonnet-5"},
        {"key":"ollama_url","label":"URL Ollama","type":"text",
         "placeholder":"http://localhost:11434"},
        {"key":"xkiro_model","label":"Modello xKiro (gateway)","type":"text",
         "placeholder":"sempre vendor/modello — es. openai/gpt-5.6-sol",
         "default":"openai/gpt-5.6-sol"},
        {"key":"xkiro_url","label":"URL xKiro","type":"text",
         "placeholder":"https://api.xkiro.com/v1"},
        {"key":"grok_model","label":"Modello xAI Grok","type":"text",
         "placeholder":"es. grok-4","default":"grok-4"},
        {"key":"openai_model","label":"Modello OpenAI / ChatGPT API","type":"text",
         "placeholder":"es. gpt-4.1"},
        {"key":"openai_fast_model","label":"Modello OpenAI rapido","type":"text",
         "placeholder":"es. gpt-5.6-luna"},
        {"key":"openai_smart_model","label":"Modello OpenAI intelligente","type":"text",
         "placeholder":"es. gpt-5.6-terra"},
        {"key":"response_profile","label":"Profilo risposte","type":"choice",
         "options":["adaptive","fast","smart"],"default":"adaptive"},
        {"key":"google_model","label":"Modello Gemini","type":"text",
         "placeholder":"es. gemini-3.8-flash"},
        {"key":"compatible_model","label":"Modello OpenAI-compatible","type":"text",
         "placeholder":"es. qwen / local-model"},
        {"key":"compatible_url","label":"URL OpenAI-compatible","type":"text",
         "placeholder":"http://localhost:1234"},
        {"key":"timeout","label":"Timeout (secondi)","type":"text","default":90},
        {"key":"fast_timeout","label":"Timeout rapido (secondi)","type":"text","default":18},
    ],
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action","status")).lower()
    mapping = {
        "use_ollama":"ollama", "use_openai":"openai", "use_grok":"grok",
        "use_gemini":"google", "use_auto":"auto", "use_xkiro":"xkiro",
    }
    if action in mapping:
        save_config(provider=mapping[action])
        return f"Motore AI impostato su {mapping[action]}."
    if action == "status":
        h = health()
        lines = []
        for name, data in h.items():
            lines.append(f"{name}: {'ONLINE/CONFIGURATO' if data.get('configured') else 'non disponibile'}")
        return "Stato motori AI: " + "; ".join(lines)
    return "Azione AI non riconosciuta."

def test_connection():
    h = health()
    ok = [k for k,v in h.items() if v.get("configured")]
    return "ONLINE: " + ", ".join(ok) if ok else "Nessun provider disponibile."
