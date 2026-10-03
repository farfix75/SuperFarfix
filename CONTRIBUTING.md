# Contribuire a SuperFarfix

Grazie per l'interesse. Ecco come collaborare senza perdere tempo.

## Segnalare un problema

Apri una issue usando il modello "Bug report". Le cose che servono davvero per
capire un problema sono: sistema operativo, versione di Python, cosa stavi
facendo e il messaggio d'errore completo.

Se il problema riguarda l'audio (non sente, si interrompe da solo, risponde in
ritardo), aggiungi sempre: **cuffie o casse?** È la singola informazione che
spiega la maggior parte dei problemi audio.

> ⚠️ Non incollare mai chiavi API nelle issue. Se ne hai pubblicata una per
> sbaglio, revocala subito dal pannello del provider.

## Proporre una modifica

1. Fai un fork e crea un ramo: `git checkout -b mia-modifica`
2. Modifica il codice
3. Controlla che tutto compili ed i test passino:

```bash
python -m compileall -q .
python -m unittest discover -s tests
```

4. Apri la pull request spiegando **cosa** cambia e **perché**

## Stile del codice

Il progetto segue alcune convenzioni proprie, visibili leggendo i file:

- **I commenti spiegano il perché, non il cosa.** Un commento che ripete quello
  che il codice già dice è rumore; uno che spiega perché un valore è 420 e non
  600 evita che qualcuno lo "sistemi" rompendolo.
- **Nessun numero magico senza motivazione.** Se una costante è stata misurata,
  scrivi come.
- **L'interfaccia non deve mai morire per motivi estetici.** Il rendering del
  volto è avvolto in try/except: se il volto fallisce, l'HUD continua a
  funzionare.
- **Niente dipendenze nuove senza necessità reale.** Il volto è disegnato con
  PyQt6 e numpy, senza OpenGL e senza GPU, e deve restare così.

## Aree dove l'aiuto serve di più

- Prove su hardware audio diverso (l'interruzione vocale dipende molto dalla stanza)
- Supporto macOS e Linux, meno collaudati di Windows
- Traduzioni dell'interfaccia
- Nuove azioni in `actions/` e plugin in `plugins/`
