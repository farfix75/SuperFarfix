# Changelog

Tutte le modifiche rilevanti del progetto.

## [Non rilasciato]

### Corretto — modelli Gemini testuali non più disponibili
- `gemini-2.5-flash` e `gemini-2.5-flash-lite` rispondono ora 404 ("no longer
  available to new users") e verranno spenti a ottobre 2026. Le catene di
  modelli usano ora `gemini-3.5-flash-lite`, `gemini-3.8-flash`,
  `gemini-3.6-flash` e `gemini-3.1-flash-lite`, come raccomandato da Google.
- Un modello che risponde 404 viene saltato per il resto della sessione invece
  di essere ritentato a ogni richiesta.
- Il modello di default del router (`gemini-3.1-flash`, inesistente) è ora
  `gemini-3.8-flash`; i nomi di modelli ritirati salvati nella configurazione
  vengono aggiornati da soli all'avvio, e se un nome non esiste più il router
  ripiega sul default invece di fallire.
- Rimosso l'avviso "Direct use of automatic function calling (AFC)": la
  chiamata automatica di funzioni, mai usata qui, è disattivata esplicitamente.
- Le chiamate del router a Gemini hanno ora un timeout.

### Corretto — UnicodeDecodeError ('charmap' codec can't decode byte 0x8d)
- I programmi di console di Windows (netsh, powershell, schtasks…) scrivono
  nella code page OEM (cp850 in italiano), mentre Python leggeva il loro output
  con quella ANSI (cp1252): ogni lettera accentata faceva fallire la lettura.
  Ora `core/win_subprocess.py` decodifica in OEM e sostituisce i byte strani
  invece di andare in errore, per tutto il programma.
- Conseguenza nascosta dello stesso bug: la dashboard credeva che le regole del
  firewall mancassero, chiedeva i permessi di amministratore a ogni avvio e
  aggiungeva ogni volta una regola duplicata. Il controllo ora usa il codice di
  uscita (il testo di netsh è tradotto) e le regole vengono sostituite, non
  duplicate.
- Rimosso l'avviso "The pynvml package is deprecated": il pacchetto consigliato
  è ora `nvidia-ml-py`.

### Corretto — "dopo poco si scollega e non risponde più"
- **Rete morta rilevata in secondi**: keepalive del websocket a 10 s + 15 s
  (prima 20 + 20), così una rete caduta in silenzio viene notata subito e la
  conversazione riprende su una nuova connessione.
- **Connessione che non si apre**: la fase di setup ha ora un limite di 20 s;
  prima poteva restare appesa per sempre senza mai ritentare.
- **Sessioni secondarie che rubavano posto**: le chiamate "di servizio" a
  Gemini (riassunti, strumenti) aprono sessioni Live sulla stessa chiave. Il
  limite si "perdeva" (lo slot veniva liberato prima che la sessione fosse
  chiusa) e usavano il modello sbagliato; ora sono al massimo 2 e rilasciano il
  posto solo a sessione chiusa, così la riconnessione principale non viene
  rifiutata.
- **Riassunto di sessione** non più a ogni riconnessione di routine (massimo
  ogni 30 minuti), perché apriva una sessione proprio mentre quella principale
  si riconnetteva.
- **Dashboard del telefono**: un telefono in standby con la pagina aperta poteva
  bloccare l'assistente appena connesso. Ora gli invii sono paralleli, con
  scadenza, e non vengono mai attesi dal ciclo vocale. Avvio della dashboard e
  generazione del certificato spostati fuori dal ciclo vocale.
- **DNS in coda**: la risoluzione del nome del server usava lo stesso piccolo
  pool di thread dei controlli in background; ora il pool è ampio e l'apertura
  dei dispositivi audio ha thread dedicati.
- **Motore vocale mai più morto in silenzio**: se un errore imprevisto usciva
  dal ciclo principale, la finestra restava aperta ma l'assistente non
  rispondeva più. Ora il ciclo riparte da solo e l'errore viene mostrato.
- I messaggi "NET: Connection failed" mostrano il motivo reale; se è il limite
  di utilizzo Gemini, lo dice e aspetta 60 s invece di martellare.

### Rinominato
- Il nome dell'assistente è ora **FARFIX** ovunque: interfaccia, voce, log,
  notifiche, dashboard, prompt e documentazione. Le installazioni esistenti
  vengono migrate da sole al primo avvio (`core/legacy_names.py`): nome salvato
  in configurazione, avvio automatico, certificati della dashboard, profili del
  browser, attività pianificate e regole firewall.
- La frase di attivazione resta quella del modello pre-addestrato di
  openwakeword (vedi `WAKE_PHRASE` in `core/wake_word.py`).

### Corretto — "dopo qualche minuto non mi sente più"
- **Rinnovo della connessione senza interruzioni**: il server Gemini chiude la
  connessione circa ogni 10 minuti e avvisa prima con un messaggio GoAway, che
  prima veniva ignorato. Ora il passaggio a una nuova connessione (stessa
  conversazione) avviene nel primo momento di silenzio, senza schermata SLEEPING
  e senza i 3 secondi di attesa.
- **Stato "sta parlando" bloccato**: dopo una riconnessione a metà risposta, o
  con un turn_complete perso, il flag restava vero per sempre e il microfono (che
  viene inviato solo quando l'assistente tace) non si riapriva più. Ora viene
  azzerato a ogni sessione e scade da solo dopo 1,2 s senza audio.
- **Risposta all'interruzione scartata**: interrompendo l'assistente quando il
  server aveva già finito di generare, la risposta a ciò che avevi appena detto
  veniva buttata. Ora si scarta solo se c'è davvero altro audio in arrivo.
- **Strumenti fuori dal ciclo di ricezione**: mentre uno strumento lavorava
  nessuno leggeva il socket; dopo ~20 s scadevano i ping e il server chiudeva la
  connessione. Ora gli strumenti girano in parallelo, in un pool dedicato, con un
  timeout di 90 s; le chiamate annullate dal server non ricevono risposta.
- **Microfono e casse sorvegliati**: se lo stream audio muore in silenzio
  (cuffie USB in risparmio energetico, servizio audio di Windows, Bluetooth)
  viene riaperto automaticamente.
- **Errore 1007 ≠ chiave non valida**: un 1007 a metà sessione mandava l'app
  sulla schermata di inserimento chiave, sorda per sempre. Ora solo un vero
  errore di autenticazione lo fa; il resto riconnette.
- In modalità parola di attivazione non si riaddormenta più a ogni riconnessione.

### Modificato
- Riproduzione audio a callback con buffer circolare: niente più attese sul
  pool di thread, e l'interruzione è silenzio immediato.
- Coda del microfono limitata con scarto dei blocchi più vecchi; MIME corretto
  `audio/pcm;rate=16000`.
- Il registro scrive a blocchi di caratteri invece che uno ogni 6 ms: il thread
  dell'interfaccia resta libero e il volto non scatta durante le risposte lunghe.
- Il modello Live si può cambiare con `"live_model"` in `config/api_keys.json`.
  `thinking_config` non viene più inviato a `gemini-3.8-live`, che lo rifiuta.

### Aggiunto
- **Vista radar nel pannello** per aerei e terremoti: proiezione azimutale
  equidistante centrata su di te, disegnata con QPainter e incorporata come
  data URI. Niente globo 3D, niente tessere, niente rete.
- **Quattro fonti di dati pubblici**, senza chiavi e senza processi in
  sottofondo: aerei in volo (OpenSky), terremoti (USGS), Stazione Spaziale e
  lanci orbitali (wheretheiss.at, Launch Library 2), meteo reale (Open-Meteo).
  Le basi comuni stanno in `core/public_data.py`.
- **Interruzione vocale**: parlando sopra l'assistente, questo si ferma entro
  circa 0,25 s e la parte iniziale della frase viene conservata e inviata al
  modello, così non si perde quello che hai detto. Disattivabile con
  `"voice_barge_in": false`.
- Occhi umani: bulbo sfumato, iride con anello esterno e fibre, pupilla che si
  dilata con la voce, riflessi di luce, ombra della palpebra superiore.

### Modificato
- Il volto usa ora l'ombreggiatura sfumata (Gouraud) al posto del colore piatto
  per triangolo: la superficie risulta liscia invece che a tasselli.
- Rimossa la griglia fissa di linee sul volto; resta la banda di scansione.
- Turni più reattivi: 420 ms di silenzio invece dell'attesa predefinita.
- Audio inviato in blocchi da 100 ms invece di 200: si ferma prima quando viene
  interrotto.
- Il collegamento sul desktop si chiama `SuperFarfix` su tutti i sistemi.

### Corretto
- Dopo un'interruzione la risposta successiva poteva essere scartata in
  silenzio, perché si attendeva un `turn_complete` che per una risposta
  annullata non arriva mai.

## [SuperFarfix] — precedentemente "Mark LIV"
- Volto olografico con sincronizzazione labiale reale
- Sistema di plugin, sessioni illimitate, temi dal vivo, parola di attivazione
