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

from . import config, instagram, instagram_api, memoria, writer

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
    # studio delle CTA delle pagine di riferimento: una volta a settimana (costa una lettura in più)
    prev = writer.load_style().get("cta_studio") or {}
    try:
        fresh = prev.get("creato") and (datetime.now(timezone.utc)
                                        - datetime.fromisoformat(prev["creato"])).days < 7 \
            and (prev.get("pagine_config") or []) == sorted(h.lower().lstrip("@") for h in brand.get("pagine_studio") or [])
    except Exception:
        fresh = False
    if fresh:
        style["cta_studio"] = prev
    else:
        try:
            style["cta_studio"] = study_reference_ctas(brand) or prev
        except Exception as e:
            log.warning("Studio CTA delle pagine di riferimento non riuscito: %s", e)
            style["cta_studio"] = prev
    # studio dei caroselli virali (temi, hook, strutture, trend): ogni 3 giorni, base per i post da zero
    pv = writer.load_style().get("studio_virali") or {}
    try:
        fresh_v = pv.get("creato") and (datetime.now(timezone.utc) - datetime.fromisoformat(pv["creato"])).days < 3
    except Exception:
        fresh_v = False
    style["studio_virali_nuovo"] = False
    if fresh_v:
        style["studio_virali"] = pv
    else:
        try:
            nuovo = study_viral(brand)
            style["studio_virali"] = nuovo or pv
            style["studio_virali_nuovo"] = bool(nuovo)
        except Exception as e:
            log.warning("Studio dei caroselli virali non riuscito: %s", e)
            style["studio_virali"] = pv
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


STUDY_TOOL = {
    "name": "studio_cta",
    "description": "Come le pagine di riferimento collegano la CTA finale al post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "post": {"type": "array", "items": {"type": "object", "properties": {
                "numero": {"type": "integer"},
                "ha_cta": {"type": "boolean", "description": "true se l'ultima slide è una CTA (libro, guida, segui, commenta)."},
                "formato": {"type": "string", "description": "elenco, scena/dialogo, riflessione, storia..."},
                "aggancio": {"type": "string", "description": "Come la CTA riprende il post (in parole tue, NON copiare il testo): da quale emozione o frase del post parte, come passa al prodotto."},
                "struttura": {"type": "string", "description": "I passaggi della CTA in ordine, es. 'domanda che riprende il dolore -> ammissione -> presento il libro in una riga -> dove trovarlo'."},
            }, "required": ["numero", "ha_cta", "formato", "aggancio", "struttura"]}},
            "lezioni": {"type": "string", "description": "8-12 regole pratiche (una per riga, iniziano con '- ') su come scrivere una CTA che sembra la naturale continuazione del post: transizione, tono, lunghezza, come si nomina il libro, come si chiude. In parole tue."},
            "schemi_aggancio": {"type": "array", "items": {"type": "string"}, "description": "6-10 schemi di transizione riutilizzabili, scritti in italiano con parole TUE e [segnaposto], es. 'Ora vorrei chiederti… [domanda sul dolore del post]? Per aiutarti ho scritto [libro in una riga]…'."},
        },
        "required": ["post", "lezioni", "schemi_aggancio"],
    },
}


def study_reference_ctas(brand: dict, per_page: int = 40, top: int = 8) -> dict:
    """Studia le CTA finali delle pagine di riferimento (pagine_studio, es. convincimii): penultima e ultima
    slide dei caroselli migliori, per capire COME collegano la CTA al post. Si salvano solo regole e schemi
    scritti con parole nostre, mai i loro testi. Queste pagine non entrano nel report."""
    handles = [h.lower().lstrip("@") for h in brand.get("pagine_studio") or [] if h]
    if not handles:
        return {}
    posts = instagram.fetch_profiles(handles, per_page)
    by_page: dict = {}
    for p in posts:
        if p.kind.lower() == "sidecar" and len(p.image_urls) >= 3 and _engagement(p) is not None:
            by_page.setdefault(p.owner, []).append(p)
    chosen = []
    for h, ps in by_page.items():
        ps.sort(key=lambda x: _engagement(x) or 0, reverse=True)
        chosen += ps[:top]
    if not chosen:
        return {}
    content, n = [], 0
    for p in chosen:
        try:
            imgs = instagram.download_images([p.image_urls[0], p.image_urls[-2], p.image_urls[-1]])
        except Exception as e:
            log.warning("Studio CTA: slide non scaricate %s: %s", p.url, e)
            continue
        n += 1
        content += [{"type": "text", "text": f"Post {n} (@{p.owner}): prima slide, penultima, ultima."}]
        content += [writer._image_block(d, 700) for d in imgs]
        if n >= 14:
            break
    if not n:
        return {}
    content.append({"type": "text", "text": (
        "Sono i caroselli migliori di pagine italiane molto brave a chiudere con una CTA che sembra la naturale "
        "continuazione del post. Per ogni post guarda come l'ultima slide riprende il post e porta al prodotto. "
        "Poi ricava le regole e gli schemi. Non copiare mai le loro frasi: descrivi e riscrivi con parole tue.")})
    out = writer._tool_call("Sei un copywriter che studia le CTA dei migliori caroselli italiani.", content,
                            STUDY_TOOL, model=config.CAPTION_MODEL, max_tokens=8000)
    return {"lezioni": (out.get("lezioni") or "").strip(),
            "schemi_aggancio": [x.strip() for x in out.get("schemi_aggancio") or [] if x.strip()][:10],
            "pagine": sorted(by_page), "pagine_config": sorted(handles), "post_studiati": n,
            "creato": datetime.now(timezone.utc).isoformat(timespec="seconds")}


