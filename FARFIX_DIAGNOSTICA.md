# FARFIX Multi-AI — diagnostica della revisione

## Problemi individuati
- La configurazione inclusa aveva `wake_word_enabled=true`: l'assistente partiva in SLEEPING e ignorava microfono e comandi testuali finché non veniva pronunciata la parola di attivazione.
- Il Live model era `gemini-3.1-flash-live-preview`, ancora disponibile ma ormai legacy. La revisione usa `gemini-3.8-live`, modello Live stabile corrente.
- L'installer pretendeva Grok + OpenAI anche se il progetto dichiara correttamente che basta un solo provider o Ollama.
- Il file di configurazione del pacchetto conteneva chiavi API in chiaro: sono state rimosse dal pacchetto revisionato.

## Test eseguiti
- Compilazione sintattica di tutti i 52 file Python: OK.
- Controllo dei punti di ingresso `main.py`, `farfix_ai.py`, router multi-provider, microfono, playback e wake-word.
- Il router testuale supporta fallback automatico tra Grok, OpenAI, Ollama, Claude, Gemini e endpoint OpenAI-compatible.

## Avvio
1. `python setup.py`
2. Inserisci almeno una chiave valida oppure configura Ollama.
3. `python main.py`
4. Se vuoi il dialogo vocale continuo, lascia Wake Word disattivato. Se la abiliti, FARFIX parte addormentato e richiede la parola di attivazione o il pulsante WAKE NOW.
5. Per verificare solo l'intelligenza testuale: `python farfix_ai.py ask "ciao, chi sei?"`

## Sicurezza
Le chiavi API presenti nel file ricevuto non vengono incluse nel nuovo ZIP. Se quelle chiavi erano reali, devono essere revocate/rigenerate presso i rispettivi provider.
