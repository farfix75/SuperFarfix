# FARFIX AI ENGINE — SUPERFARFIX MULTI-MODEL EDITION

Questa versione estende SuperFarfix con un motore AI unificato per **Gemini, OpenAI/ChatGPT API, Ollama** e server OpenAI-compatible.

## Cosa cambia

- **AI Router** in `core/ai_router.py`
- selezione `auto / ollama / openai / google / compatible`
- fallback reale: se il provider selezionato non risponde, l'AUTO può provare gli altri
- profilo OpenAI adattivo: richieste brevi usano il modello rapido, quelle complesse il modello più capace
- cache breve per richieste ripetute e cronologia compatta, per risposte più rapide senza perdere il contesto recente
- rilevamento dei modelli Ollama installati
- rilevamento dei modelli OpenAI disponibili con la chiave configurata
- impostazioni integrate nell'overlay plugin di SuperFarfix
- installer `setup.py` che configura Gemini, OpenAI e Ollama al primo avvio
- utility `farfix_ai.py` per diagnostica e test
- nessuna modifica alla pipeline Gemini Live: voce/avatar/lip-sync restano intatti

## Importante

La **voce Live** continua a usare Gemini Live, perché Ollama e la normale Chat Completions API non sono drop-in replacement per quella pipeline audio realtime. Il selettore MULTI-AI governa invece il motore testuale utilizzabile per pianificazione, coding, analisi e altre attività integrate nel progetto.

### Installazione

```bat
python setup.py
python main.py
```

Durante `setup.py`:
1. inserisci Gemini API key (se la usi);
2. inserisci OpenAI API key (se la usi);
3. scegli il provider predefinito;
4. indica i modelli.

Ollama non richiede una API key. Deve essere installato e avere almeno un modello locale.

### Diagnostica

```bat
python farfix_ai.py status
python farfix_ai.py models
python farfix_ai.py use ollama
python farfix_ai.py use openai
python farfix_ai.py use google
python farfix_ai.py use auto
python farfix_ai.py ask "Scrivi una funzione Python che..."
```

### Sicurezza

Le chiavi esistenti in `config/api_keys.json` restano supportate. Per OpenAI è preferibile usare `OPENAI_API_KEY` in `.env.local`: il router la legge localmente senza esportarla ai processi figli e senza stamparla. Non inserire chiavi nel codice sorgente, non committare questi file e non condividere il progetto con le credenziali già compilate.

## Architettura

```text
                    FARFIX FARFIX
                          |
                    AI ORCHESTRATOR
                          |
                    +-----+------+
                    |            |
                 AUTO         MANUAL
                    |            |
        +-----------+------------+-----------+
        |           |            |           |
      Ollama      OpenAI       Gemini   Compatible
       LOCAL        API          API       LOCAL
        |           |            |           |
        +-----------+------------+-----------+
                          |
                     TOOLS / MEMORY
```

`AUTO` non inventa uno stato: prova realmente il provider e passa al successivo solo dopo un errore concreto.