VIRAL_POST_TOOL = {
    "name": "carosello_studiato",
    "description": "Analisi di un gruppo di caroselli virali.",
    "input_schema": {
        "type": "object",
        "properties": {
            "post": {"type": "array", "items": {"type": "object", "properties": {
                "numero": {"type": "integer"},
                "tema": {"type": "string", "description": "Di cosa parla, in 6-10 parole."},
                "dolore": {"type": "string", "description": "Il dolore o desiderio del lettore che tocca."},
                "tipo_hook": {"type": "string", "description": "Che tipo di gancio usa la prima slide (verità scomoda, domanda, numero/elenco, scena, provocazione, segreto, confronto, profezia...)."},
                "hook": {"type": "string", "description": "Il meccanismo del gancio, riscritto con parole TUE (non copiare)."},
                "formato": {"type": "string", "description": "fumetto/dialogo, testo su fondo pieno, foto con titolo, elenco numerato, storia..."},
                "struttura": {"type": "string", "description": "Ruolo di ogni slide in ordine, es. '1 gancio -> 2 scena -> 3 errore -> 4 meccanismo -> 5 svolta -> 6 frase finale -> 7 CTA'."},
                "slide": {"type": "integer"},
                "perche_funziona": {"type": "string", "description": "In 2-3 frasi CONCRETE e specifiche per questo post: perché è andato virale. Quale leva ha toccato (emozione precisa, identificazione, ego, polemica, condivisibilità: perché qualcuno lo manda a un amico o lo salva), cosa fa il gancio nei primi secondi, cosa tiene il lettore fino all'ultima slide. Per un post DEBOLE: perché non ha funzionato. Niente frasi generiche valide per qualsiasi post."},
                "formato_narrativo": {"type": "string", "enum": [
                    "pov", "prima_persona", "immersivo", "elenco", "dialogo_scena", "verita_scomoda", "storia",
                    "confronto", "domanda", "citazione", "tutorial", "altro"],
                    "description": "Come è raccontato: pov (il lettore è dentro la scena, 'POV: ...'), prima persona ('io ho...'), immersivo (seconda persona al presente, 'Sei lì, lei ti guarda...'), elenco, dialogo/scena, verità scomoda, storia, confronto, domanda, citazione, tutorial."},
                "fattore": {"type": "string", "enum": [
                    "formato", "struttura", "dolore_forte", "tema_caldo", "hook", "grafica", "polemica",
                    "identificazione", "altro"],
                    "description": "Il fattore PRINCIPALE del risultato (o del flop): non sempre è la struttura, spesso è un dolore fortissimo o un tema caldo del momento."},
                "lezione": {"type": "string", "description": "Una frase riutilizzabile per noi: cosa ripetere (o cosa evitare se il post è debole)."},
                "testo_copertina": {"type": "string", "enum": ["enorme", "grande", "medio", "piccolo"],
                    "description": "Quanto è grande il testo del gancio nella prima slide rispetto alla slide (enorme = occupa più di metà slide; piccolo = una riga in un angolo)."},
                "funzione": {"type": "string", "enum": ["portata", "autorevolezza", "vendita", "connessione"],
                    "description": "Il ruolo del post nel profilo: portata (virale, nuovi follower), autorevolezza (insegna, fa vedere che sai), vendita (porta al prodotto), connessione (il lettore si sente compreso, commenta, si identifica)."},
                "coinvolgimento": {"type": "string", "description": "Come porta il lettore DENTRO il post: lo fa sentire compreso? gli fa porre domande? lo lascia arrivare da solo alla soluzione? In 1 frase."},
            }, "required": ["numero", "tema", "dolore", "tipo_hook", "hook", "formato", "struttura", "slide",
                            "perche_funziona", "formato_narrativo", "fattore", "lezione", "testo_copertina",
                            "funzione", "coinvolgimento"]}},
        },
        "required": ["post"],
    },
}

