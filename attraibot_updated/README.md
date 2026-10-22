# AttraiSeduci Bot - configurazione report

Report programmato alle **08:00 Europe/Rome**, finestra **48 ore**, shortlist **15** post e produzione dei **5** migliori.

## Prima dell’avvio
1. Aggiungi gli handle reali dei collaboratori in `brands/attraiseduci.yaml` → `pagine_monitorate`.
2. Imposta `report.chat_id` con l’ID della chat Telegram che riceverà i report (`/id`).
3. Configura su Render le variabili di `.env.example` (non caricare chiavi reali nel repository).
4. Carica il logo in `assets/logo.png` e, se disponibile, il font in `assets/font.ttf`.
5. Avvia il worker con `python -m bot.main`.

**Nota:** la classifica dipende dai dati effettivamente restituiti da Apify. Il bot può produrre meno di 15 candidati o 5 post quando i contenuti disponibili sono insufficienti o un'elaborazione fallisce. Il punteggio non è ancora normalizzato sulle medie storiche dei singoli profili. Nessun test live con API è stato eseguito.
