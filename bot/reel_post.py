"""Reel -> post (Gabriele 21/11). Mandando il link di un reel il bot non lo scarta più:
- reel PARLATO (didattico): trascrive la voce e ne fa un carosello nostro, stessi concetti nella nostra voce;
- reel con una FRASE a schermo: la frase è il post -> post singolo con quella frase, adattata al nostro tono.
Video, fotogrammi e trascrizione sono gli stessi di /copione."""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from . import config, copione, crea, writer

log = logging.getLogger(__name__)

TOOL = {
    "name": "reel",
    "description": "Che tipo di reel è e cosa contiene.",
    "input_schema": {"type": "object", "properties": {
        "tipo": {"type": "string", "enum": ["parlato", "frase"],
                 "description": "parlato = qualcuno spiega a voce (o con molte scritte in sequenza) un concetto: "
                                "diventa un carosello. frase = il reel è una frase/citazione a schermo (su un video, "
                                "una musica, una scena) e quella frase è il contenuto: diventa un post singolo."},
        "frase_originale": {"type": "string", "description": "Solo se tipo=frase: la frase a schermo, esatta."},
        "frase_italiana": {"type": "string", "description": (
            "Solo se tipo=frase: la frase in italiano, nel tono della pagina. Se è già in italiano ed è buona, "
            "lasciala quasi identica; se è in un'altra lingua adattala (non parola per parola), stessa forza, "
            "stessa lunghezza circa. Niente virgolette, niente firma dell'altra pagina.")},
        "contenuto": {"type": "string", "description": (
            "Solo se tipo=parlato: la tesi e tutti i passaggi del ragionamento, in ordine, con gli esempi concreti "
            "che usa. Abbastanza completo da scriverci un carosello senza aver visto il video.")},
    }, "required": ["tipo"]},
}


def leggi(url: str, brand: dict) -> dict:
    video, descrizione = copione.video_da_link(url)
    frames, durata = copione._fotogrammi(video, n=8)
    try:
        parlato = copione._trascrivi(video, durata)
    except Exception as e:
        from . import costi
        if isinstance(e, costi.TettoSpesa):
            raise
        log.warning("Trascrizione del reel non riuscita: %s", e)
        parlato = ""
    system = (f"Leggi un reel di Instagram per la pagina {brand['nome']} (@{brand['handle']}) e dici se è un reel "
              "PARLATO (si spiega un concetto) o una FRASE a schermo (il contenuto è una frase). Una voce che "
              "canta o una musica non è parlato. Leggi bene il testo nei fotogrammi.\n\n"
              f"TONO DELLA PAGINA\n" + "\n".join("- " + t for t in brand.get("tono", [])))
    content = [{"type": "text", "text": (
        f"Durata: {durata:.0f} secondi.\nTRASCRIZIONE DELLA VOCE:\n{parlato or '(nessuna voce)'}\n\n"
        f"DESCRIZIONE DEL REEL:\n{descrizione[:1500] or '(nessuna)'}\n\nFOTOGRAMMI IN ORDINE:")}]
    content += [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": f}} for f in frames]
    out = writer._tool_call(system, content, TOOL, model=config.CAPTION_MODEL, max_tokens=3000)
    out["_parlato"] = parlato
    out["_descrizione"] = descrizione
    return out


def hint_carosello(info: dict) -> str:
    return ("Trasforma in un carosello NOSTRO il contenuto di questo reel didattico: stessa tesi e stessi passaggi, "
            "riscritti nella nostra voce e con il nostro gancio (non una traduzione parola per parola, niente "
            "nomi o firme dell'altra pagina).\n\nCONTENUTO DEL REEL:\n" + (info.get("contenuto") or "")
            + ("\n\nTRASCRIZIONE:\n" + info["_parlato"][:4000] if info.get("_parlato") else ""))


def make_singolo(brand: dict, rules: list[dict], recent: dict, frase: str):
    """Post singolo: una slide con la frase, descrizione e CTA 'segui' (post singolo -> mai il manuale)."""
    frase = writer.clean_text(frase)
    out = {"concetto": frase, "senso": frase, "formato": "singolo", "natura": "virale",
           "slides": [{"numero": 1, "testo": crea.compose(frase.upper(), ""), "testo_originale": "", "da_modificare": True,
                       "e_cta": False, "sfondo": "tinta_unita", "blocchi": [{"testo": frase}]}]}
    cap = writer.write_caption(out, "", brand, rules, recent)
    for k in ("slide_cta", "testo_slide_finale"):
        cap.pop(k, None)
    out["_formato_post"] = cap.pop("formato_post", "")
    post = writer._finalize({**out, **cap})
    post["_originale"] = True
    post["_testo_cta_finale"] = ""
    post["_look"] = {**crea.choose_look("virale"), "stile": "impatto"}   # frase grande, come titolo
    post["_idea"] = {"tema": frase[:80], "natura": "virale", "formato": "singolo", "tipo_hook": "frase"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{random.randint(100, 999)}"
    paths = crea.render_post(post, brand, config.DATA_DIR / "jobs" / f"reel_{stamp}")
    return post, paths