VIRAL_SUMMARY_TOOL = {
    "name": "studio_virali",
    "description": "Cosa rende virali i caroselli della nicchia.",
    "input_schema": {
        "type": "object",
        "properties": {
            "temi": {"type": "array", "items": {"type": "string"}, "description": "I temi e i dolori che rendono di più, dal più forte."},
            "hook": {"type": "array", "items": {"type": "string"}, "description": "8-12 formule di gancio vincenti, scritte con parole nostre e [segnaposto], con il tipo tra parentesi."},
            "strutture": {"type": "array", "items": {"type": "string"}, "description": "5-8 strutture di carosello vincenti: nome, numero di slide, ruolo di ogni slide, quando usarla."},
            "formati": {"type": "string", "description": "Quali formati grafici rendono di più (fumetto, testo su nero, foto...) e per quali temi."},
            "regole": {"type": "string", "description": "8-12 regole pratiche (righe che iniziano con '- ') per scrivere da zero un carosello che diventi virale in questa nicchia."},
        },
        "required": ["temi", "hook", "strutture", "formati", "regole"],
    },
}


def analizza_post(chosen: list, slide_max: int = 10) -> list[dict]:
    """Analizza uno per uno i post (competitor, collaboratori, nostri): perché sono andati (o no), gancio,
    struttura, formato, dolore, fattore principale, lezione per noi. Ogni analisi finisce nella memoria dei
    contenuti. chosen = [(SourcePost, rendimento rispetto alla sua pagina, fonte)]."""
    analyses = []
    for start in range(0, len(chosen), 4):                 # 4 caroselli per chiamata
        content, batch = [], chosen[start:start + 4]
        for k, (p, sc, fonte) in enumerate(batch, 1):
            try:
                imgs = instagram.download_images(p.image_urls[:slide_max])
            except Exception as e:
                log.warning("Analisi post: slide non scaricate %s: %s", p.url, e)
                imgs = []
            esito = "VIRALE" if sc >= 1.5 else "DEBOLE (spiega perché non ha funzionato)"
            cap = " ".join((p.caption or "").split())[:600]
            content.append({"type": "text", "text": f"CAROSELLO {k} (@{p.owner}{' - NOSTRO' if fonte == 'nostro' else ''}, "
                                                    f"{sc:.1f}x la media della pagina, {esito}, "
                                                    f"{len(p.image_urls)} slide, {p.likes or 0} like, "
                                                    f"{p.comments or 0} commenti)"
                                                    + (f"\nDescrizione: {cap}" if cap else "") + ":"})
            content += [writer._image_block(d, 640) for d in imgs]
        content.append({"type": "text", "text": "Analizza ogni carosello con lo strumento carosello_studiato. "
                                                "Spiega il PERCHÉ in modo concreto e specifico per QUEL post (non frasi "
                                                "generiche valide per tutti). Non copiare i testi: descrivi con parole tue."})
        try:
            out = writer._tool_call("Sei uno stratega di contenuti Instagram nella nicchia psicologia maschile e "
                                    "attrazione. Studi perché certi caroselli diventano virali.", content,
                                    VIRAL_POST_TOOL, model=config.ANALYSIS_MODEL, max_tokens=6000)
        except Exception as e:
            log.warning("Analisi post fallita: %s", e)
            continue
        mem = []
        for a in out.get("post", []):
            n = a.get("numero")
            if isinstance(n, int) and 1 <= n <= len(batch):
                p_, sc_, fonte_ = batch[n - 1]
                a["pagina"], a["rendimento"], a["fonte"], a["url"] = p_.owner, round(sc_, 2), fonte_, p_.url
                a.pop("numero", None)
                analyses.append(a)
                mem.append({"url": p_.url, "pagina": p_.owner, "fonte": fonte_, "data_post": p_.timestamp,
                            "rendimento": round(sc_, 2), "formato": a.get("formato_narrativo"),
                            "tema": a.get("tema"), "dolore": a.get("dolore"), "tipo_hook": a.get("tipo_hook"),
                            "struttura": a.get("struttura"), "fattore": a.get("fattore"),
                            "lezione": a.get("lezione"), "grafica": a.get("formato"),
                            "testo_copertina": a.get("testo_copertina"), "funzione": a.get("funzione"),
                            "coinvolgimento": a.get("coinvolgimento")})
        try:
            memoria.add(mem)
        except Exception as e:
            log.warning("Memoria dei contenuti non aggiornata: %s", e)
    return analyses


