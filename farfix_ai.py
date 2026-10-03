"""
FARFIX AI console utility.
Examples:
  python farfix_ai.py status
  python farfix_ai.py use ollama
  python farfix_ai.py models
  python farfix_ai.py ask "spiegami questo codice"
"""
import sys

try:
    import core.win_subprocess  # noqa: F401  (Windows subprocess fixes)
except Exception:
    pass
from core.ai_router import generate, get_config, health, list_ollama_models, list_openai_models, save_config

def main():
    args=sys.argv[1:]
    if not args or args[0]=="status":
        print("FARFIX AI")
        print("Configurazione:", get_config())
        for k,v in health().items():
            print(f"  {k:12} {'ONLINE' if v.get('configured') else 'OFFLINE'}  {v.get('model','')}")
        return
    if args[0]=="use" and len(args)>1:
        save_config(provider=args[1].lower())
        print("Motore:", get_config()["provider"]); return
    if args[0]=="models":
        try: print("Ollama:", ", ".join(list_ollama_models()) or "(nessuno)")
        except Exception as e: print("Ollama:", e)
        try: print("OpenAI:", ", ".join(list_openai_models()) or "(nessuno)")
        except Exception as e: print("OpenAI:", e)
        return
    if args[0]=="ask":
        prompt=" ".join(args[1:])
        if not prompt: print("Uso: python farfix_ai.py ask \"...\""); return
        print(generate(prompt)); return
    print(__doc__)

if __name__=="__main__":
    main()
