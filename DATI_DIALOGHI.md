# Registro dei dialoghi

Ogni scambio con l'assistente viene registrato in `memory/interactions/`, un
file JSONL al giorno, una riga per scambio.

## Perché

Tutto quello che potrai fare in seguito — misurare se una modifica ha
migliorato le risposte, capire quali comandi falliscono, un giorno addestrare
un modello sul tuo stile — richiede dialoghi reali. E quei dati si raccolgono
solo mentre le conversazioni avvengono: **una conversazione non registrata è
persa per sempre**. È per questo che questo è il primo passo, anche se da solo
non cambia nulla di visibile.

## Cosa contiene una riga

```json
{
  "ts": "2026-09-26T17:41:34",
  "session": "decd4c94f3e6",
  "turn": 1,
  "model": "gemini-3.8-live",
  "user": "apri chrome",
  "assistant": "Apro Chrome, signore.",
  "reply_ms": 350,
  "tools": [{"name": "open_app", "ok": true, "ms": 120.4}]
}
```

Rispetto al vecchio registro di sessione (una lista di stringhe che spariva
alla chiusura) qui ci sono le colonne che rendono i dati utilizzabili:

| Campo | A cosa serve |
|---|---|
| `reply_ms` | tempo dal tuo silenzio alla prima voce: la misura della reattività |
| `tools` | quali azioni sono state eseguite, quanto ci hanno messo, se sono riuscite |
| `interrupted` | l'hai interrotto: la risposta è troncata, non è un buon esempio |
| `error` | il turno è fallito |
| `model` | quale modello ha risposto, per confrontarli |

## Comandi

```bash
python -m core.interaction_log stats            # quanti scambi, tempi, azioni che falliscono
python -m core.interaction_log export file.jsonl # esporta nel formato per il fine-tuning
python -m core.interaction_log purge            # cancella tutto (chiede conferma)
```

`stats` è quello che userai più spesso. Esempio:

```
Scambi registrati : 412
Tempo di risposta (dal tuo silenzio alla prima voce)
  mediana : 980 ms
  p90     : 1840 ms
Azioni più usate
  open_app                   64   (tutte riuscite)
  weather_report             31   (7/31 fallite)
```

Quel `7/31 fallite` è il genere di cosa che senza misura non si nota: sono
fallimenti sparsi in settimane diverse, ognuno dimenticato il giorno dopo.

## Privacy

Il file contiene tutto quello che dici al computer.

- Resta sul tuo disco. Non viene inviato da nessuna parte.
- È escluso da git (`memory/interactions/` in `.gitignore`).
- Le stringhe che somigliano a chiavi API o password vengono sostituite con
  `[RIMOSSO]` prima della scrittura. Non è una garanzia assoluta — nessun
  filtro di questo tipo lo è — ma copre il caso più comune, cioè dettare una
  chiave a voce.
- Si disattiva con `"interaction_log": false` in `config/api_keys.json`.
- Si cancella con `python -m core.interaction_log purge`.

Se un giorno vorrai condividere l'archivio o portarlo su una macchina
affittata per l'addestramento, rileggilo prima: nomi di persone, indirizzi e
argomenti personali non vengono filtrati, perché non sono riconoscibili da un
filtro automatico.

## E poi?

Con qualche settimana di dati raccolti diventano possibili, in ordine:

1. **Misurare.** Prendi cinquanta domande ricorrenti dall'archivio e rilanciale
   dopo ogni modifica: è l'unico modo per sapere se hai migliorato o peggiorato.
2. **Migliorare la memoria.** Gli scambi mostrano cosa ti serve ricordare
   davvero, invece di indovinarlo.
3. **Corsia veloce locale.** Le azioni più frequenti e sempre uguali
   (`open_app`, volume, ora) possono essere gestite da un modello piccolo in
   locale, senza passare dalla rete.
4. **Fine-tuning**, per ultimo, quando hai qualche migliaio di esempi e un modo
   per misurare se è servito.
