# Come pubblicare SuperFarfix su GitHub

Guida passo passo. Puoi cancellare questo file dopo la pubblicazione.

---

## Prima di tutto: due cose da sapere

**1. La tua chiave OpenAI era in `.env.local`.** In questa cartella quel file
non c'è più, quindi non rischi di pubblicarla. Però quella chiave è passata
attraverso file condivisi: se vuoi stare tranquillo, revocala e generane una
nuova su <https://platform.openai.com/api-keys>. Ci vogliono trenta secondi.

**2. Il vecchio `.gitignore` non funzionava.** Aveva i commenti a fine riga, e
git non li riconosce: la riga

```
config/api_keys.json          # your Gemini API key
```

non ignorava `config/api_keys.json`, cercava un file chiamato letteralmente
`config/api_keys.json          # your Gemini API key`. Risultato: la chiave
privata TLS in `config/certs/` e il file delle configurazioni sarebbero
finiti online al primo push. Ora è riscritto e ogni regola è stata verificata.

---

## Passo 1 — Crea il repository su GitHub

1. Vai su <https://github.com/new>
2. Nome: `SuperFarfix`
3. Descrizione: `Assistente vocale da scrivania con volto olografico animato`
4. Pubblico o privato, come preferisci
5. **Non** spuntare "Add a README", "Add .gitignore" né "Choose a license" —
   ci sono già tutti in questa cartella
6. Clicca "Create repository"

## Passo 2 — Carica il progetto

Apri il terminale dentro questa cartella:

```bash
git init
git add -A
```

**Fermati e controlla** che non stia caricando niente di riservato:

```bash
git status --porcelain | grep -E "api_keys.json|\.env|certs|long_term"
```

Deve uscire soltanto:

```
A  .env.example
A  config/api_keys.example.json
```

Se compare qualsiasi altra cosa, fermati e chiedi aiuto prima di continuare.

Se il controllo è pulito:

```bash
git commit -m "SuperFarfix: primo rilascio pubblico"
git branch -M main
git remote add origin https://github.com/FatihMakes/SuperFarfix.git
git push -u origin main
```

## Passo 3 — Sistema la pagina del repository

Sulla pagina GitHub del progetto:

- **⚙️ accanto a "About"**: aggiungi la descrizione e alcuni argomenti
  (`voice-assistant`, `python`, `pyqt6`, `gemini`, `farfix`, `avatar`)
- **Settings → Features**: attiva "Issues" e "Discussions" se vuoi ricevere
  segnalazioni
- **Security → Secret scanning**: attivalo. Se un giorno committi una chiave
  per sbaglio, GitHub te lo dice subito

## Passo 4 — Controlla che la CI sia verde

Nella scheda **Actions** partirà automaticamente il controllo: verifica che
tutto compili, che i test passino e che non ci siano credenziali nel codice,
su Python 3.11, 3.12 e 3.13. Se diventa rosso, l'errore è scritto lì dentro.

---

## Cosa contiene il repository

| File | A cosa serve |
|---|---|
| `README.md` | la pagina principale che vedono i visitatori |
| `CONTRIBUTING.md` | come collaborare |
| `SECURITY.md` | cosa non pubblicare e come segnalare vulnerabilità |
| `CHANGELOG.md` | storico delle modifiche |
| `LICENSE` | CC BY-NC 4.0 |
| `.gitignore` | cosa git deve ignorare (riscritto e verificato) |
| `.gitattributes` | normalizza i fine riga tra Windows e Linux |
| `.env.example` | modello per le chiavi API |
| `config/api_keys.example.json` | modello di configurazione |
| `.github/workflows/ci.yml` | controlli automatici a ogni push |
| `.github/ISSUE_TEMPLATE/` | moduli per segnalazioni e proposte |
| `docs/README-en-legacy.md` | la vecchia documentazione estesa in inglese |

---

## Aggiornamenti successivi

```bash
git add -A
git status --porcelain          # controlla sempre prima di committare
git commit -m "descrizione della modifica"
git push
```

---

## Una nota sulla licenza

Stai usando CC BY-NC 4.0. Va benissimo per vietare l'uso commerciale, ma è
nata per opere creative, non per il software: non dice nulla su brevetti,
garanzie o responsabilità, cose che le licenze software trattano esplicitamente.
Se la scelta è voluta, va bene così. Se invece ti interessa soprattutto che il
progetto resti aperto, le alternative più usate per il software sono MIT
(permissiva) o GPL-3.0 (chi modifica deve ripubblicare le modifiche); nessuna
delle due però vieta l'uso commerciale.
