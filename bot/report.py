"""Report mattutino.

1. Legge gli ultimi post delle pagine monitorate (un'unica esecuzione Apify) e salva i loro numeri:
   giorno dopo giorno si costruisce la media storica di ogni pagina.
2. Media di una pagina = mediana di (like + 3 x commenti) dei suoi post degli ultimi 90 giorni,
   esclusi quelli degli ultimi 3 giorni che stanno ancora raccogliendo interazioni.
   Finché lo storico è corto (meno di 8 post) usa i post appena letti.
3. Punteggio di un post = quante volte ha reso rispetto alla media della SUA pagina.
4. Tiene i post delle ultime N ore non ancora lavorati e ne mette in classifica i migliori (candidati);
   la classifica viene salvata per /altri.
5. Claude sceglie tra i candidati quelli più adatti ad AttraiSeduci, preferendo quelli sopra la media.
6. Il bot prepara i post scelti (slide, descrizione, CTA) e li manda a te e al socio.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from datetime import datetime, timedelta, timezone

from . import config, instagram, pipeline, storage, writer

log = logging.getLogger(__name__)
_locks: dict[str, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    """Un report alla volta per pagina (pagine diverse possono lavorare insieme)."""
    from . import pagine
    return _locks.setdefault(pagine.corrente(), asyncio.Lock())

SELECT_TOOL = {
    "name": "seleziona_post",
    "description": "Sceglie i post da preparare per AttraiSeduci.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scelti": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "numero": {"type": "integer", "description": "Numero del candidato nell'elenco."},
                        "motivo": {"type": "string", "description": "Perché, in una frase: dolore toccato, meccanismo, adattabilità al tono."},
                    },
                    "required": ["numero", "motivo"],
                },
            },
        },
        "required": ["scelti"],
    },
}


def _engagement(p: instagram.SourcePost) -> float | None:
    likes = p.likes if (p.likes is not None and p.likes >= 0) else None
    comments = p.comments if (p.comments is not None and p.comments >= 0) else None
    if likes is None and comments is None:
        return None
    return (likes or 0) + 3 * (comments or 0)   # un commento vale più di un like


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _quota_eta(ore: float) -> float:
    """Che parte delle interazioni finali un post ha già raccolto dopo `ore` ore (curva tipica di Instagram:
    circa 3/4 nel primo giorno, quasi tutto entro il secondo). Serve a confrontare post di età diverse."""
    import math
    return max(0.15, 1 - math.exp(-max(ore, 0.5) / 18))


def _eta_ore(p) -> float | None:
    ts = _parse_ts(p.timestamp)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600 if ts else None


def _normalizzato(e: float, ore: float | None) -> float:
    return e / _quota_eta(ore if ore is not None else 72)


def _baselines(posts: list[instagram.SourcePost]) -> dict[str, float]:
    """Rendimento tipico di ogni pagina, a parità di età: si confrontano i post di oggi con i post della stessa
    pagina letti quando avevano 0-3 giorni (non con post vecchi che hanno già raccolto tutto)."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=60)).isoformat()
    fetched: dict[str, list[float]] = {}
    for p in posts:
        e = _engagement(p)
        if e is not None and not p.is_video:
            fetched.setdefault(p.owner, []).append(_normalizzato(e, _eta_ore(p)))
    out = {}
    for owner, values in fetched.items():
        storici = [_normalizzato(e, ore) for e, ore in storage.page_engagements_giovani(owner, since)]
        base = storici if len(storici) >= 6 else values
        out[owner] = max(statistics.median(base), 1.0)
    return out


def rank_candidates(posts: list[instagram.SourcePost], hours: int, limit: int) -> list[instagram.SourcePost]:
    """Salva i numeri per la media storica e restituisce i post recenti in ordine di punteggio."""
    rows = []
    for p in posts:
        e = _engagement(p)
        ts = _parse_ts(p.timestamp)
        if e is not None and ts and not p.is_video:
            rows.append((p.url, p.owner, e, ts.astimezone(timezone.utc).isoformat()))
    storage.save_post_stats(rows)
    baseline = _baselines(posts)

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []
    for p in posts:
        ts = _parse_ts(p.timestamp)
        e = _engagement(p)
        if p.is_video or not p.image_urls or e is None or not ts or ts < since:
            continue
        if (_eta_ore(p) or 0) < 6:      # troppo giovane per giudicarlo: lo si rilegge domani (finestra di 2 giorni)
            continue
        p.score = _normalizzato(e, _eta_ore(p)) / baseline.get(p.owner, max(e, 1.0))   # a parità di età
        recent.append(p)
    done = storage.processed_urls([p.url for p in recent])
    recent = [p for p in recent if p.url not in done]
    recent.sort(key=lambda p: p.score, reverse=True)
    return recent[:limit]


