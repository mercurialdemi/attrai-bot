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
        },
        "required": ["guida", "dolori", "da_evitare", "esempi"],
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
        "Niente consigli generici da manuale di copywriting: solo quello che si vede in questi testi."
    )
    content = [{"type": "text", "text": (
        f"MIGLIORI ({len(top)}):\n\n{block(top)}\n\n"
        f"PEGGIORI ({len(bottom)}):\n\n{block(bottom)}\n\n"
        "Restituisci lo studio con lo strumento studio_pagina."
    )}]
    out = writer._tool_call(system, content, ANALYSIS_TOOL, max_tokens=6000)

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
        "post_analizzati": len(scored),
        "creato": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "stile.json").write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
    return style
