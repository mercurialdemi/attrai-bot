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
_running = asyncio.Lock()

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


def _baselines(posts: list[instagram.SourcePost]) -> dict[str, float]:
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=90)).isoformat()
    mature = (now - timedelta(days=3)).isoformat()
    fetched: dict[str, list[float]] = {}
    for p in posts:
        e = _engagement(p)
        if e is not None and not p.is_video:
            fetched.setdefault(p.owner, []).append(e)
    out = {}
    for owner, values in fetched.items():
        hist = storage.page_engagements(owner, since, mature)
        base = hist if len(hist) >= 8 else values
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
        p.score = e / baseline.get(p.owner, max(e, 1.0))
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
            "likes": p.likes, "comments": p.comments, "timestamp": p.timestamp, "score": p.score}


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


def _settings(brand: dict) -> dict:
    cfg = brand.get("report") or {}
    quanti = int(cfg.get("quanti_post", 5))
    return {
        "quanti": quanti,
        "candidati": max(quanti, int(cfg.get("candidati", 15))),
        "ore": int(cfg.get("finestra_ore", 48)),
        "per_pagina": int(cfg.get("post_per_pagina", 30)),
        "min_score": float(cfg.get("punteggio_minimo", 1.0)),
        "completa": bool(cfg.get("completa_sempre", True)),
        # False = il report manda solo i link dei migliori post (nessun credito speso per tradurli)
        "prepara": bool(cfg.get("prepara_post", False)),
    }


def _offset_file():
    return config.DATA_DIR / "classifica_mostrati.txt"


def _link_lines(items: list, start: int, min_score: float) -> list[str]:
    out = []
    for i, p in enumerate(items, start):
        anteprima = " ".join((p.caption or "").split())[:70]
        nums = f"❤️ {p.likes or 0} · 💬 {p.comments or 0}"
        out.append(f"{i}. @{p.owner} · {p.score:.1f}x la sua media{'' if p.score >= min_score else ' (sotto media)'}"
                   f" · {nums}\n{p.url}" + (f"\n“{anteprima}…”" if anteprima else ""))
    return out


async def _send_links(bot, chat_ids: list[int], header: str, lines: list[str], footer: str) -> None:
    """Messaggi di soli link, spezzati sotto il limite di Telegram, senza anteprime."""
    chunks, cur = [], header
    for ln in lines:
        if len(cur) + len(ln) + 2 > 3800:
            chunks.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + ln
    chunks.append(cur + "\n\n" + footer)
    for c in chat_ids:
        for text in chunks:
            try:
                await bot.send_message(c, text, disable_web_page_preview=True)
            except Exception as e:
                log.warning("Invio a %s fallito: %s", c, e)


def _pool(cands: list[instagram.SourcePost], st: dict) -> list[instagram.SourcePost]:
    """Se completa_sempre è spento, si lavora solo sui post sopra la soglia."""
    return cands if st["completa"] else [p for p in cands if p.score >= st["min_score"]]


async def run_report(bot, chat_ids: list[int]) -> None:
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
            posts = await asyncio.to_thread(instagram.fetch_profiles, handles, st["per_pagina"])
        except Exception:
            log.exception("Report: lettura pagine fallita")
            await _broadcast(bot, chat_ids, "Report: non sono riuscito a leggere le pagine. Riprova con /report.")
            return

        letti = {p.owner for p in posts}
        mancanti = [h for h in handles if h not in letti]
        if mancanti:
            await _broadcast(bot, chat_ids, "Non riesco a leggere: " + ", ".join(f"@{h}" for h in mancanti)
                             + ". Di solito è perché la pagina ha un limite d'età"
                             + (" e anche la lettura con accesso non è riuscita: controlla che INSTAGRAM_COOKIES sia ancora valido."
                                if config.INSTAGRAM_COOKIES else
                                ": inserisci INSTAGRAM_COOKIES su Render (vedi la guida)."))
        if not st["prepara"]:
            # solo link: classifica lunga salvata, si mostrano i primi; /altri mostra i successivi
            cands = rank_candidates(posts, st["ore"], st["candidati"] * 4)
            storage.save_ranking([_to_dict(p) for p in cands])
            if not cands:
                await _broadcast(bot, chat_ids, f"Report: nessun post nuovo con immagini nelle ultime {st['ore']} ore.")
                return
            first = cands[:st["candidati"]]
            _offset_file().write_text(str(len(first)))
            await _send_links(
                bot, chat_ids,
                f"☀️ Migliori post delle ultime {st['ore']} ore (rendimento rispetto alla media di ogni pagina):",
                _link_lines(first, 1, st["min_score"]),
                "Mandami il link di quelli che ti piacciono e li preparo in italiano."
                + (f"\nScrivi /altri per vederne altri {min(st['candidati'], len(cands) - len(first))}." if len(cands) > len(first) else ""))
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
            nxt = [instagram.SourcePost(**d) for d in ranking[shown:shown + st["candidati"]]]
            if not nxt:
                await _broadcast(bot, chat_ids, "Non ci sono altri post nella classifica di oggi.")
                return
            _offset_file().write_text(str(shown + len(nxt)))
            left = len(ranking) - shown - len(nxt)
            await _send_links(bot, chat_ids, "Altri post dalla classifica:",
                              _link_lines(nxt, shown + 1, st["min_score"]),
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