FATTORI_IT = {"formato": "il formato", "struttura": "la struttura", "dolore_forte": "un dolore fortissimo",
              "tema_caldo": "un tema caldo del momento", "hook": "il gancio", "grafica": "la grafica",
              "polemica": "la polemica", "identificazione": "l'identificazione", "altro": "altro"}


def scheda(a: dict, n: int | None = None) -> str:
    """Il perché di un post, leggibile su Telegram."""
    sc = a.get("rendimento") or 0
    testa = (f"{n}. " if n else "") + f"@{a.get('pagina', '')} · {sc:.1f}x la sua media" + (
        "" if sc >= 1.5 else " · ❌ debole")
    righe = [testa]
    if a.get("url"):
        righe.append(a["url"])
    righe.append(("🧠 Perché è andato: " if sc >= 1.5 else "🧠 Perché non è andato: ") + (a.get("perche_funziona") or "").strip())
    righe.append(f"🎯 Dolore: {a.get('dolore', '')}")
    righe.append(f"🪝 Gancio ({a.get('tipo_hook', '')}): {a.get('hook', '')}")
    righe.append(f"🧱 Struttura: {a.get('struttura', '')}")
    righe.append(f"🎬 Racconto: {str(a.get('formato_narrativo', '')).replace('_', ' ')} · grafica: {a.get('formato', '')}"
                 f" · testo in copertina {a.get('testo_copertina', '')}")
    if a.get("coinvolgimento"):
        righe.append(f"🫵 Come ti porta dentro: {a['coinvolgimento']}")
    righe.append(f"⚡ Fattore principale: {FATTORI_IT.get(a.get('fattore'), a.get('fattore', ''))}")
    if a.get("lezione"):
        righe.append(f"✅ Per noi: {a['lezione']}")
    return "\n".join(r for r in righe if r.strip())


def perche_breve(a: dict) -> str:
    """Versione corta per il report del mattino: perché è andato, gancio, struttura, fattore, cosa ci serve."""
    righe = [f"🧠 Perché è andato: {(a.get('perche_funziona') or '').strip()}",
             f"🪝 Gancio ({a.get('tipo_hook', '')}): {a.get('hook', '')}",
             f"🎬 Racconto: {str(a.get('formato_narrativo', '')).replace('_', ' ')} · copertina {a.get('testo_copertina', '')}",
             f"🧱 Struttura: {a.get('struttura', '')}",
             f"⚡ Fattore principale: {FATTORI_IT.get(a.get('fattore'), a.get('fattore', ''))}"]
    if a.get("lezione"):
        righe.append(f"✅ Per noi: {a['lezione']}")
    return "\n".join(righe)


def schede_studio(sv: dict) -> list[str]:
    """Tutte le schede dell'ultimo studio, divise per competitor, collaboratori e nostri post."""
    cars = sv.get("caroselli") or []
    out = []
    for fonte, titolo in (("competitor", "🔎 COMPETITOR: perché i loro post sono andati"),
                          ("collaboratore", "🤝 COLLABORATORI: perché i loro post sono andati"),
                          ("nostro", "🏠 I NOSTRI POST: cosa ha funzionato e cosa no")):
        gruppo = sorted([a for a in cars if a.get("fonte") == fonte], key=lambda a: -(a.get("rendimento") or 0))
        if gruppo:
            out.append(titolo)
            out += [scheda(a, i) for i, a in enumerate(gruppo, 1)]
    return out


