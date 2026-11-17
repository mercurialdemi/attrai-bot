"""/analizzareel: studio strutturale dei reel di una pagina (Gabriele 17/11, per capire i pattern di Diventa Semidio).
Apify legge i reel, si scaricano i video dei più visti, se ne estraggono alcuni fotogrammi e Claude li analizza."""
from __future__ import annotations

import base64
import io
import logging
import tempfile

import httpx
from apify_client import ApifyClient
from PIL import Image

from . import config, instagram, writer

log = logging.getLogger(__name__)

ANALISI_TOOL = {
    "name": "analisi_reel",
    "description": "Analisi strutturale dei reel di una pagina Instagram.",
    "input_schema": {"type": "object", "properties": {"analisi": {"type": "string"}}, "required": ["analisi"]},
}


def _v(it: dict, *chiavi, default=None):
    for k in chiavi:
        if it.get(k) not in (None, ""):
            return it[k]
    return default


def _leggi(handle: str, quanti: int) -> list[dict]:
    client = ApifyClient(config.APIFY_TOKEN)
    run = client.actor(config.APIFY_ACTOR).call(run_input={
        "directUrls": [f"https://www.instagram.com/{handle}/reels/"], "resultsType": "posts",
        "resultsLimit": quanti, "addParentData": False}, timeout_secs=900)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items()) if run else []
    instagram._conta_letture(len(items))
    video = [it for it in items if _v(it, "videoUrl", "video_url")]
    if not video and config.INSTAGRAM_COOKIES:              # pagina che richiede l'accesso: lettura con i cookie
        items = instagram._run_login_actor([handle], quanti)
        instagram._conta_letture(len(items))
        video = [it for it in items if _v(it, "videoUrl", "video_url")]
    return video


def _fotogrammi(url: str, n: int = 6) -> tuple[list[str], float]:
    """Scarica il video e ne estrae n fotogrammi (inizio fitto, poi distribuiti). Restituisce (jpg base64, durata)."""
    import cv2
    with httpx.Client(timeout=120, follow_redirects=True) as h:
        dati = h.get(url).content
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(dati); f.flush()
        cap = cv2.VideoCapture(f.name)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        tot = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        durata = tot / fps if tot else 0
        tempi = [0.3, 1.5, 3.0] + [durata * p for p in (0.35, 0.65, 0.95)][: max(0, n - 3)]
        out = []
        for t in tempi:
            if durata and t > durata:
                continue
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if not ok:
                continue
            im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            im.thumbnail((480, 860))
            b = io.BytesIO(); im.save(b, "JPEG", quality=70)
            out.append(base64.b64encode(b.getvalue()).decode())
        cap.release()
    return out, durata


def analizza(handle: str, quanti: int = 40, top: int = 8) -> str:
    handle = handle.strip().lstrip("@").strip("/")
    video = _leggi(handle, quanti)
    if not video:
        return f"Non ho trovato reel su @{handle} (pagina privata o nessun video)."
    vis = lambda it: int(_v(it, "videoPlayCount", "videoViewCount", "play_count", "view_count", default=0) or 0)
    video.sort(key=vis, reverse=True)
    media = sum(vis(v) for v in video) / len(video)
    scelti = video[:top] + video[-2:]                       # i migliori + i 2 peggiori, per il confronto
    content = [{"type": "text", "text": f"Pagina @{handle}: {len(video)} reel letti, media visualizzazioni "
                                        f"{media:,.0f}. Seguono i {top} più visti e i 2 meno visti."}]
    for i, it in enumerate(scelti, 1):
        try:
            frames, durata = _fotogrammi(_v(it, "videoUrl", "video_url"))
        except Exception as e:
            log.warning("Video non scaricato: %s", e)
            frames, durata = [], float(_v(it, "videoDuration", default=0) or 0)
        etichetta = "PIÙ VISTO" if i <= top else "MENO VISTO"
        content.append({"type": "text", "text": (
            f"\n=== Reel {i} ({etichetta}) · {vis(it):,} visualizzazioni · {int(_v(it, 'likesCount', default=0) or 0):,} like · "
            f"{int(_v(it, 'commentsCount', default=0) or 0):,} commenti · durata {durata:.0f}s\n"
            f"Descrizione: {(_v(it, 'caption', default='') or '')[:700]}\n"
            f"Fotogrammi a 0,3s / 1,5s / 3s / 35% / 65% / 95%:")})
        content += [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": f}} for f in frames]
    richiesta = (
        "Sei uno stratega di contenuti Instagram. Analizza la STRUTTURA di questi reel e scrivi in italiano, per un "
        "creator che vuole capire il metodo (non copiare personaggi o testi). Sezioni, con elenchi brevi:\n"
        "1. HOOK: cosa succede nei primi 1,5 secondi (testo a schermo, immagine, conflitto) e le formule ricorrenti.\n"
        "2. STRUTTURA: sequenza tipo delle scene, durata, ritmo dei tagli, testo a schermo, ruolo del personaggio fisso.\n"
        "3. TEMI E LEVE: dolori, credenze attaccate, emozioni usate (ego, rabbia, ironia...).\n"
        "4. CHIUSURA E CTA: come finiscono, come portano a seguire, commentare o al prodotto.\n"
        "5. DESCRIZIONI: lunghezza, domanda finale, hashtag.\n"
        "6. COSA HANNO I PIÙ VISTI CHE MANCA AI MENO VISTI.\n"
        "7. FORMAT DA PROVARE su AttraiSeduci (personaggio: uno scheletro elegante con cappello di paglia), 3 idee "
        "concrete che usano gli stessi meccanismi su temi di attrazione e potere maschile, senza deridere le donne.\n"
        "Basati solo su ciò che vedi e leggi; se un dato non si vede (es. audio), dillo.")
    out = writer._tool_call(richiesta, content, ANALISI_TOOL, model=config.CLAUDE_MODEL, max_tokens=6000)
    return f"Analisi dei reel di @{handle} ({len(video)} letti, media {media:,.0f} visualizzazioni)\n\n" + out.get("analisi", "")
