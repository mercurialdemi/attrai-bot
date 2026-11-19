"""/copione: da un reel (link o video mandato in chat) a un copione di circa 60 secondi con lo stesso senso
dell'originale ma con il NOSTRO tono e il nostro scheletro (Gabriele 19/11).
Apify scarica il reel, OpenAI trascrive la voce, si prendono alcuni fotogrammi e Claude riscrive."""
from __future__ import annotations

import base64
import io
import logging
import tempfile

import httpx
from PIL import Image

from . import config, writer

log = logging.getLogger(__name__)

MODELLO = config.ROOT / "brands" / "comuni" / "copione_modello.md"
TRASCRIZIONE_MODEL = config._env("TRASCRIZIONE_MODEL", "whisper-1")
DOLLARI_AL_MINUTO = 0.006

TOOL = {
    "name": "copione",
    "description": "Copione del video riscritto nel tono di AttraiSeduci.",
    "input_schema": {"type": "object", "properties": {
        "senso_originale": {"type": "string", "description": "In 2-3 righe: la tesi del video originale e come è costruito (hook, conflitto, spiegazione, chiusa)."},
        "copione": {"type": "string", "description": "Il copione completo, pronto da registrare, nel formato del modello."},
    }, "required": ["senso_originale", "copione"]},
}


class CopioneError(RuntimeError):
    pass


def video_da_link(url: str) -> tuple[bytes, str]:
    """Scarica il reel dal link. Restituisce (video, descrizione originale)."""
    from apify_client import ApifyClient
    from . import instagram
    client = ApifyClient(config.APIFY_TOKEN)
    run = client.actor(config.APIFY_ACTOR).call(run_input={
        "directUrls": [url], "resultsType": "posts", "resultsLimit": 1, "addParentData": False}, timeout_secs=240)
    items = [i for i in (client.dataset(run["defaultDatasetId"]).iterate_items() if run else []) if not i.get("error")]
    instagram._conta_letture(len(items))
    it = next((i for i in items if i.get("videoUrl") or i.get("video_url")), None)
    if it is None and config.INSTAGRAM_COOKIES:
        try:
            items = instagram._run_login_actor([url], 1)
            it = next((i for i in items if i.get("videoUrl") or i.get("video_url")), None)
        except Exception as e:
            log.warning("Lettura con accesso fallita: %s", e)
    if it is None:
        raise CopioneError("Non trovo il video in questo link: è un reel pubblico? Puoi anche mandarmi il video "
                           "direttamente e rispondergli con /copione.")
    with httpx.Client(timeout=120, follow_redirects=True) as h:
        r = h.get(it.get("videoUrl") or it.get("video_url"))
        r.raise_for_status()
        return r.content, str(it.get("caption") or "")


def _fotogrammi(video: bytes, n: int = 10) -> tuple[list[str], float]:
    import cv2
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video)
        f.flush()
        cap = cv2.VideoCapture(f.name)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        tot = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        durata = tot / fps if tot else 0
        tempi = [0.4] + [durata * (k + 0.5) / (n - 1) for k in range(n - 1)] if durata else [0.4]
        out = []
        for t in tempi:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if not ok:
                continue
            im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            im.thumbnail((420, 760))
            b = io.BytesIO()
            im.save(b, "JPEG", quality=68)
            out.append(base64.b64encode(b.getvalue()).decode())
        cap.release()
    return out, durata


def _trascrivi(video: bytes, durata: float) -> str:
    if not config.OPENAI_API_KEY:
        return ""
    from openai import OpenAI
    from . import costi
    costi.controlla("trascrizione")
    cl = OpenAI(api_key=config.OPENAI_API_KEY, timeout=180, max_retries=1)
    r = cl.audio.transcriptions.create(model=TRASCRIZIONE_MODEL, file=("reel.mp4", video, "video/mp4"))
    try:
        costi._aggiungi("trascrizione OpenAI", max(durata, 30) / 60 * DOLLARI_AL_MINUTO * costi.EURO_PER_DOLLARO)
    except Exception:
        pass
    return (getattr(r, "text", "") or "").strip()


