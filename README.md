# AttraiBot: dal link al post pronto per AttraiSeduci

Mandi al bot su Telegram il link di un post dei vostri collaboratori autorizzati (anche in inglese).
Il bot ti restituisce:

- le slide in italiano, con illustrazioni e stile dell'originale, senza il watermark della pagina di origine e con il logo AttraiSeduci;
- la descrizione già scritta con il tono di AttraiSeduci (max 2200 caratteri, niente asterischi o trattini lunghi);
- la CTA scelta secondo le vostre regole (segui la pagina oppure manuale giusto), con il motivo della scelta.

Poi puoi chiedere modifiche a parole ("slide 2 più corta", "cambia l'hook", "CTA al manuale Potere Sociale", "rifai la grafica della slide 4"): il bot rifà solo quello che cambia.

Con `regola: ...` dai una regola fissa che il bot applica da quel momento a ogni post. `/regole` le elenca, `/togli 3` toglie la numero 3.

Il bot accetta i link che gli mandate senza controlli: sta a voi mandare solo post di pagine che vi hanno autorizzato.

## Come lavora su ogni post

- **Slide:** il testo viene tradotto in modo fedele (stesso contenuto, ordine e tono dell'originale, in italiano naturale). Se preferisci che sia riscritto nel tono di AttraiSeduci, metti `traduzione_slide: adattata` nel profilo del brand.
- **Descrizione e CTA:** sono un lavoro separato, scritto sempre nel tono di AttraiSeduci, con la guida di stile ricavata da `/analizza`.
- **Watermark:** il logo AttraiSeduci va solo sulle slide che avevano il watermark della pagina di origine. Se lo vuoi sempre, metti `solo_se_presente: false` nella sezione `watermark`.
- **Controllo automatico:** ogni slide prodotta da Gemini viene riletta da Claude. Se il testo ha errori o restano parti in inglese o il vecchio watermark, Gemini riprova con l'indicazione di cosa correggere, fino a 3 volte.
- **Post incompleti:** se una slide non passa il controllo dopo tutti i tentativi, non viene inviata e il post arriva marcato "POST INCOMPLETO, non pubblicarlo". Scrivi `riprova` per rifare solo quelle slide.

## Testo delle slide

Il testo delle slide lo scrive sempre Claude e lo disegna il codice, mai Gemini:
- slide di solo testo su sfondo uniforme: il codice cancella il testo originale e riscrive l'italiano nello stesso punto, stessa dimensione, colore e allineamento (font Inter);
- slide con illustrazioni: Gemini cancella solo il testo, poi il codice scrive l'italiano nei riquadri del testo originale indicati da Claude.

## Slide CTA finale

In fondo a ogni carosello va la vostra slide CTA (segui la pagina o il manuale scelto), al posto della CTA della pagina di origine. Le slide CTA le prende /analizza dai vostri caroselli migliori; puoi anche caricarle a mano mandando al bot l'immagine con didascalia `cta segui` (o `cta friendzone`, `cta potere-sociale`...). Quelle caricate a mano hanno la precedenza.

## File per Canva

Con ogni post arriva un file .pptx: in Canva → Crea → Importa file. Nelle slide di testo le scritte sono modificabili.

## Studio della pagina: /analizza

Legge gli ultimi 80 post di AttraiSeduci, confronta le descrizioni dei migliori con quelle dei peggiori e ricava una guida di stile concreta (aperture, struttura, ritmo, dolori che rendono, come chiudete, cosa evitare). Sceglie anche 6-8 tue descrizioni come esempi, estrae le CTA di ogni post e le ordina per rendimento: metà delle volte il bot riusa (adattata) una delle CTA migliori, l'altra metà ne scrive una nuova sulle stesse leve. Il rendimento si misura su like e commenti rispetto alla media: i clic sul link arriveranno con le API di Instagram. Da quel momento ogni descrizione viene scritta seguendo la guida, con 3 esempi diversi ogni volta. `/stile` mostra la guida attuale. Conviene rilanciarlo una volta al mese.

## Miglioramento continuo

Ogni lunedì alle 7:30 il bot rifà da solo lo studio della pagina e ti manda un riepilogo:
- aggiorna guida di stile, esempi e classifica delle CTA;
- riconosce tra i vostri post pubblicati quelli preparati da lui e misura quanto hanno reso per tipo di hook e di CTA;
- da lì in avanti preferisce hook e CTA che rendono di più, continuando a variare.

Senza collegamento il rendimento si misura su like e commenti. Con `INSTAGRAM_TOKEN` su Render (vedi sotto) il bot usa le statistiche vere di ogni post: copertura, salvataggi, condivisioni, nuovi follower e clic sul link in bio. Le CTA ai manuali si ordinano per clic sul link ogni 1000 persone raggiunte, quelle "segui" per nuovi follower. `/statistiche` mostra i numeri degli ultimi post. La chiave dura 60 giorni e il bot la rinnova da solo ogni settimana.

## Report delle 8

Ogni mattina alle 8 il bot:
1. legge gli ultimi 30 post di ogni pagina in `pagine_monitorate` e salva i loro numeri, così giorno dopo giorno costruisce la media storica di ogni pagina (ultimi 90 giorni);
2. calcola il punteggio di ogni post: quante volte ha reso rispetto alla media della sua pagina (un commento vale 3 like);
3. mette in classifica i 15 migliori post delle ultime 48 ore non ancora lavorati;
4. fa scegliere a Claude i 5 più adatti ad AttraiSeduci, preferendo quelli sopra la media e con temi diversi tra loro;
5. li prepara e li manda a te e al socio, ognuno con il motivo della scelta.

Se i post sopra la media sono meno di 5, completa con i migliori sotto la media e te lo segnala ("sotto media"). Con `completa_sempre: false` invece prepara solo quelli sopra la media.

`/report` lo lancia subito, `/altri` prepara i 5 successivi della classifica senza ripetere quelli già fatti, `/pagine` mostra le pagine monitorate.

## Cosa c'è dentro

| File | A cosa serve |
|---|---|
| `brands/attraiseduci.yaml` | Profilo del brand: pagine monitorate, report, tono, regole, CTA, manuali, posizione del logo. **Si modifica qui, non nel codice.** |
| `assets/logo.png` | Il vostro logo (da aggiungere: quadrato, viene ritagliato a cerchio). |
| `assets/font.ttf` | Font per la scritta "AttraiSeduci" accanto al logo. Puoi sostituirlo con il font del brand. |
| `bot/` | Il codice. |

## Prima di andare online: 3 cose da compilare

1. In `brands/attraiseduci.yaml`, sotto `pagine_monitorate`, scrivi gli handle Instagram delle vostre pagine e dei collaboratori autorizzati (senza @, minuscolo, due spazi e trattino davanti).
2. Sempre lì, sotto `manuali`, sostituisci i `DA COMPILARE` con i dolori che ogni manuale risolve. È da qui che il bot capisce quale manuale proporre.
3. Metti il logo in `assets/logo.png`.

## Messa online su Render (circa 15 minuti, circa 7,50 $ al mese)

Il report delle 8 parte da solo e arriva a tutti gli ID in `ALLOWED_USER_IDS`.

Il bot gira su Render come "Background Worker": sempre acceso, con un piccolo disco dove restano post, regole e CTA.

**1. Chiave Gemini e Apify** (le altre le hai già)
- Gemini: aistudio.google.com → Get API key. Attiva la fatturazione, altrimenti la generazione immagini ha limiti molto bassi.
- Apify: apify.com → Settings → API & Integrations → copia il Personal API token.

**2. Carica il progetto su GitHub**
- github.com → New repository → nome `attraibot`, **Private**.
- "uploading an existing file" → trascina tutto il contenuto della cartella (non la cartella stessa) → Commit.
- Controlla che nella lista compaia `render.yaml`: è il file che dice a Render come avviare il bot.

**3. Crea il servizio su Render**
- render.com → registrati con l'account GitHub e aggiungi una carta (il worker è a pagamento, non esiste la versione gratuita).
- **New → Blueprint** → collega il repository `attraibot`.
- Render legge `render.yaml` e propone il worker `attraibot` con il disco già configurato. Ti chiede i valori delle chiavi:
  - `TELEGRAM_BOT_TOKEN` (da BotFather)
  - `ANTHROPIC_API_KEY`
  - `GEMINI_API_KEY`
  - `APIFY_TOKEN`
  - `ALLOWED_USER_IDS`: per ora scrivi `0`
- **Apply**. Render installa tutto e avvia il bot (2-3 minuti).

**4. Dai l'accesso a te e al tuo socio**
- Scrivi `/id` al bot, fallo fare anche al tuo socio.
- Su Render apri il servizio → **Environment** → `ALLOWED_USER_IDS` → metti i due numeri separati da virgola (es. `12345678,87654321`) → Save. Il bot si riavvia da solo.

**5. Prova**: scrivi /analizza, poi mandagli il link di un carosello di un collaboratore, poi /report per vedere subito il report.

## Impostazioni facoltative (scheda Environment)

| Variabile | Default | Quando cambiarla |
|---|---|---|
| `SLIDE_CHECK` | `on` | `off` spegne il controllo automatico delle slide (costa meno, rischi errori). |
| `SLIDE_ATTEMPTS` | `3` | Tentativi di Gemini per slide prima di segnalarla. |
| `CHECK_MODEL` | `claude-haiku-5-5` | Modello che controlla le slide. |
| `SLIDE_TEXT_MODE` | `gemini` | Metti `overlay` se Gemini sbaglia lettere nel testo italiano: Gemini pulisce la slide e il testo lo scrive il codice, sempre corretto ma con il font di `assets/font.ttf` invece di quello originale. |
| `GEMINI_IMAGE_MODEL` | `gemini-2.5-flash-image` | Un modello più recente (es. `gemini-3.1-flash-image-preview`) scrive il testo in modo più preciso. |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | `claude-haiku-5-5` costa meno ma scrive peggio. |
| `MAX_PARALLEL_IMAGES` | `4` | Slide generate in contemporanea. |

## Se qualcosa non va

- Il bot non risponde: su Render apri il servizio → **Logs**. Se manca una chiave lo dice nella prima riga.
- Nei log compare "Conflict: terminated by other getUpdates": lo stesso bot gira anche da un'altra parte (per esempio una vecchia prova). Spegni l'altro, ne deve girare uno solo.
- "Non riesco a leggere il post": il post deve essere pubblico e il link completo.
- Un post arriva "INCOMPLETO": alcune slide non hanno passato il controllo. Scrivi `riprova`; se fallisce spesso, prova `SLIDE_TEXT_MODE=overlay`.

## Prossime fasi

3. Studio di cosa funziona anche sui dati di click e conversioni (dolori, hook, strutture, CTA).
4. Caroselli originali da zero.
5-6. Statistiche reali dalle API di Instagram, misura dei clic e miglioramento automatico delle CTA.
8. File per Canva e dashboard web.

La tabella `cta_log` del database registra già da ora ogni CTA usata, così i dati per la fase 6 si accumulano fin dal primo post.
