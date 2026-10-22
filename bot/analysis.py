"""/analizza: studia i post di AttraiSeduci e ricava guida di stile ed esempi per le descrizioni.

1. Legge gli ultimi N post della pagina (default 80).
2. Li ordina per rendimento rispetto alla mediana della pagina.
3. Claude confronta le descrizioni dei post migliori con quelle dei peggiori e scrive una guida concreta:
   aperture, struttura, ritmo, lessico, dolori che rendono, come chiudono, come sono scritte le CTA.
4. Sceglie le descrizioni migliori come esempi.
Tutto viene salvato in data/stile.json e usato da writer.write_caption a ogni descrizione.
"""
from __future__ import annotations

import difflib
import json
import logging
import statistics
from datetime import datetime, timezone

from . import config, instagram, instagram_api, writer

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
    stats: dict = {}
    if instagram_api.enabled():
        try:
            stats = instagram_api.stats_by_shortcode(n_posts)
        except Exception as e:
            log.warning("Statistiche Instagram non lette, uso like e commenti: %s", e)
    def value(p):
        st = stats.get(instagram_api.shortcode(p.url))
        sc = instagram_api.score(st) if st else None
        return sc if sc is not None else _engagement(p)
    scored = [(p, value(p)) for p in posts]
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
            st = stats.get(instagram_api.shortcode(items[n - 1][0].url)) or {}
            entry = {"testo": writer.clean_text(c["testo"]), "tipo": c.get("tipo"),
                     "manuale": c.get("manuale") or "", "rendimento": round(items[n - 1][1] / med, 2)}
            if st.get("reach"):
                entry["clic_link"] = st.get("clic_link", 0)
                entry["clic_ogni_1000"] = round(1000 * (st.get("clic_link") or 0) / st["reach"], 2)
                entry["follow_ogni_1000"] = round(1000 * (st.get("follows") or 0) / st["reach"], 2)
            cta_list.append(entry)
    # con le statistiche vere le CTA si ordinano per quello che devono ottenere:
    # clic sul link per le CTA ai manuali, nuovi follower per quelle "segui"
    def cta_key(c):
        if "clic_ogni_1000" in c:
            return c["clic_ogni_1000"] * 10 if c["tipo"] == "manuale" else c["follow_ogni_1000"] * 10
        return c["rendimento"]
    cta_list.sort(key=cta_key, reverse=True)
    cta_slides = _find_cta_slides([p for p, _ in sorted(scored, key=lambda x: -x[1])], brand)
    feedback = _bot_feedback([(p, e / med) for p, e in scored])

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
        "cta_migliori": (cta_list[:12] if stats else [c for c in cta_list if c["rendimento"] >= 1.0][:12]),
        "cta_peggiori": (cta_list[-6:] if stats and len(cta_list) > 12 else [c for c in cta_list if c["rendimento"] < 0.7][-6:]),
        "fonte_numeri": "statistiche Instagram" if stats else "like e commenti",
        "lezioni_cta": (out.get("lezioni_cta") or "").strip(),
        "slide_cta": {k: v for k, v in cta_slides.items() if not k.startswith("_")},
        "slide_cta_motivo": cta_slides.get("_motivo", ""),
        "testi_slide_cta": cta_slides.get("_testi", []),
        "feedback_bot": feedback,
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
                        "testo": {"type": "string", "description": "Il testo scritto sulla slide, parola per parola, con gli a capo. Vuoto se tipo=nessuna."},
                    },
                    "required": ["numero", "tipo", "manuale", "testo"],
                },
            },
        },
        "required": ["slide"],
    },
}