def crea(video: bytes, descrizione: str = "", note: str = "", brand: dict | None = None) -> str:
    brand = brand or writer.load_brand()
    frames, durata = _fotogrammi(video)
    try:
        parlato = _trascrivi(video, durata)
    except Exception as e:
        from . import costi
        if isinstance(e, costi.TettoSpesa):
            raise
        log.warning("Trascrizione non riuscita: %s", e)
        parlato = ""
    if not parlato and not frames:
        raise CopioneError("Non riesco né a sentire né a vedere questo video.")
    modello = MODELLO.read_text(encoding="utf-8") if MODELLO.exists() else ""
    guida = writer.guida_pagina(brand) or ""
    system = (
        "Sei l'autore dei video di AttraiSeduci. Ricevi un video di un'altra pagina (trascrizione della voce, "
        "fotogrammi, descrizione) e lo RISCRIVI come copione per un nostro video di circa 60 secondi.\n\n"
        "COSA TIENI DELL'ORIGINALE: la tesi, il ragionamento e la struttura (hook, conflitto, spiegazione, chiusa). "
        "Lo spettatore deve arrivare alla stessa conclusione. Non copi frasi intere, personaggi o battute: riscrivi.\n\n"
        "COME LO RISCRIVI (tono del modello qui sotto, è il riferimento assoluto):\n"
        "- La voce che svela la verità è il NOSTRO SCHELETRO (elegante, cappello di paglia, sigaro, beffardo, "
        "teatrale). Prende il posto del narratore o del personaggio guida dell'originale.\n"
        "- Una scena concreta con un personaggio che dice la frase ingenua o la falsa credenza; lo scheletro la smonta.\n"
        "- Se l'interlocutrice è una donna, lui la chiama 'principessa' / 'Vostra Altezza', con sarcasmo.\n"
        "- Battute corte (3-12 parole), una per riga: ognuna diventa un sottotitolo e un'immagine. Risate scritte "
        "(AHAHAH), parole chiave in MAIUSCOLO, puntini per le pause.\n"
        "- Circa 140-170 parole parlate in tutto. Massimo 4-5 scene.\n"
        "- Chiusa velenosa, una sentenza secca, poi *STACCO SU NERO.*\n"
        "- Studi e numeri: solo quelli citati nell'originale o studi reali che conosci con certezza. Mai inventare.\n"
        "- Le tesi seguono la visione di AttraiSeduci qui sotto.\n\n"
        "FORMATO: 'TITOLO FISSO IN ALTO: ...' (formula con i due punti, es. 'LA VERITÀ BRUTALE:'), poi "
        "'SCENA N — LUOGO', battute 'NOME: «...»' e indicazioni di scena tra *asterischi*.\n\n"
        f"=== MODELLO DI TONO ===\n{modello}\n\n=== VISIONE ATTRAISEDUCI ===\n{guida}")
    content = [{"type": "text", "text": (
        f"Durata originale: {durata:.0f} secondi.\n"
        f"TRASCRIZIONE DELLA VOCE:\n{parlato or '(nessuna voce trascritta: basati su fotogrammi e testo a schermo)'}\n\n"
        f"DESCRIZIONE ORIGINALE:\n{descrizione[:1500] or '(nessuna)'}\n\n"
        + (f"RICHIESTA DI GABRIELE: {note}\n\n" if note else "")
        + "FOTOGRAMMI IN ORDINE (leggi anche il testo a schermo):")}]
    content += [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": f}} for f in frames]
    out = writer._tool_call(system, content, TOOL, model=config.STORIE_MODEL, max_tokens=6000)
    return (f"🎬 COPIONE\n\nL'originale in breve: {out.get('senso_originale', '').strip()}\n\n"
            f"{out.get('copione', '').strip()}")
