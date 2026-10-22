"""/analizza: studia i post di AttraiSeduci e ricava guida di stile ed esempi per le descrizioni.

1. Legge gli ultimi N post della pagina (default 80).
2. Li ordina per rendimento rispetto alla mediana della pagina.
3. Claude confronta le descrizioni dei post migliori con quelle dei peggiori e scrive una guida concreta:
   aperture, struttura, ritmo, lessico, dolori che rendono, come chiudono, come sono scritte le CTA.
4. Sceglie le descrizioni migliori come esempi.
Tutto viene salvato in data/stile.json e usato da writer.write_caption a ogni descrizione.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone

from . import config, instagram, writer

log = logging.getLogger(__name__)

ANALYSIS_TOOL = {
    "name": "studio_pagina",
    "description": "Risultato dello studio della pagina.",
    "input_schema": {
        "type": "object",
        "properties": {
            "guida": {"type": "string", "description": "Guida di stile in 10-15 punti concreti e verificabili, ognuno su una riga che inizia con '- '. Basata su cosa distingue i post migliori dai peggiori, non su principi generici."},
            "dolori": {"type": "array", "items": {"type": "string"}, "description": "I dolori e i temi che rendono di più, dal più forte."},
            "da_evitare": {"type": "array", "items": {"type": "string"}, "description": "Cosa hanno in comune i post peggiori."},
            "esempi": {"type": "array", "items": {"type": "integer"}, "description": "Numeri (dall'elenco MIGLIORI) delle 6-8 descrizioni più rappresentative da usare come esempi, varie tra loro per struttura e tema."},
            "cta": {
                "type": "array",
                "description": "La CTA finale di OGNI descrizione (MIGLIORI e PEGGIORI), copiata parola per parola.",
                "items": {
                    "type": "object",
                    "properties": {
                        "gruppo": {"type": "string", "enum": ["migliori", "peggiori"]},
                        "numero": {"type": "integer", "description": "Numero del post nel suo elenco."},
                        "testo": {"type": "string", "description": "La CTA esatta, dall'inizio dell'invito fino alla fine."},
                        "tipo": {"type": "string", "enum": ["segui", "manuale", "altro"]},
                        "manuale": {"type": "string", "description": "id del manuale se tipo=manuale, altrimenti vuoto."},
                    },
                    "required": ["gruppo", "numero", "testo", "tipo", "manuale"],
                },
            },
            "lezioni_cta": {"type": "string", "description": "In 4-6 righe: cosa hanno in comune le CTA dei post che rendono di più rispetto a quelle dei peggiori (leva emotiva, lunghezza, formula, posizione)."},
        },
        "required": ["guida", "dolori", "da_evitare", "esempi", "cta", "lezioni_cta"],
    },
}


def _engagement(p: instagram.SourcePost) -> float | None:
    likes = p.likes if (p.likes is not None and p.likes >= 0) else None
    comments = p.comments if (p.comments is not None and p.comments >= 0) else None
    if likes is None and comments is None:
        return None
    return (likes or 0) + 3 * (comments or 0)


def run_analysis(brand: dict, n_posts: int = 80) -> dict:
    handle = brand["handle"].lower().lstrip("@")
    posts = instagram.fetch_profiles([handle], n_posts)
    scored = [(p, _engagement(p)) for p in posts]
    scored = [(p, e) for p, e in scored if e is not None and len((p.caption or "").strip()) >= 120]
    if len(scored) < 12:
        raise instagram.InstagramError(
            f"Ho trovato solo {len(scored)} post di @{handle} con descrizione e numeri leggibili: troppo pochi per uno studio affidabile.")
    med = max(statistics.median(e for _, e in scored), 1.0)
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:25]
    bottom = scored[-10:]

    def block(items):
        return "\n\n".join(
            f"{i}. {e / med:.1f}x la media · {p.kind}\n<<<\n{p.caption.strip()[:2200]}\n>>>"
            for i, (p, e) in enumerate(items, 1))

    system = (
        f"Sei un analista editoriale. Studi la pagina Instagram italiana {brand['nome']} "
        f"({brand.get('posizionamento', '').strip()}) per capire come scrivere descrizioni che rendono.\n"
        "Confronta le descrizioni MIGLIORI con le PEGGIORI e ricava regole concrete: tipo di apertura e prima riga, "
        "lunghezza, divisione in paragrafi, ritmo delle frasi, lessico ricorrente, uso di autori e studi, "
        "come costruiscono la tensione, come chiudono e come è scritta la CTA. "
        "Niente consigli generici da manuale di copywriting: solo quello che si vede in questi testi.\n"
        "Estrai anche la CTA finale di ogni descrizione e confronta le CTA dei post migliori con quelle dei peggiori.\n"
        "Manuali in vendita: " + ", ".join(f"{m['id']} ({m['nome']})" for m in brand.get("manuali", []))
    )
    content = [{"type": "text", "text": (
        f"MIGLIORI ({len(top)}):\n\n{block(top)}\n\n"
        f"PEGGIORI ({len(bottom)}):\n\n{block(bottom)}\n\n"
        "Restituisci lo studio con lo strumento studio_pagina."
    )}]
    out = writer._tool_call(system, content, ANALYSIS_TOOL, max_tokens=12000)

    # CTA con il rendimento del loro post: le migliori il bot le riusa più spesso
    groups = {"migliori": top, "peggiori": bottom}
    cta_list = []
    for c in out.get("cta", []):
        items = groups.get(c.get("gruppo"), [])
        n = c.get("numero")
        if isinstance(n, int) and 1 <= n <= len(items) and (c.get("testo") or "").strip():
            cta_list.append({"testo": writer.clean_text(c["testo"]), "tipo": c.get("tipo"),
                             "manuale": c.get("manuale") or "", "rendimento": round(items[n - 1][1] / med, 2)})
    cta_list.sort(key=lambda c: c["rendimento"], reverse=True)
    cta_slides = _find_cta_slides([p for p, _ in top], brand)

    esempi = []
    for n in out.get("esempi", []):
        if isinstance(n, int) and 1 <= n <= len(top):
            esempi.append(writer.clean_text(top[n - 1][0].caption.strip()))
    guida = out.get("guida", "").strip()
    if out.get("dolori"):
        guida += "\n- Dolori e temi che rendono di più: " + "; ".join(out["dolori"][:8]) + "."
    if out.get("da_evitare"):
        guida += "\n- Da evitare (tipico dei post peggiori): " + "; ".join(out["da_evitare"][:6]) + "."

    style = {
        "guida": guida,
        "esempi": list(dict.fromkeys(esempi)),
        "cta_migliori": [c for c in cta_list if c["rendimento"] >= 1.0][:12],
        "cta_peggiori": [c for c in cta_list if c["rendimento"] < 0.7][-6:],
        "lezioni_cta": (out.get("lezioni_cta") or "").strip(),
        "slide_cta": cta_slides,
        "post_analizzati": len(scored),
        "creato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "stile.json").write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
    return style


CTA_SLIDE_TOOL = {
    "name": "slide_cta",
    "description": "Classifica le ultime slide dei caroselli.",
    "input_schema": {
        "type": "object",
        "properties": {
            "slide": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "numero": {"type": "integer"},
                        "tipo": {"type": "string", "enum": ["segui", "manuale", "nessuna"],
                                 "description": "segui = invita a seguire la pagina; manuale = invita a comprare un manuale; nessuna = non è una slide CTA."},
                        "manuale": {"type": "string", "description": "id del manuale se tipo=manuale, altrimenti vuoto."},
                    },
                    "required": ["numero", "tipo", "manuale"],
                },
            },
        },
        "required": ["slide"],
    },
}


def _find_cta_slides(top_posts, brand: dict) -> dict:
    """Prende l'ultima slide dei caroselli migliori, riconosce quali sono slide CTA e salva la migliore
    per ogni tipo (segui / manuale) come slide CTA da mettere in fondo ai caroselli.
    Non sovrascrive le slide CTA caricate a mano."""
    cands = [p for p in top_posts if p.kind.lower() == "sidecar" and len(p.image_urls) >= 2][:10]
    if not cands:
        return {}
    try:
        imgs = instagram.download_images([p.image_urls[-1] for p in cands])
    except Exception as e:
        log.warning("Slide CTA non scaricate: %s", e)
        return {}
    content = []
    for i, data in enumerate(imgs, 1):
        content += [{"type": "text", "text": f"Slide {i}:"}, writer._image_block(data, 800)]
    content.append({"type": "text", "text": "Manuali: " + ", ".join(m["id"] for m in brand.get("manuali", []))
                    + ". Classifica ogni slide con lo strumento slide_cta."})
    try:
        out = writer._tool_call("Riconosci le slide di call to action di una pagina Instagram.", content,
                                CTA_SLIDE_TOOL, max_tokens=2000)
    except Exception as e:
        log.warning("Classificazione slide CTA fallita: %s", e)
        return {}
    cta_dir = config.DATA_DIR / "cta"
    cta_dir.mkdir(parents=True, exist_ok=True)
    found = {}
    for s in out.get("slide", []):            # i caroselli sono già in ordine di rendimento: la prima trovata è la migliore
        n, tipo = s.get("numero"), s.get("tipo")
        if not isinstance(n, int) or not (1 <= n <= len(imgs)) or tipo == "nessuna":
            continue
        key = "segui" if tipo == "segui" else (s.get("manuale") or "")
        if not key or key in found:
            continue
        target = cta_dir / f"{key}.jpg"
        manual = cta_dir / f"{key}.manuale"
        if not manual.exists():                # se l'hai caricata tu a mano, resta la tua
            target.write_bytes(imgs[n - 1])
        found[key] = cands[n - 1].url
    return found
