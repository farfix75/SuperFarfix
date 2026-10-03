# FARFIX Multi-AI — installazione

Esegui `python setup.py`. L'installer apre un modulo grafico con campi per:

- Google Gemini API Key
- OpenAI / ChatGPT API Key
- Anthropic / Claude API Key
- Ollama URL e API key opzionale
- Modello di ciascun provider
- Provider AUTO

Le chiavi vengono salvate localmente in `config/api_keys.json`. Non inserire chiavi reali in un repository Git.

`python farfix_ai.py status` mostra lo stato dei provider senza stampare le chiavi.


---

## xKiro (gateway)

xKiro non è un modello: è un intermediario. Con **una sola chiave** raggiungi i
modelli di OpenAI, Anthropic, Google, DeepSeek, Qwen, Mistral e altri, con
fatturazione unica e cambio automatico di percorso se un fornitore è in
difficoltà.

### Configurazione

1. Crea la chiave su <https://xkiro.com/dashboard/api/keys> (viene mostrata una
   volta sola: non è recuperabile dopo).
2. Mettila in `.env.local`:

```
XKIRO_API_KEY=sk-xt-...
```

3. Scegli il modello nelle impostazioni del plugin **FARFIX AI ENGINE**, oppure
   in `config/api_keys.json`:

```json
"farfix_ai": {
  "xkiro_model": "openai/gpt-5.6-sol",
  "xkiro_url": "https://api.xkiro.com/v1"
}
```

### L'unica cosa che si sbaglia sempre

Gli identificativi sono nella forma `vendor/modello`, e il nome nudo **non
viene risolto**:

```
"openai/gpt-5.6-sol"   ✅
"gpt-5.6-sol"          ❌  404 not_found
```

Il gateway risponderebbe «modello non trovato», che fa pensare a un modello
inesistente mentre il problema è solo il prefisso. Per questo FARFIX controlla
prima di inviare e ti dice esplicitamente cosa manca.

Per vedere il catalogo aggiornato:

```python
from core.ai_router import list_xkiro_models
print(list_xkiro_models())
```

### Come si inserisce nella catena

Con il motore su `auto`, l'ordine di tentativo è:

```
openai → grok → google → xkiro → ollama → anthropic → compatible
```

xKiro sta dopo i fornitori diretti e prima di quelli locali: raggiunge gli
stessi modelli passando per un intermediario, quindi ha senso come rete di
sicurezza quando una chiave diretta manca o il fornitore ha un momento storto.
Per usarlo come motore principale, imposta il provider su `xkiro` (a voce:
«usa xkiro»).

### Cosa non cambia

La voce in tempo reale resta su **Gemini Live**: xKiro instrada chat testuali,
non sessioni audio bidirezionali. Quindi xKiro entra nelle risposte testuali e
nelle azioni, non nel dialogo parlato.

### Due avvertenze pratiche

- **Latenza.** Un intermediario aggiunge un passaggio di rete. Per le risposte
  testuali è irrilevante; è un motivo in più per non usarlo dove serve la
  massima velocità.
- **Privacy.** Le richieste passano dai server di xKiro oltre che da quelli del
  fornitore finale: una parte in più che vede il contenuto delle conversazioni.