def _select_sync(cands: list[instagram.SourcePost], brand: dict, quanti: int,
                 min_score: float = 1.0) -> list[instagram.SourcePost]:
    if len(cands) <= quanti:
        for p in cands:
            p.motivo = p.motivo or "Tra i migliori per rendimento."
        return cands
    elenco = "\n\n".join(
        f"{i}. @{p.owner} | {p.score:.1f}x la media della pagina | like {p.likes} | commenti {p.comments}\n"
        f"Descrizione: {(p.caption or '')[:500]}"
        for i, p in enumerate(cands, 1)
    )
    system = (
        f"Sei il responsabile contenuti di {brand['nome']}. Posizionamento: {brand.get('posizionamento', '').strip()}\n"
        f"Tono: {' '.join(brand.get('tono', []))}\n\n"
        f"Scegli esattamente {quanti} post da adattare in italiano. Criteri, in ordine: rendimento rispetto alla media "
        f"della pagina (preferisci sempre quelli da {min_score:.1f}x in su, gli altri solo se mancano), forza del dolore "
        "toccato, adattabilità al nostro tono, varietà tra i post scelti (niente due post sullo stesso tema)."
    )
    try:
        raw = writer._tool_call(system, [{"type": "text", "text": f"Candidati:\n\n{elenco}"}],
                                SELECT_TOOL, max_tokens=2000).get("scelti", [])
    except Exception as e:
        log.warning("Selezione con Claude fallita, uso solo il punteggio: %s", e)
        raw = []
    chosen, seen = [], set()
    for s in raw:
        n = s.get("numero")
        if isinstance(n, int) and 1 <= n <= len(cands) and n not in seen:
            seen.add(n)
            cands[n - 1].motivo = s.get("motivo", "")
            chosen.append(cands[n - 1])
    for p in cands:                       # se Claude ne ha scelti meno, completa con i migliori per punteggio
        if len(chosen) >= quanti:
            break
        if p not in chosen:
            p.motivo = p.motivo or "Tra i migliori per rendimento."
            chosen.append(p)
    return chosen[:quanti]


async def _broadcast(bot, chat_ids: list[int], text: str):
    for c in chat_ids:
        try:
            await bot.send_message(c, text)
        except Exception as e:
            log.warning("Invio a %s fallito: %s", c, e)


def _to_dict(p: instagram.SourcePost) -> dict:
    return {"url": p.url, "owner": p.owner, "caption": p.caption, "kind": p.kind, "image_urls": p.image_urls,
            "likes": p.likes, "comments": p.comments, "timestamp": p.timestamp, "score": p.score,
            "motivo": p.motivo, "natura": p.natura, "pagina": getattr(p, "pagina", "")}


async def _produce(bot, chat_ids: list[int], brand: dict, chosen: list[instagram.SourcePost],
                   min_score: float, label: str) -> None:
    sem = asyncio.Semaphore(2)

    async def one(i: int, src: instagram.SourcePost):
        async with sem:
            try:
                post, paths, failed = await pipeline.build_post(src, brand, f"{label}{i}")
                job_id = storage.save_job(0, 0, src.url, src.owner, post, paths)
                pipeline.after_save(job_id, src.url, post, paths, failed)
                sotto = "" if src.score >= min_score else " (sotto media, scelto per completare)"
                header = (f"{label.capitalize()} {i}/{len(chosen)} · da @{src.owner} · {src.score:.1f}x la media{sotto}\n"
                          f"Perché: {src.motivo}")
                for c in chat_ids:
                    await pipeline.send_post(bot, c, job_id, post, paths, failed, header=header)
            except Exception:
                log.exception("Report: post %s fallito", src.url)
                await _broadcast(bot, chat_ids, f"{label.capitalize()} {i}: non sono riuscito a preparare {src.url}")

    await asyncio.gather(*(one(i, p) for i, p in enumerate(chosen, 1)))


