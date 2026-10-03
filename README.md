# ⚙️ SuperFarfix

**Assistente vocale da scrivania con volto olografico animato.** Gli parli e lui ti risponde a voce. Al centro dello schermo un volto tridimensionale muove la bocca sulle sue parole, e non è una mascella che si apre col volume: sono le vere forme della bocca. Le labbra si chiudono su *m*, *b* e *p*, si allargano sulla *i* e si arrotondano sulla *u*. Le sopracciglia seguono la frase, lo sguardo si sposta e le palpebre sbattono.

Gira interamente sul tuo computer: nessun server, nessuna telemetria, nessun account. Le sole connessioni in uscita sono quelle verso i modelli AI che scegli tu e verso le fonti di dati pubbliche che usi.

![Anteprima del volto](docs/anteprima_volto.png)

![CI](https://github.com/farfix75/SuperFarfix/actions/workflows/ci.yml/badge.svg)

---

## Indice

- [Novità di questa versione](#-novità-di-questa-versione)
- [Cosa sa fare](#cosa-sa-fare)
- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Modalità di utilizzo](#modalità-di-utilizzo)
- [Struttura del progetto](#struttura-del-progetto)
- [Privacy e dati](#privacy-e-dati)
- [Problemi frequenti](#problemi-frequenti)
- [Sviluppo e test](#sviluppo-e-test)
- [Documentazione](#documentazione)
- [Licenza](#licenza)

---

## 🆕 Novità di questa versione

Il dettaglio completo è nel [CHANGELOG](CHANGELOG.md). In sintesi:

### Nuove funzionalità

- **Dati dal mondo reale, senza chiavi API.** Aerei in volo (OpenSky), terremoti (USGS), posizione della ISS e prossimi lanci (wheretheiss.at, Launch Library 2) e meteo reale (Open-Meteo).
- **Vista radar nel pannello.** Per aerei e terremoti compare una mappa disegnata e centrata su di te, con cerchi di distanza, punti cardinali e bersagli orientati secondo la rotta reale.
- **Interruzione vocale.** Se gli parli sopra si ferma in circa 0,25 secondi e non perde l'inizio della tua frase.
- **LLM locale di supporto** (Ollama). Riconosce in pochi istanti cosa stai chiedendo e risponde lui se Gemini si sta riconnettendo.
- **Occhi umani** (iride con fibre, pupilla che si dilata, riflessi) e **ombreggiatura liscia** del volto (Gouraud).
- **Registro dei dialoghi**, cioè uno storico strutturato di tempi di risposta, azioni ed esiti.
- **Provider Grok** e **gateway xKiro** nel router multi-AI.

### Stabilità: "dopo un po' non mi sente più" è risolto

- **Rinnovo trasparente della connessione.** Gemini chiude la connessione circa ogni 10 minuti. Ora l'avviso viene raccolto e il passaggio avviene nel primo silenzio, senza schermata SLEEPING.
- **Rete caduta rilevata in pochi secondi**, con riconnessione automatica e nessuna perdita della conversazione.
- **Gli strumenti lunghi non fanno più cadere la sessione**: girano in parallelo con un timeout di 90 secondi.
- **Microfono e casse sorvegliati.** Se uno stream audio muore (cuffie USB, Bluetooth, servizio audio di Windows) viene riaperto da solo.
- **Il ciclo vocale riparte da solo** dopo un errore imprevisto, invece di lasciare una finestra aperta che non risponde.
- **Dashboard del telefono non bloccante.** Un telefono in standby non congela più l'assistente.
- **Errori più chiari.** "NET: Connection failed" ora mostra il motivo reale, e se hai raggiunto il limite di utilizzo lo dice e aspetta.

### Correzioni

- **Modelli Gemini aggiornati.** I modelli `gemini-2.5-flash*` dismessi sono stati sostituiti dalla famiglia 3.x. I nomi ritirati salvati in configurazione si aggiornano da soli e un modello che risponde 404 viene saltato.
- **Fine dell'errore `UnicodeDecodeError ('charmap')`** su Windows in italiano. Era causato dall'output di netsh, powershell e schtasks letto con la code page sbagliata.
- **Firewall della dashboard.** Non chiede più i permessi di amministratore a ogni avvio e non duplica le regole.
- **Rimossi gli avvisi** di AFC e di `pynvml` deprecato.

### Rinominato

- L'assistente si chiama ora **FARFIX** ovunque. Le installazioni esistenti vengono migrate da sole al primo avvio (`core/legacy_names.py`): avvio automatico, certificati, profili del browser, attività pianificate e regole del firewall.

---

## Cosa sa fare

Ogni riga corrisponde a codice presente nel progetto; il file indicato è dove guardare.

### Volto e voce

| | Descrizione |
|---|---|
| 🧑 **Volto olografico** | Testa umana animata nell'HUD, basata su geometria facciale reale (468 vertici). È illuminata e disegnata via software, senza GPU né pacchetti aggiuntivi (`core/avatar.py`, `core/avatar_mesh.py`) |
| 👄 **Sincronizzazione labiale vera** | Le forme della bocca nascono dalla fusione tra i formanti dell'audio e la trascrizione: chiusure su *m·b·p*, apertura sulla *a*, arrotondamento sulla *u* (`core/viseme.py`) |
| 🌍 **Bocca indipendente dalla lingua** | Latino, cirillico e greco usano le stesse regole. Le scritture che non rivelano la pronuncia ripiegano in modo pulito |
| 👁️ **Occhi umani** | Bulbo sfumato, iride con anello esterno e fibre, pupilla che si dilata con la voce, riflessi. Le palpebre coprono davvero l'iride |
| 🙂 **Recitazione facciale** | Sopracciglia che seguono la frase, sguardo che salta tra punti di fissazione, battito di ciglia naturale, piccolo cenno sulle sillabe accentate |
| 😐 **Il volto come stato** | Distoglie lo sguardo mentre pensa, ti guarda mentre ascolta, abbassa le palpebre quando si addormenta |
| ⚛️ **Stile alternativo** | `hud_style: core` sostituisce il volto con il nucleo del reattore, molto più leggero |
| 🎙️ **Voce in tempo reale** | Conversazione a bassa latenza in qualsiasi lingua tramite Gemini Live (`gemini-3.8-live`) |
| 🗣️ **Scelta della voce** | Cinque voci native (Charon, Puck, Kore, Fenrir, Aoede), cambiabili dall'interfaccia senza riavviare |

### Conversazione

| | Descrizione |
|---|---|
| ✋ **Interruzione vocale** | Parlagli sopra e si ferma in circa 0,25 s. L'inizio della tua frase viene conservato e inviato al modello (`core/echo.py`) |
| 🔇 **Guardia anti-eco** | Non risponde mai alla propria voce: la coda che rientra dal microfono viene riconosciuta e scartata, senza metterti in muto |
| ⚡ **Turni rapidi** | Bastano 420 ms di silenzio per chiudere il tuo turno, e l'audio viaggia in blocchi da 100 ms |
| 🎚️ **Push-to-talk** | Tieni premuto Ctrl+Spazio per aprire il microfono. Funziona in modo globale su Windows (`core/hotkey.py`) |
| 🎙️ **Parola di attivazione** | Rilevamento locale e offline. Dorme finché non viene chiamato e mentre dorme non trasmette audio (`core/wake_word.py`) |
| ♾️ **Sessioni illimitate** | Compressione del contesto a finestra scorrevole: una conversazione può durare ore |
| 🔗 **Continuità di sessione** | Una connessione caduta, un rinnovo del server o un cambio di voce o dispositivo non cancellano la conversazione |
| ⌨️ **Ingresso ibrido** | Tastiera e voce si alternano liberamente |

### Modelli e AI

| | Descrizione |
|---|---|
| 🚀 **Motore Live** | `gemini-3.8-live`, sostituibile con `live_model` in configurazione |
| 🪜 **Scala di ripiego** | Una quota esaurita, un timeout o un 404 fanno passare al modello successivo invece di fallire (`_LADDERS` in `core/gemini.py`) |
| ⏱️ **Chiamate con timeout** | Tutte le chiamate Gemini "di servizio" passano da un unico punto, con timeout e limite di sessioni parallele (`core/gemini.py`) |
| 🔀 **Multi-AI** | Gemini, OpenAI, Anthropic, Grok, xKiro, Ollama e qualunque server compatibile OpenAI, con modalità AUTO (`core/ai_router.py`) |
| 🌐 **Gateway xKiro** | Una sola chiave per i modelli di più fornitori. Gli identificativi sono sempre `vendor/modello` |
| 🏠 **LLM locale** | Ollama (default `llama3.2`) riconosce l'intento in meno di 400 ms e risponde quando Gemini è offline (`core/local_llm.py`) |
| 🎛️ **Selettore AI** | Plugin con modulo grafico per scegliere provider e modello (`plugins/farfix_ai_control.py`) |
| 📊 **Registro dei dialoghi** | Ogni scambio è salvato con tempo di risposta, azioni eseguite ed esiti (`core/interaction_log.py`) |

### Memoria

| | Descrizione |
|---|---|
| 🧠 **Memoria richiamabile** | Nessun limite di dimensione e nulla dimenticato in silenzio. Nel prompt entra ciò che ci sta, il resto viene cercato localmente su richiesta |
| 👁️ **Pannello memoria** | Vedi ogni fatto memorizzato su di te e quando è stato appreso, e lo cancelli con un clic |
| 🗓️ **Memoria di sessione** | Riassume ogni conversazione (al massimo ogni 30 minuti) e la richiama il giorno dopo |
| 🧑‍💻 **Memoria della lingua** | Rileva la lingua che parli al primo uso e vi si adatta |

### Azioni sul computer

Sono 20 azioni, scoperte automaticamente all'avvio (`core/action_loader.py`).

| | Azione | Descrizione |
|---|---|---|
| 🚀 | `open_app` | Avvia programmi per nome |
| 🖥️ | `computer_settings` | Volume, luminosità, WiFi, scorciatoie, spegnimento e riavvio |
| 🖱️ | `computer_control` | Scrive, clicca, usa scorciatoie, scorre, muove il mouse, cattura e trova elementi sullo schermo |
| 🗂️ | `desktop_control` | Sfondo, riordino e pulizia del desktop, statistiche |
| 📁 | `file_controller` | Elenca, crea, sposta, copia, rinomina, legge, scrive e cerca file; mostra lo spazio su disco |
| 📄 | `file_processor` | Immagini (OCR, ridimensiona, converte), PDF, Word, CSV/Excel, JSON, codice, audio e video |
| 💻 | `code_helper` | Scrive, modifica, spiega ed esegue file di codice |
| 🏗️ | `dev_agent` | Costruisce progetti completi su più file: pianifica, scrive, installa le dipendenze, apre VS Code, esegue e corregge |
| 🌐 | `browser_control` | Apre URL, naviga tra le schede, interagisce con le pagine |
| 🔍 | `web_search` | Notizie, ricerca, prezzi e confronti. Prima Gemini con fonti, poi DuckDuckGo come riserva |
| 📺 | `youtube_video` | Cerca e apre video, ne riassume la trascrizione, mostra le tendenze |
| 📨 | `send_message` | WhatsApp, Telegram e altro |
| ⏰ | `reminder` | Notifiche pianificate dal sistema operativo (Utilità di pianificazione, LaunchAgent, systemd) |
| 🎮 | `game_updater` | Steam ed Epic Games: installa, aggiorna, elenca i giochi, controlla i download, pianifica gli aggiornamenti |
| 🛫 | `flight_finder` | Cerca voli su Google Flights e ti legge le opzioni migliori |
| ⛅ | `weather_now` | Meteo reale letto a voce (temperatura, percepita, cielo, vento, raffiche) via Open-Meteo |
| 🌤️ | `weather_report` | Apre il meteo nel browser |
| ✈️ | `live_aircraft` | Gli aerei in volo sopra un luogo adesso: distanza, quota, velocità e rotta dai transponder ADS-B |
| 🌍 | `earthquakes` | Scosse recenti dall'USGS: magnitudo, luogo, profondità, distanza da te e un giudizio a parole |
| 🛰️ | `space_watch` | Dove si trova ora la ISS e i prossimi lanci orbitali |

Le quattro fonti pubbliche (aerei, terremoti, spazio, meteo) non richiedono chiavi e usano la CPU solo nei decimi di secondo in cui servono. Geocodifica, limiti di frequenza e cache sono condivisi in `core/public_data.py`.

### Sicurezza delle azioni

| | Descrizione |
|---|---|
| ↩️ **Annulla** | Riporta indietro file spostati, rinominati, creati o scritti e impostazioni cambiate (`core/undo.py`) |
| ⚠️ **Conferma reale** | Spegnimento, riavvio e WiFi aspettano che tu prema un pulsante: il modello non può confermare da solo (`core/confirm.py`) |
| 🪟 **Niente finestre di console** | Su Windows nessun comando apre finestre nere e l'output con lettere accentate viene letto correttamente (`core/win_subprocess.py`) |

### Visione e monitoraggio

| | Descrizione |
|---|---|
| 👁️ **Consapevolezza visiva** | Schermo e webcam inviati alla sessione su richiesta, etichettati per origine (`actions/screen_processor.py`) |
| 🛰️ **Vista radar** | Proiezione azimutale centrata su di te, disegnata con QPainter. Niente tessere, niente rete, niente GPU (`core/map_view.py`) |
| 🌐 **Globo terrestre** *(modulo pronto, non ancora collegato all'interfaccia)* | La Terra vista dallo spazio con le coste di Natural Earth (24 KB) e proiezione ortografica (`core/globe_view.py`) |
| 📈 **Monitor di sistema** | CPU, RAM e GPU con avvisi vocali, senza lanciare processi esterni (`actions/system_monitor.py`) |
| 📰 **Argomenti seguiti** | Controlla le notizie una volta al giorno sugli argomenti che scegli e ti avvisa delle novità (`actions/background_monitor.py`) |
| 🔔 **Proattività** | Messaggi che tengono conto di ora e contesto, briefing mattutino, senza ripetersi (`actions/proactive.py`) |

### Interfaccia

| | Descrizione |
|---|---|
| 🎨 **Temi dal vivo** | Ricolora l'intero HUD da una ruota dei colori; il volto si adegua |
| 〰️ **HUD reattivo** | La forma d'onda pulsa sull'audio reale: il microfono mentre ascolti, l'assistente mentre parla |
| 🗺️ **Pannello contenuti** | Strato scorrevole sotto l'HUD per risultati web, notizie, mappe radar e dati |
| 📋 **Clipboard intelligente** | Copia del testo e compare un pannello con Traduci, Riassumi, Spiega e Correggi |
| 🎧 **Selettore audio** | Microfono e casse scelti per nome e verificati davvero (`core/audio_devices.py`) |
| 📱 **Dashboard remota** | Controllo dal telefono con abbinamento tramite QR e traffico cifrato AES-256 (`dashboard/server.py`) |
| 🪪 **Personalizzazione** | Nome dell'assistente, il tuo nome, voce e colore, con effetto immediato |
| ⚡ **Avvio automatico** | Si registra all'avvio del sistema (registro di Windows, LaunchAgent, `.desktop`) |

### Estensibilità

| | Descrizione |
|---|---|
| 🧩 **Azioni auto-descritte** | Ogni azione dichiara schema e gestore in un dizionario `TOOL` e viene scoperta all'avvio |
| 🔌 **Plugin** | Copia `plugins/_template.py`, rinominalo e al riavvio l'assistente ha una capacità in più. I plugin si attivano e disattivano senza riavvio |
| 🪪 **Auto-conoscenza** | Nome, sistema operativo, capacità e limiti vengono rigenerati a ogni sessione |

---

## Requisiti

| | |
|---|---|
| **Python** | 3.11, 3.12 o 3.13 |
| **Sistema** | Windows, macOS o Linux |
| **Chiave API** | Google Gemini (per la voce). Gli altri provider sono opzionali |
| **Hardware** | Microfono e casse. Con le cuffie l'interruzione vocale funziona meglio |
| **Opzionale** | [Ollama](https://ollama.com) per l'LLM locale e la risposta offline |

---

## Installazione

```bash
git clone https://github.com/farfix75/SuperFarfix.git
cd SuperFarfix
python setup.py
```

`setup.py` apre una finestra di configurazione, installa le dipendenze e ti chiede le chiavi API.

Se preferisci fare a mano:

```bash
pip install -r requirements.txt
cp .env.example .env.local      # poi inserisci le tue chiavi
python main.py
```

Le funzioni pesanti sono opzionali e si installano a parte:

```bash
pip install openwakeword   # parola di attivazione (o un clic da ⚙ → WAKE WORD)
pip install pandas         # analisi di fogli di calcolo e CSV
pip install pydub          # audio (serve anche ffmpeg)
ollama pull llama3.2       # LLM locale
```

---

## Configurazione

Le chiavi si possono mettere in due posti.

**1. File `.env.local`** (consigliato, ignorato da git)

```bash
cp .env.example .env.local
```

Variabili supportate: `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY` (Grok) e `XKIRO_API_KEY`. Servono solo quelle dei provider che usi.

**2. File `config/api_keys.json`**

```bash
cp config/api_keys.example.json config/api_keys.json
```

Contiene anche le impostazioni non segrete: nome dell'assistente, colore, dispositivi audio e modelli.

> ⚠️ Entrambi i file sono già in `.gitignore`. Non toglierli: committarli significa pubblicare le tue chiavi.

### Impostazioni utili

| Chiave | Default | Cosa fa |
|---|---|---|
| `voice_barge_in` | `true` | Si interrompe quando gli parli sopra. Mettilo a `false` se casse alte in una stanza riverberante lo fanno fermare da solo |
| `turn_tuning.silence_ms` | `420` | Millisecondi di silenzio dopo cui il tuo turno è finito. Alzalo se ti taglia durante le pause |
| `proactive_audio` | `true` | Decide se una frase era rivolta a lui. Disattivandolo risponde prima, ma anche a discorsi non diretti a lui |
| `interaction_log` | `true` | Registra gli scambi in `memory/interactions/`. Vedi [DATI_DIALOGHI.md](DATI_DIALOGHI.md) |
| `hud_style` | `face` | `face` per il volto, `core` per il nucleo del reattore |
| `live_model` | `gemini-3.8-live` | Modello usato per la voce in tempo reale |
| `llm_provider` / `llm_model` / `llm_url` | `ollama` / `llama3.2` / `http://localhost:11434` | LLM locale per l'intento rapido e la risposta offline |

---

## Modalità di utilizzo

| Comando | Cosa avvia |
|---|---|
| `python main.py` | L'assistente completo: HUD, volto, voce e azioni |
| `python personal_cli.py` | Assistente testuale nel terminale, senza interfaccia grafica, con memoria (`/memoria`, `/help`, `/esci`) |
| `python farfix_ai.py status` | Stato dei provider AI, senza mostrare le chiavi |
| `python farfix_ai.py use ollama` | Cambia il motore testuale (`auto`, `google`, `openai`, `anthropic`, `grok`, `xkiro`, `ollama`, `compatible`) |
| `python farfix_ai.py models` | Elenca i modelli disponibili su Ollama e OpenAI |
| `python farfix_ai.py ask "..."` | Domanda singola dal terminale |

---

## Struttura del progetto

```
SuperFarfix/
├── main.py               # avvio, sessione vocale, ciclo audio
├── ui.py                 # interfaccia HUD (PyQt6)
├── setup.py              # installatore grafico
├── personal_cli.py       # assistente da terminale
├── farfix_ai.py          # utilità da riga di comando per i provider AI
├── core/
│   ├── avatar.py         # rendering e animazione del volto
│   ├── avatar_mesh.py    # mesh anatomica della testa
│   ├── viseme.py         # forme della bocca dal parlato
│   ├── echo.py           # separa la tua voce dall'eco dell'assistente
│   ├── gemini.py         # chiamate Gemini con timeout e scala di ripiego
│   ├── ai_router.py      # motore testuale multi-provider
│   ├── local_llm.py      # LLM locale: intento rapido e risposta offline
│   ├── public_data.py    # basi comuni per le fonti di dati pubbliche
│   ├── map_view.py       # vista radar
│   ├── globe_view.py     # globo terrestre disegnato
│   ├── undo.py           # annulla condiviso
│   ├── confirm.py        # conferme che il modello non può falsificare
│   ├── interaction_log.py
│   ├── legacy_names.py   # migrazione dal vecchio nome
│   ├── win_subprocess.py # correzioni per i processi su Windows
│   └── ...
├── actions/              # le 20 azioni (file, web, sistema, dati pubblici...)
├── plugins/              # estensioni caricate a runtime
├── dashboard/            # controllo remoto dal telefono
├── memory/               # configurazione e memoria a lungo termine
├── data/                 # coste del mondo (Natural Earth)
├── tests/                # test offline
└── docs/                 # documentazione estesa
```

---

## Privacy e dati

- **Tutto resta in locale.** Memoria, registro dei dialoghi e configurazione stanno nella cartella del progetto.
- **Il registro dei dialoghi contiene quello che dici al computer.** Si disattiva con `"interaction_log": false` e si cancella eliminando `memory/interactions/`.
- **Non pubblicare mai** questi file e cartelle (sono già in `.gitignore`):

  | Percorso | Contiene |
  |---|---|
  | `config/api_keys.json`, `.env.local` | Le tue chiavi API |
  | `config/certs/` | La chiave privata TLS della dashboard |
  | `config/whatsapp_web/` | La sessione WhatsApp collegata |
  | `memory/long_term.json` | I fatti che l'assistente ha imparato su di te |
  | `memory/interactions/` | Lo storico delle conversazioni |

> ⚠️ **Attenzione al caricamento dal sito di GitHub.** Con "Add file → Upload files", o trascinando uno zip, il `.gitignore` **non viene applicato** e i file qui sopra finiscono online. Pubblica sempre con `git push` da una cartella pulita. Vedi [PUBBLICARE_SU_GITHUB.md](PUBBLICARE_SU_GITHUB.md).

---

## Problemi frequenti

**Si interrompe da solo mentre parla.** Le casse rimandano la sua voce nel microfono. Usa le cuffie, abbassa il volume oppure imposta `"voice_barge_in": false`.

**Mi taglia mentre sto ancora parlando.** Alza `turn_tuning.silence_ms` a 600–800.

**Risponde in ritardo di un turno.** Prova `"proactive_audio": false`.

**Il volto va a scatti.** Dalle impostazioni passa allo stile `core`, molto più leggero.

**Non sente il microfono.** Controlla il dispositivo di ingresso nelle impostazioni. Se `input_device` è vuoto usa il dispositivo predefinito di sistema, che non sempre è quello giusto.

**"NET: Connection failed".** Il messaggio ora indica il motivo. Se è il limite di utilizzo di Gemini, l'assistente aspetta 60 secondi e riprova da solo.

**Gemini non risponde.** Con Ollama attivo l'assistente risponde in locale finché la connessione non torna.

Per altri casi vedi [FARFIX_DIAGNOSTICA.md](FARFIX_DIAGNOSTICA.md).

---

## Sviluppo e test

Prima di aprire una pull request:

```bash
python -m compileall -q .
python -m unittest discover -s tests
```

La CI di GitHub ripete questi controlli su Python 3.11, 3.12 e 3.13 e blocca qualsiasi commit che contenga una chiave API o una chiave privata.

Per aggiungere una capacità basta un file: un'azione in `actions/` con un dizionario `TOOL`, oppure un plugin in `plugins/` partendo da `_template.py`. Le linee guida sono in [CONTRIBUTING.md](CONTRIBUTING.md); per segnalare un problema di sicurezza vedi [SECURITY.md](SECURITY.md).

---

## Documentazione

- [Come avviare](COME_AVVIARE.md): il primo avvio passo passo
- [Configurazione multi-AI](FARFIX_MULTI_AI_SETUP.md): collegare OpenAI, Anthropic, Grok, xKiro e Ollama
- [Motore AI](FARFIX_AI_README.md): come funziona il router dei provider
- [Registro dei dialoghi](DATI_DIALOGHI.md): cosa viene salvato, come leggerlo e come cancellarlo
- [Diagnostica](FARFIX_DIAGNOSTICA.md): quando qualcosa non parte
- [Pubblicare su GitHub](PUBBLICARE_SU_GITHUB.md): come caricare il progetto senza esporre dati
- [Changelog](CHANGELOG.md): tutte le modifiche
- [Componenti di terze parti](THIRD_PARTY.md)
- [Documentazione estesa (EN)](docs/README-en-legacy.md)

---

## Licenza

[CC BY-NC 4.0](LICENSE). Puoi usarlo e modificarlo liberamente citando l'autore; l'uso commerciale non è permesso.

Copyright © 2026 FatihMakes