def _find_cta_slides(top_posts, brand: dict) -> dict:
    """Prende l'ultima slide dei caroselli migliori, riconosce quali sono slide CTA, ne legge il testo e salva
    la migliore per ogni tipo (segui / manuale) come immagine di riserva.
    Non sovrascrive le slide CTA caricate a mano."""
    sidecars = [p for p in top_posts if p.kind.lower() == "sidecar"]
    cands = [p for p in sidecars if len(p.image_urls) >= 2][:30]
    if not cands:
        return {"_motivo": f"nei dati letti da Instagram non ci sono caroselli con le slide separate "
                           f"({len(sidecars)} caroselli, nessuno con più di un'immagine)"}
    pairs = []                                   # (post, immagine dell'ultima slide), solo quelle scaricate
    for p in cands:
        try:
            pairs.append((p, instagram.download_images([p.image_urls[-1]])[0]))
        except Exception as e:
            log.warning("Ultima slide non scaricata %s: %s", p.url, e)
    if not pairs:
        return {"_motivo": "non sono riuscito a scaricare le ultime slide dei caroselli"}
    manuali = "; ".join(f"{m['id']}: {m['nome']}" for m in brand.get("manuali", []))
    classified = []                              # (indice in pairs, risultato)
    errors = 0
    for start in range(0, len(pairs), 10):       # a gruppi di 10: risposte corte, niente tagli
        chunk = pairs[start:start + 10]
        content = []
        for k, (_, data) in enumerate(chunk, 1):
            content += [{"type": "text", "text": f"Slide {k}:"}, writer._image_block(data, 800)]
        content.append({"type": "text", "text": "Manuali (id: nome): " + manuali
                        + ". Una slide che promuove il bundle con Potere Sociale va sotto potere-sociale. "
                        "Classifica ogni slide con lo strumento slide_cta e trascrivi il testo delle slide CTA."})
        try:
            out = writer._tool_call("Riconosci le slide di call to action di una pagina Instagram.", content,
                                    CTA_SLIDE_TOOL, max_tokens=6000)
        except Exception as e:
            log.warning("Classificazione slide CTA fallita: %s", e)
            errors += 1
            continue
        for sl in out.get("slide", []):
            n = sl.get("numero")
            if isinstance(n, int) and 1 <= n <= len(chunk):
                classified.append((start + n - 1, sl))
    imgs = [d for _, d in pairs]
    cands = [p for p, _ in pairs]
    out = {"slide": [{**sl, "numero": idx + 1} for idx, sl in classified]}
    if not out["slide"]:
        return {"_motivo": ("la lettura delle slide non è riuscita" if errors else
                            f"tra le ultime slide di {len(pairs)} caroselli nessuna è una CTA")}
    cta_dir = config.DATA_DIR / "cta"
    cta_dir.mkdir(parents=True, exist_ok=True)
    found = {}
    texts = []
    for s in out.get("slide", []):
        t = writer.clean_text(s.get("testo") or "")
        if s.get("tipo") in ("segui", "manuale") and t and t not in {x["testo"] for x in texts}:
            texts.append({"tipo": s["tipo"], "manuale": s.get("manuale") or "", "testo": t})
    found["_testi"] = texts
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


def _norm(t: str) -> str:
    return " ".join((t or "").lower().split())[:400]


def _bot_feedback(published: list) -> dict:
    """Riconosce tra i post pubblicati quelli preparati dal bot (descrizione molto simile) e calcola
    quanto rendono per tipo di hook, natura del post e CTA. Così il bot sa cosa ripetere e cosa evitare."""
    from . import storage
    jobs = storage.all_jobs_light()
    if not jobs:
        return {}
    matched = []
    for post, ratio in published:
        cap = _norm(post.caption)
        if len(cap) < 60:
            continue
        best, best_r = None, 0.0
        for j in jobs:
            r = difflib.SequenceMatcher(None, cap[:250], _norm(j["descrizione"])[:250]).quick_ratio()
            if r > best_r:
                best, best_r = j, r
        if best and best_r >= 0.72 and difflib.SequenceMatcher(None, cap[:250], _norm(best["descrizione"])[:250]).ratio() >= 0.6:
            matched.append((best, ratio))
    if not matched:
        return {"pubblicati_dal_bot": 0}

    def group(key):
        agg = {}
        for j, r in matched:
            k = j.get(key) or "?"
            if key == "cta_tipo" and k == "manuale":
                k = f"manuale {j.get('manuale') or '?'}"
            agg.setdefault(k, []).append(r)
        return {k: {"post": len(v), "rendimento": round(statistics.median(v), 2)}
                for k, v in sorted(agg.items(), key=lambda kv: -statistics.median(kv[1]))}

    return {
        "pubblicati_dal_bot": len(matched),
        "rendimento_medio": round(statistics.median(r for _, r in matched), 2),
        "per_hook": group("tipo_hook"),
        "per_natura": group("natura"),
        "per_cta": group("cta_tipo"),
    }
