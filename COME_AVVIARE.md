# FARFIX Multi-AI — come farlo funzionare

Questo progetto è un assistente personale desktop (HUD PyQt6 + voce Gemini Live)
con un motore testuale multi-modello (Grok / OpenAI / Gemini / Claude / Ollama).

## Requisiti

- Python 3.11 o 3.12 (3.13 può funzionare, ma alcune dipendenze audio/GUI sono più fragili)
- Windows / macOS / Linux con microfono e altoparlanti per la versione vocale
- Almeno **una** API key valida **oppure** Ollama in locale

## 1. Ambiente

```bash
cd FARFIX_MULTI_AI_PRO
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt
```

Per solo chat testuale (senza HUD/voce):

```bash
pip install requests openai google-genai anthropic
```

## 2. Chiavi API

Per OpenAI puoi usare preferibilmente `.env.local` nella cartella del progetto:

```text
OPENAI_API_KEY=la_tua_chiave
```

Il router dà precedenza alla variabile d'ambiente di sistema, poi a `.env.local`, quindi alle chiavi legacy in `config/api_keys.json`. Le chiavi già presenti non vengono sostituite.

Apri `config/api_keys.json` e inserisci le chiavi vere:

- `gemini_api_key` — necessaria per **voce Live** e HUD
- `openai_api_key` — ChatGPT
- `grok_api_key` — deve essere una chiave **xAI** (`xai-...`), non Groq (`gsk_...`)
- `anthropic_api_key` — Claude
- Ollama: nessuna chiave, avvia `ollama serve` e scarica un modello (`ollama pull llama3.2`)

Oppure esegui:

```bash
python setup.py
```

**Sicurezza:** le chiavi nel file originale erano in chiaro. Non condividere lo zip
né committare `config/api_keys.json`. Se hai già condiviso l’archivio, **rigenera
subito** le chiavi su OpenAI / Google / xAI.

## 3. Avvio

### Assistente vocale + faccia olografica (app completa)

```bash
python main.py
```

Serve display grafico, PyQt6, audio e una Gemini API key valida. La voce usa Gemini 3.8 Live.

### Assistente personale in terminale (funziona ovunque)

```bash
python personal_cli.py
```

Comandi: `/memoria`  `/esci`  `ricorda notes.cosa: valore`

### Diagnostica motori

```bash
python farfix_ai.py status
python farfix_ai.py models
python farfix_ai.py use auto
python farfix_ai.py ask "ciao, chi sei?"
```

## Cosa è stato sistemato in questa revisione

- Endpoint Ollama corretto: ora usa `/api/chat` (prima chiamava un path OpenAI inesistente).
- Aggiunto `personal_cli.py`: chat con memoria e fallback automatico tra i provider.
- Router già supportava Grok, OpenAI, Gemini, Claude, Ollama e server compatibili.
- OpenAI ora usa un profilo adattivo: modello rapido per richieste semplici e modello più capace per analisi, codice e pianificazione.
- La cronologia viene compressa al contesto recente e le richieste identiche non dinamiche hanno una cache breve in memoria.

## Limiti reali

- La **voce in tempo reale** resta su Gemini Live: Ollama/OpenAI non sostituiscono quella pipeline.
- Senza display (SSH, container headless) `main.py` / `setup.py` GUI non partono: usa la CLI.
- Le chiavi nel file di esempio del progetto originale sembrano placeholder o di tipo sbagliato
  (es. `gsk_` è Groq, non xAI). Controllale.


## Diagnosi rapida se FARFIX non risponde

1. Avvia `python farfix_ai.py status` per vedere quali motori sono configurati.
2. Avvia `python farfix_ai.py ask "ciao"` per testare il router testuale senza microfono.
3. Nell'app, in Impostazioni > Wake Word, lascia Wake Word disattivato se vuoi che il microfono sia sempre pronto.
4. Il modello vocale Live predefinito è `gemini-3.8-live`.
