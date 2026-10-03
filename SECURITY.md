# Sicurezza

## Segnalare una vulnerabilità

Per problemi di sicurezza **non aprire una issue pubblica**. Usa la funzione
"Report a vulnerability" nella scheda Security di GitHub, oppure contatta
direttamente l'autore del repository.

## Cosa non deve mai finire nel repository

Questi file sono già in `.gitignore`. Se li togli da lì, pubblichi le tue
credenziali al primo push:

| File | Cosa contiene |
|---|---|
| `.env.local`, `.env.*` | chiavi API |
| `config/api_keys.json` | chiavi API e configurazione personale |
| `config/certs/` | chiave privata TLS della dashboard |
| `config/whatsapp_web/` | sessione WhatsApp collegata — dà accesso al tuo account |
| `memory/long_term.json` | quello che l'assistente ha memorizzato su di te |
| `**/token*.json`, `**/client_secret*.json` | token OAuth Google |

Prima di ogni push, un controllo veloce:

```bash
git status --porcelain
git diff --cached --name-only
```

Se vedi uno dei file qui sopra, fermati.

## Se hai pubblicato una chiave per sbaglio

1. **Revocala subito** dal pannello del provider (Google AI Studio, OpenAI, ecc.).
   Questo è il passo che conta davvero.
2. Generane una nuova.
3. Rimuovere il commit non basta: chi ha già clonato il repository ha ancora la
   chiave, e GitHub conserva i commit raggiungibili tramite le fork. La revoca
   è l'unica soluzione reale.

## Cosa fa il programma sul tuo computer

SuperFarfix può aprire programmi, leggere e scrivere file, controllare il
browser e leggere lo schermo. È il suo scopo, ma significa anche che le azioni
che gli chiedi vengono eseguite davvero. Le operazioni distruttive passano da
una richiesta di conferma (`core/confirm.py`); non disattivarla se non sai
esattamente cosa stai facendo.

L'audio e il video vengono inviati al provider AI che hai configurato, secondo
le condizioni di quel provider. Nient'altro lascia il tuo computer: non esiste
un server del progetto.