def _post_per_pagina(massimo: int = 4) -> int:
    """Quanti post leggere per pagina ogni mattina restando nel tetto mensile di Apify: il budget del giorno diviso
    le pagine collaboratrici di TUTTE le nostre pagine (quelle in comune contano una volta). Meno pagine = più post."""
    import yaml
    from . import pagine
    uniche: set = set()
    cur = pagine.corrente()
    try:
        for slug in pagine.elenco():
            pagine.imposta(slug)
            b_ = writer.load_brand()
            if not b_.get("in_pausa"):
                uniche |= {h.lower() for h in b_.get("pagine_monitorate") or []}
    finally:
        pagine.imposta(cur)
    al_giorno = instagram.tetto_mese() / 31
    return max(1, min(massimo, int(al_giorno // max(1, len(uniche)))))


def _settings(brand: dict) -> dict:
    cfg = brand.get("report") or {}
    quanti = int(cfg.get("quanti_post", 5))
    return {
        "quanti": quanti,
        "candidati": max(quanti, int(cfg.get("candidati", 15))),
        "ore": int(cfg.get("finestra_ore", 48)),
        "per_pagina": _post_per_pagina(int(cfg.get("post_per_pagina", 4))),   # dentro il tetto mensile di Apify
        "min_score": float(cfg.get("punteggio_minimo", 1.0)),
        "completa": bool(cfg.get("completa_sempre", True)),
        # False = il report manda solo i link dei migliori post (nessun credito speso per tradurli)
        "prepara": bool(cfg.get("prepara_post", False)),
        "link": max(1, int(cfg.get("post_nel_report", 7))),   # quanti link nel report (metà didattici, metà virali)
        "perche": int(cfg.get("perche_quanti", 0)),          # 0 = il perché non è nel report (arriva una volta a settimana)
    }


FIT_TOOL = {
    "name": "post_in_tema",
    "description": "Quali post sono adatti a QUESTA pagina (pubblico, dolori, tono).",
    "input_schema": {
        "type": "object",
        "properties": {
            "post": {"type": "array", "items": {"type": "object", "properties": {
                "numero": {"type": "integer"},
                "dolore": {"type": "string", "description": "PRIMA di decidere: il dolore o il tema che il post tocca davvero e a chi parla (uomo, donna, entrambi), in poche parole."},
                "adatto": {"type": "boolean", "description": "true solo se il post parla al PUBBLICO di questa pagina e tocca uno dei SUOI dolori/temi, così che i suoi follower lo sentano scritto per loro."},
                "pagina_migliore": {"type": "string", "description": "Tra QUESTA pagina e le nostre altre pagine, il NOME di quella a cui il post è più adatto (pubblico e dolore), oppure 'nessuna'."},
                "natura": {"type": "string", "enum": ["didattico", "virale"],
                           "description": "didattico = INSEGNA qualcosa di concreto: spiega un meccanismo, un trucco, cosa fare o non fare, segnali da riconoscere, errori con la soluzione. virale = punta sull'EMOZIONE più che sull'insegnamento: provoca, polarizza, tocca ego o orgoglio, fa sentire rabbia, rivalsa, riconoscimento o nostalgia (frasi d'impatto, verità scomode, scene, confronti, citazioni). Nel dubbio: se non c'è un insegnamento pratico, è virale."},
                "motivo": {"type": "string", "description": "Perché va (o non va) su questa pagina, massimo 14 parole."},
            }, "required": ["numero", "dolore", "adatto", "pagina_migliore", "natura", "motivo"]}},
        },
        "required": ["post"],
    },
}


def pagine_nostre(brand: dict) -> list[dict]:
    """Tutte le nostre pagine (la principale per prima), dalle loro schede: nome, pubblico e temi.
    Servono a capire a quale pagina va ogni post."""
    import yaml
    from . import pagine
    out = []
    for slug in pagine.elenco():
        try:
            b = yaml.safe_load(pagine.brand_file(slug).read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if b.get("nome"):
            out.append({"nome": b["nome"], "handle": b.get("handle", ""), "pubblico": b.get("pubblico", ""),
                        "temi": b.get("temi_report") or b.get("posizionamento", "")})
    return out or [{"nome": brand["nome"], "handle": brand.get("handle", ""), "pubblico": "",
                    "temi": brand.get("posizionamento", "")}]


def _nome_pagina(val, pagine: list[dict]) -> str:
    v = " ".join(str(val or "").lower().replace("@", "").split())
    if not v or v.startswith("nessun"):
        return ""
    for pg in pagine:
        nomi = {pg["nome"].lower(), str(pg.get("handle") or "").lower()} - {""}
        if v in nomi or any(x.replace(" ", "") == v.replace(" ", "") for x in nomi):
            return pg["nome"]
    for pg in pagine:
        if pg["nome"].lower() in v or v in pg["nome"].lower():
            return pg["nome"]
    return ""


def _fit_filter(cands: list, brand: dict) -> tuple[list, bool]:
    """Tiene solo i post adatti a QUESTA pagina: stesso pubblico, uno dei suoi dolori, il suo taglio.
    Lo stesso collaboratore può stare in più pagine: ogni pagina sceglie tra i suoi post quelli giusti per sé, e ogni
    post va a una sola pagina (quella a cui è più adatto). Guarda la descrizione e la copertina (molti post hanno poco testo sotto).
    Claude Haiku: costa pochissimo. Restituisce (post adatti, filtro riuscito)."""
    if not cands:
        return cands, True
    altre = [pg for pg in pagine_nostre(brand) if pg["nome"] != brand["nome"]]
    profilo = (f"PAGINA: {brand['nome']} (@{brand.get('handle', '')})\n"
               f"PUBBLICO: {brand.get('pubblico') or '-'}\n"
               f"DI COSA PARLA: {' '.join(str(brand.get('temi_report') or '').split())}\n"
               f"POSIZIONAMENTO: {' '.join(str(brand.get('posizionamento') or '').split())}\n"
               f"TONO: {' '.join(brand.get('tono') or [])}")
    contrasto = "\n".join(f"- {pg['nome']} (pubblico: {pg.get('pubblico') or '-'}): {' '.join(str(pg.get('temi', '')).split())[:300]}"
                          for pg in altre)
    system = ("Sei il responsabile contenuti di una pagina Instagram italiana. Scegli quali post dei collaboratori "
              "vanno bene per QUESTA pagina, e solo per lei.\n\n" + profilo + "\n\n"
              + (("LE NOSTRE ALTRE PAGINE (per capire i confini: lo stesso collaboratore pubblica post diversi, alcuni "
                  "adatti a noi e altri a un'altra nostra pagina; ogni post va SOLO alla pagina a cui è più adatto):\n"
                  + contrasto + "\n\n")
                 if contrasto else "")
              + "Regole: conta a CHI parla il post e QUALE dolore tocca. Se il pubblico non è quello della pagina "
              "(es. un post che parla alle donne per una pagina di uomini, o viceversa) non è adatto. Se il tema è "
              "fuori dai temi della pagina non è adatto, anche se il post è virale. Mai adatti: meme senza contenuto, "
              "sport, auto, pubblicità, notizie, giveaway, motivazionali generici non legati ai temi della pagina.\n"
              "Per ogni post dì anche se è DIDATTICO o VIRALE.")
    keep, ok = [], True
    for start in range(0, len(cands), 12):
        chunk = cands[start:start + 12]
        content = []
        for i, p in enumerate(chunk, 1):
            content.append({"type": "text", "text": f"{i}. @{p.owner}: "
                                                    f"{' '.join((p.caption or '(nessuna descrizione)').split())[:400]}"})
            if p.image_urls:
                try:
                    img = instagram.download_images(p.image_urls[:1])[0]
                    content += [{"type": "text", "text": f"Copertina del post {i}:"}, writer._image_block(img, 512)]
                except Exception:
                    pass
        content.append({"type": "text", "text": "Valuta ogni post con lo strumento post_in_tema."})
        try:
            out = writer._tool_call(system, content, FIT_TOOL, model=config.CHECK_MODEL, max_tokens=4000)
        except Exception as e:
            log.warning("Filtro di tema non riuscito: %s", e)
            keep += chunk
            ok = False
            continue
        for r in out.get("post", []):
            n = r.get("numero")
            # ogni post va a UNA sola pagina: quella a cui è più adatto (niente doppioni tra le nostre pagine)
            migliore = _nome_pagina(r.get("pagina_migliore"), pagine_nostre(brand)) or brand["nome"]
            if isinstance(n, int) and 1 <= n <= len(chunk) and r.get("adatto") and migliore == brand["nome"]:
                p = chunk[n - 1]
                p.pagina = brand["nome"]
                prima = p.motivo if (p.motivo or "").startswith("📈") else ""
                p.motivo = (prima + "\n" if prima else "") + r.get("motivo", "")
                p.natura = r.get("natura", "")
                keep.append(p)
    keep.sort(key=lambda p: -p.score)
    return keep, ok


def _shown_file():
    return config.DATA_DIR / "report_gia_mostrati.json"


def _already_shown() -> dict:
    """Link già mandati in un report precedente, con il rendimento che avevano allora: {url: [quando, punteggio]}."""
    import json
    try:
        d = json.loads(_shown_file().read_text())
    except Exception:
        return {}
    limit = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    out = {}
    for u, v in d.items():
        v = v if isinstance(v, list) else [v, 0.0]
        if v[0] >= limit:
            out[u] = v
    return out


def _mark_shown(posts: list) -> None:
    import json
    d = _already_shown()
    now = datetime.now(timezone.utc).isoformat()
    d.update({p.url: [now, float(p.score or 0)] for p in posts})
    try:
        _shown_file().write_text(json.dumps(d))
    except OSError:
        pass


def _ripescabile(p, shown: dict) -> bool:
    """Un post già mandato torna solo se nel frattempo è esploso (rendimento almeno raddoppiato)."""
    old = shown.get(p.url)
    if not old:
        return True
    if old[1] and p.score >= 2 * old[1]:
        p.motivo = f"📈 in forte crescita: da {old[1]:.1f}x a {p.score:.1f}x"
        return True
    return False


def _offset_file():
    return config.DATA_DIR / "classifica_mostrati.txt"


def _perche(items: list) -> dict:
    """Per ogni post del report: perché è andato (gancio, struttura, fattore, lezione per noi).
    Le analisi finiscono anche nella memoria dei contenuti, così il bot impara ogni giorno."""
    from . import analysis
    da_fare = [(p, float(p.score or 0), "collaboratore") for p in items if p.image_urls and not p.is_video]
    if not da_fare:
        return {}
    try:
        return {a["url"]: analysis.perche_breve(a) for a in analysis.analizza_post(da_fare, slide_max=6) if a.get("url")}
    except Exception as e:
        log.warning("Report: analisi del perché non riuscita: %s", e)
        return {}


def _bilancia(cands: list, n: int) -> list:
    """I migliori n post, metà didattici e metà virali (se di un tipo ce ne sono pochi, si completa con l'altro)."""
    did = [p for p in cands if p.natura == "didattico"]
    vir = [p for p in cands if p.natura != "didattico"]
    k_did = min(len(did), (n + 1) // 2)
    k_vir = min(len(vir), n - k_did)
    k_did = min(len(did), n - k_vir)
    scelti = did[:k_did] + vir[:k_vir]
    return sorted(scelti, key=lambda p: (p.natura != "didattico", -p.score))


def _sezioni(items: list, min_score: float, perche: dict | None = None, start: int = 1) -> list[str]:
    """Il report diviso in due blocchi: prima i didattici, poi i virali."""
    manuali = bool(writer.load_brand().get("manuali"))
    did = [p for p in items if p.natura == "didattico"]
    vir = [p for p in items if p.natura != "didattico"]
    out = []
    if did:
        out.append("📚 DIDATTICI" + (" · CTA al manuale" if manuali else "") + f" ({len(did)})")
        out += _link_lines(did, start, min_score, perche, tipo=False)
    if vir:
        out.append(f"🔥 VIRALI · per far crescere la pagina ({len(vir)})")
        out += _link_lines(vir, start + len(did), min_score, perche, tipo=False)
    return out


def _link_lines(items: list, start: int, min_score: float, perche: dict | None = None, tipo: bool = True) -> list[str]:
    perche = perche or {}
    out = []
    for i, p in enumerate(items, start):
        anteprima = " ".join((p.caption or "").split())[:70]
        nums = f"❤️ {p.likes or 0} · 💬 {p.comments or 0}"
        principale = bool(writer.load_brand().get("manuali"))   # CTA al manuale solo se la pagina ha manuali
        tipo_txt = ({"didattico": "📚 DIDATTICO (CTA manuale)", "virale": "🔥 VIRALE (segui la pagina)"} if principale
                else {"didattico": "📚 didattico", "virale": "🔥 virale"}).get(p.natura, "")
        testa = tipo_txt if tipo else ""
        out.append(f"{i}. " + (f"{testa}\n" if testa else "") + f"@{p.owner} · {p.score:.1f}x la sua media{'' if p.score >= min_score else ' (sotto media)'}"
                   f" · {nums}\n{p.url}" + (f"\n{p.motivo}" if p.motivo else (f"\n“{anteprima}…”" if anteprima else ""))
                   + (f"\n{perche[p.url]}" if perche.get(p.url) else ""))
    return out


async def _send_links(bot, chat_ids: list[int], header: str, lines: list[str], footer: str) -> None:
    """Messaggi di soli link, spezzati sotto il limite di Telegram, senza anteprime."""
    chunks, cur = [], header
    for ln in lines:
        if len(cur) + len(ln) + 2 > 3800:
            chunks.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + ln
    for ln in [footer]:                       # anche il riepilogo in fondo rispetta il limite di Telegram
        for pezzo in [ln[k:k + 3500] for k in range(0, len(ln), 3500)] or [""]:
            if len(cur) + len(pezzo) + 2 > 3800:
                chunks.append(cur)
                cur = ""
            cur += ("\n\n" if cur else "") + pezzo
    chunks.append(cur)
    for c in chat_ids:
        for text in chunks:
            try:
                await bot.send_message(c, text, disable_web_page_preview=True)
            except Exception as e:
                log.warning("Invio a %s fallito: %s", c, e)


def _pool(cands: list[instagram.SourcePost], st: dict) -> list[instagram.SourcePost]:
    """Se completa_sempre è spento, si lavora solo sui post sopra la soglia."""
    return cands if st["completa"] else [p for p in cands if p.score >= st["min_score"]]


def _page_summary(posts: list, handles: list[str], hours: int, min_score: float, own: set) -> str:
    """Una riga per pagina: quanti post letti, quanti recenti con immagini, il migliore rispetto alla sua media.
    Così si vede che ogni pagina è stata letta, anche quando nessun suo post entra nel report."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    righe = []
    for h in handles:
        if h in own:
            continue
        mine = [p for p in posts if p.owner == h]
        if not mine:
            righe.append(f"@{h}: non letta")
            continue
        rec = [p for p in mine if (_parse_ts(p.timestamp) or since) >= since]
        img = [p for p in rec if not p.is_video and p.image_urls]
        best = max((p.score or 0 for p in img), default=0)
        txt = f"@{h}: {len(mine)} post letti, {len(rec)} nelle ultime {hours} ore"
        if rec and not img:
            txt += " (solo video/reel)"
        elif img:
            txt += f", il migliore {best:.1f}x" + (" ✅" if best >= min_score else "")
        righe.append(txt)
    return "Pagine controllate:\n" + "\n".join(righe)


async def run_report(bot, chat_ids: list[int]) -> None:
    _running = _lock()
    if _running.locked():
        await _broadcast(bot, chat_ids, "Il report è già in preparazione, arriva tra poco.")
        return
    async with _running:
        brand = writer.load_brand()
        handles = [h.lower().lstrip("@") for h in brand.get("pagine_monitorate") or [] if h]
        st = _settings(brand)
        if not handles:
            await _broadcast(bot, chat_ids, "Report: nessuna pagina in pagine_monitorate.")
            return

        await _broadcast(bot, chat_ids, f"Report: leggo le pagine monitorate ({len(handles)}), ultime {st['ore']} ore…")
        try:
            posts = await asyncio.to_thread(instagram.fetch_profiles_giorno, handles, st["per_pagina"], 2)
        except Exception as e:
            log.exception("Report: lettura pagine fallita")
            if "Tetto mensile" in str(e):
                await _broadcast(bot, chat_ids, f"Report: {e}")
            elif "limit" in str(e).lower() and ("usage" in str(e).lower() or "monthly" in str(e).lower()):
                await _broadcast(bot, chat_ids, "Report: Apify ha finito il credito del mese, per questo non legge le "
                                                "pagine. Su apify.com → Billing → Limits alza il limite mensile (o "
                                                "passa al piano a pagamento), poi riprova con /report.")
            else:
                await _broadcast(bot, chat_ids, "Report: non sono riuscito a leggere le pagine "
                                                f"({str(e)[:150]}). Riprova con /report.")
            return

        letti = {p.owner for p in posts}
        mancanti = [h for h in handles if h not in letti]
        if mancanti:
            await _broadcast(bot, chat_ids, "Non riesco a leggere: " + ", ".join(f"@{h}" for h in mancanti)
                             + ". Di solito è perché la pagina ha un limite d'età"
                             + (" e anche la lettura con accesso non è riuscita: controlla che INSTAGRAM_COOKIES sia ancora valido."
                                if config.INSTAGRAM_COOKIES else
                                ": inserisci INSTAGRAM_COOKIES su Render (vedi la guida)."))
        own = {(brand.get("handle") or "").lower()}
        posts = [p for p in posts if p.owner not in own]      # i nostri post non servono nel report
        if not posts:
            await _broadcast(bot, chat_ids, "Report: in pagine_monitorate c'è solo la nostra pagina. "
                                            "Aggiungi le pagine dei collaboratori da cui prendere i post.")
            return
        if not st["prepara"]:
            # solo link: classifica lunga, tenuti solo i post in tema; /altri mostra i successivi
            # solo post andati DAVVERO meglio della media della loro pagina, e mai quelli già mandati
            # (un post partito piano che il giorno dopo esplode compare appena supera la soglia; uno già
            # mandato torna solo se nel frattempo ha almeno raddoppiato).
            # Se negli ultimi giorni ne sono andati forte pochi, si guarda più indietro (fino a 7 giorni)
            # invece di mandare post nella media.
            shown_before = _already_shown()
            ore, filtered = st["ore"], True
            windows = sorted({st["ore"], max(st["ore"], 120), max(st["ore"], 168)})
            for ore in windows:
                cands = rank_candidates(posts, ore, st["candidati"] * 4)
                cands = [p for p in cands if _ripescabile(p, shown_before)
                         and (st["completa"] or p.score >= st["min_score"])]
                if cands:
                    cands, filtered = await asyncio.to_thread(_fit_filter, cands, brand)
                if len(cands) >= st["link"]:
                    break
            riepilogo = _page_summary(posts, handles, ore, st["min_score"], own)
            if not cands:
                await _broadcast(bot, chat_ids, f"Report: negli ultimi {ore // 24} giorni nessun post nuovo dei "
                                                f"collaboratori ha superato {st['min_score']:.1f}x la media della "
                                                "sua pagina. Niente da segnalare oggi.\n\n" + riepilogo)
                return
            _salva_settimana(cands)                                     # il perché della domenica li riusa: zero Apify
            first = _bilancia(cands, st["link"])
            cands = first + [p for p in cands if p not in first]      # /altri riparte da dove finisce il report
            storage.save_ranking([_to_dict(p) for p in cands])
            _offset_file().write_text(str(len(first)))
            _mark_shown(first)
            perche = {}
            if st["perche"] > 0:
                await _broadcast(bot, chat_ids, f"Report: ho trovato {len(first)} post forti, ora studio perché sono andati…")
                perche = await asyncio.to_thread(_perche, first[:st["perche"]])   # solo i migliori: costa immagini
            await _send_links(
                bot, chat_ids,
                f"☀️ {brand['nome']} · post dei collaboratori andati almeno {st['min_score']:.1f}x sopra la media della loro pagina "
                f"(ultimi {ore // 24} giorni, mai mandati prima)"
                + (" · oggi pochi post forti, ho guardato anche i giorni prima" if ore > st["ore"] else "") + ".\n"
                f"I {len(first)} migliori, metà didattici e metà virali:"
                + ("" if filtered else "\n(filtro di tema non riuscito: li vedi tutti)"),
                _sezioni(first, st["min_score"], perche),
                riepilogo + "\n\nMandami il link di quelli che ti piacciono e li preparo in italiano."
                + (f"\nScrivi /altri per vederne altri {min(st['link'], len(cands) - len(first))}." if len(cands) > len(first) else ""))
            return
        cands = rank_candidates(posts, st["ore"], st["candidati"])
        storage.save_ranking([_to_dict(p) for p in cands])
        pool = _pool(cands, st)
        if not pool:
            await _broadcast(bot, chat_ids, f"Report: nessun post nuovo con immagini nelle ultime {st['ore']} ore.")
            return
        chosen = await asyncio.to_thread(_select_sync, pool, brand, st["quanti"], st["min_score"])

        righe = [f"{'✅' if p in chosen else '▫️'} {i}. @{p.owner} · {p.score:.1f}x la media"
                 f"{'' if p.score >= st['min_score'] else ' (sotto media)'} · {p.url}"
                 for i, p in enumerate(cands, 1)]
        await _broadcast(bot, chat_ids,
                         f"Classifica dei {len(cands)} migliori post (ultime {st['ore']} ore), ✅ = li preparo:\n\n"
                         + "\n".join(righe) + "\n\nScrivi /altri per farti preparare i successivi.")
        await _produce(bot, chat_ids, brand, chosen, st["min_score"], "report")
        await _broadcast(bot, chat_ids, "Report completato.")


async def run_more(bot, chat_ids: list[int]) -> None:
    """Prepara i successivi post dell'ultima classifica, saltando quelli già lavorati."""
    _running = _lock()
    if _running.locked():
        await _broadcast(bot, chat_ids, "Sto già preparando dei post, aspetta che finisca.")
        return
    async with _running:
        brand = writer.load_brand()
        st = _settings(brand)
        ranking = storage.last_ranking()
        if not ranking:
            await _broadcast(bot, chat_ids, "Non c'è ancora una classifica: lancia prima /report.")
            return
        if not st["prepara"]:
            try:
                shown = int(_offset_file().read_text())
            except Exception:
                shown = 0
            nxt = [instagram.SourcePost(**d) for d in ranking[shown:shown + st["link"]]]
            if not nxt:
                await _broadcast(bot, chat_ids, "Non ci sono altri post nella classifica di oggi.")
                return
            _offset_file().write_text(str(shown + len(nxt)))
            _mark_shown(nxt)
            left = len(ranking) - shown - len(nxt)
            perche = await asyncio.to_thread(_perche, nxt[:st["perche"]]) if st["perche"] > 0 else {}
            await _send_links(bot, chat_ids, "Altri post dalla classifica:",
                              _sezioni(nxt, st["min_score"], perche, start=shown + 1),
                              "Mandami il link di quelli che ti piacciono." + ("\n/altri per i successivi." if left > 0 else ""))
            return
        done = storage.processed_urls([d["url"] for d in ranking])
        rest = [instagram.SourcePost(**d) for d in ranking if d["url"] not in done]
        rest = _pool(rest, st)
        if not rest:
            await _broadcast(bot, chat_ids, "Ho già preparato tutti i post della classifica di oggi.")
            return
        chosen = await asyncio.to_thread(_select_sync, rest, brand, st["quanti"], st["min_score"])
        await _broadcast(bot, chat_ids, f"Preparo altri {len(chosen)} post dalla classifica (ne restano {len(rest) - len(chosen)})…")
        await _produce(bot, chat_ids, brand, chosen, st["min_score"], "extra")
        await _broadcast(bot, chat_ids, "Fatto.")


def _settimana_file():
    return config.DATA_DIR / "post_settimana.json"


def _salva_settimana(cands: list) -> None:
    """Tiene i post forti (già in tema con la pagina) visti nei report degli ultimi 8 giorni."""
    import json
    try:
        d = json.loads(_settimana_file().read_text())
    except Exception:
        d = {}
    limite = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    d = {u: v for u, v in d.items() if v.get("visto", "") >= limite}
    ora = datetime.now(timezone.utc).isoformat()
    for p in cands:
        old = d.get(p.url)
        if not old or (p.score or 0) >= (old.get("score") or 0):
            d[p.url] = {**_to_dict(p), "visto": ora}
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _settimana_file().write_text(json.dumps(d))
    except OSError:
        pass


async def run_perche_settimanale(bot, chat_ids: list[int], quanti: int = 5) -> None:
    """Una volta a settimana: i migliori `quanti` post dei collaboratori degli ultimi 7 giorni (in tema con questa
    pagina) e PERCHÉ sono andati: gancio, struttura, formato, dolore, fattore, cosa ci portiamo a casa.
    Le analisi finiscono anche nella memoria dei contenuti (servono a /crea e allo studio)."""
    from . import analysis
    brand = writer.load_brand()
    handles = [h.lower().lstrip("@") for h in brand.get("pagine_monitorate") or [] if h]
    own = {(brand.get("handle") or "").lower()}
    handles = [h for h in handles if h not in own]
    if not handles or not chat_ids:
        return
    st = _settings(brand)
    # nessuna nuova lettura Apify: si usano i post forti già trovati dai report della settimana
    import json
    try:
        d = json.loads(_settimana_file().read_text())
    except Exception:
        d = {}
    limite = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    cands = []
    for v in d.values():
        if v.get("visto", "") >= limite:
            v = {k: v[k] for k in ("url", "owner", "caption", "kind", "image_urls", "likes", "comments", "timestamp",
                                   "score", "motivo", "natura", "pagina") if k in v}
            cands.append(instagram.SourcePost(**v))
    cands = sorted([p for p in cands if p.image_urls and not p.is_video and p.score >= st["min_score"]],
                   key=lambda p: -p.score)
    cands = cands[:quanti]
    if not cands:
        await _broadcast(bot, chat_ids, f"Il perché della settimana ({brand['nome']}): nessun post dei collaboratori "
                                        f"è andato almeno {st['min_score']:.1f}x sopra la media questa settimana.")
        return
    analisi = await asyncio.to_thread(analysis.analizza_post,
                                      [(p, float(p.score or 0), "collaboratore") for p in cands], 6)
    if not analisi:
        return
    righe = [analysis.scheda(a, i) for i, a in enumerate(sorted(analisi, key=lambda a: -(a.get("rendimento") or 0)), 1)]
    await _send_links(bot, chat_ids,
                      f"🧠 {brand['nome']} · il perché della settimana: i {len(righe)} post dei collaboratori andati "
                      "meglio negli ultimi 7 giorni e cosa li ha spinti.",
                      righe, "Mandami il link di quelli che vuoi preparare in italiano.")