def study_viral(brand: dict, per_page: int = 40, per_handle: int = 6, max_posts: int = 16) -> dict:
    """Studio dei caroselli VIRALI delle pagine di riferimento e dei collaboratori: temi, dolori, hook,
    strutture e formati che rendono di più. È la base per creare caroselli da zero (mai copiare: si salvano
    solo schemi e regole con parole nostre). Virale = almeno 2 volte la mediana della sua pagina."""
    handles = list(dict.fromkeys(
        [h.lower().lstrip("@") for h in brand.get("pagine_studio") or [] if h]
        + [h.lower().lstrip("@") for h in brand.get("pagine_monitorate") or [] if h]))
    own = (brand.get("handle") or "").lower()
    handles = [h for h in handles if h != own]
    if not handles:
        return {}
    posts = instagram.fetch_profiles(handles, per_page)
    by_page: dict = {}
    for p in posts:
        if _engagement(p) is not None:
            by_page.setdefault(p.owner, []).append(p)
    chosen = []
    study_first = {h.lower().lstrip("@") for h in brand.get("pagine_studio") or []}
    for h, ps in sorted(by_page.items(), key=lambda kv: kv[0] not in study_first):
        med = statistics.median([_engagement(x) for x in ps]) or 1
        vir = [x for x in ps if x.kind.lower() == "sidecar" and len(x.image_urls) >= 3
               and (_engagement(x) or 0) >= 2 * med]
        vir.sort(key=lambda x: (_engagement(x) or 0) / med, reverse=True)
        chosen += [(x, (_engagement(x) or 0) / med) for x in vir[:per_handle]]
    chosen = [(x, sc, "competitor" if x.owner in study_first else "collaboratore") for x, sc in chosen[:max_posts]]
    # i NOSTRI post: i migliori (perché hanno funzionato) e i più deboli (perché no)
    try:
        mine = [p for p in instagram.fetch_profiles([own], per_page) if _engagement(p) is not None
                and p.kind.lower() == "sidecar" and len(p.image_urls) >= 3] if own else []
    except Exception as e:
        log.warning("Studio virali: post nostri non letti: %s", e)
        mine = []
    if len(mine) >= 6:
        med = statistics.median([_engagement(x) for x in mine]) or 1
        ranked = sorted(mine, key=lambda x: (_engagement(x) or 0) / med, reverse=True)
        best = [x for x in ranked if (_engagement(x) or 0) >= 1.5 * med][:4]
        worst = [x for x in reversed(ranked) if (_engagement(x) or 0) <= 0.6 * med][:3]
        chosen += [(x, (_engagement(x) or 0) / med, "nostro") for x in best + worst]
    if not chosen:
        return {}
    analyses = analizza_post(chosen)
    if not analyses:
        return {}
    guida = (writer.load_style().get("guida") or "").strip()
    manuali = "; ".join(f"{m.get('nome')}: {' '.join(str(m.get('dolori', '')).split())[:200]}"
                        for m in brand.get("manuali", []))
    system = (
        f"Sei lo stratega dei contenuti di {brand['nome']} (@{brand['handle']}). Dai caroselli virali di altre "
        "pagine ricavi cosa funziona davvero, ma TUTTO quello che restituisci deve essere già adattato alla "
        f"NOSTRA pagina: posizionamento, tono e dolori qui sotto.\n"
        f"POSIZIONAMENTO: {' '.join(str(brand.get('posizionamento', '')).split())}\n"
        f"TONO: {' '.join(brand.get('tono', []))}\n"
        f"I NOSTRI MANUALI E I DOLORI CHE RISOLVONO: {manuali}\n"
        + (f"COME SCRIVIAMO NOI (dai nostri post migliori):\n{guida[:2000]}\n" if guida else "")
        + "Scarta temi e formule che non c'entrano con noi (romantici, motivazionali generici, fuori nicchia) e "
        "riscrivi hook e strutture con la nostra voce: maschile, diretta, cruda, mai romantica. Privilegia i temi "
        "che portano ai nostri manuali.")
    summ = writer._tool_call(
        system, [{"type": "text", "text": json.dumps([a for a in analyses if (a.get("rendimento") or 0) >= 1.5],
                                                     ensure_ascii=False, indent=1)}],
        VIRAL_SUMMARY_TOOL, model=config.CAPTION_MODEL, max_tokens=6000)
    return {**{k: summ.get(k) for k in ("temi", "hook", "strutture", "formati", "regole")},
            "caroselli": analyses, "pagine": sorted(by_page), "pagine_config": sorted(handles),
            "creato": datetime.now(timezone.utc).isoformat(timespec="seconds")}


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
        # caroselli creati da zero: quali colori e quali caratteri rendono di più
        "per_palette": {k: v for k, v in group("palette").items() if k != "?"},
        "per_stile_grafico": {k: v for k, v in group("stile_grafico").items() if k != "?"},
        "per_formato_narrativo": {k: v for k, v in group("formato_narrativo").items() if k != "?"},
    }
