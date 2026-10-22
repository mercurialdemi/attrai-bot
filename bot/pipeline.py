"""Passaggi comuni a link manuali e report: dal post sorgente al post pronto, e invio su Telegram."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from telegram import InputMediaDocument, Message

from . import canva, config, diagnostica, images, instagram, storage, writer


def job_dir(src_url: str, suffix: str) -> Path:
    code = re.sub(r"\W", "", src_url.rstrip("/").split("/")[-1])
    return config.DATA_DIR / "jobs" / f"{code}_{suffix}"


async def build_post(src: instagram.SourcePost, brand: dict, suffix: str,
                     on_status=None, originals: list[bytes] | None = None) -> tuple[dict, list[Path], list[int]]:
    """Scarica le slide (o usa quelle già fornite), scrive il post con Claude e rigenera le slide con Gemini."""
    async def status(text: str):
        if on_status:
            await on_status(text)

    if originals is None:
        originals = await asyncio.to_thread(instagram.download_images, src.image_urls)
    await status(f"Traduco e adatto {len(originals)} slide…")
    post = await asyncio.to_thread(writer.create_post, src.caption, src.owner, originals, brand,
                                   storage.list_rules(), storage.recent_style())
    post["slides"] = post["slides"][:len(originals)]

    jdir = job_dir(src.url, suffix)
    (jdir / "originali").mkdir(parents=True, exist_ok=True)
    for i, data in enumerate(originals, 1):
        (jdir / "originali" / f"{i:02d}.jpg").write_bytes(data)

    da_fare = sum(1 for x in post["slides"] if x.get("da_modificare", True) or x.get("watermark_originale"))
    await status(f"Preparo le slide: {da_fare} da rifare in italiano, {len(post['slides']) - da_fare} già a posto…")
    paths, failed = await images.render_all(post, originals, brand, jdir)
    paths, failed = apply_cta_slide(post, paths, failed, jdir)
    post["_fallite"] = failed
    return post, paths, failed


def after_save(job_id: int, source_url: str, post: dict, paths: list[Path], failed: list[int]) -> None:
    """Registra il post per il rapporto notturno (non blocca mai il lavoro)."""
    try:
        diagnostica.record_post(job_id, source_url, post, paths, failed)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Diagnostica non registrata")


def cta_dir() -> Path:
    return config.DATA_DIR / "cta"


def cta_file(key: str) -> Path | None:
    for k in dict.fromkeys((key, "segui")):
        p = cta_dir() / f"{k}.jpg"
        if k and p.exists():
            return p
    return None


def apply_cta_slide(post: dict, paths: list[Path], failed: list[int], jdir: Path):
    """In fondo al carosello va sempre la nostra slide CTA: sostituisce la CTA della pagina di origine
    (se c'è) oppure si aggiunge come ultima slide."""
    cta = post.get("cta") or {}
    key = (cta.get("manuale") if cta.get("tipo") == "manuale" else "segui") or "segui"
    exact = cta_dir() / f"{key}.jpg"
    rewritten = any(sl.get("_cta_riscritta") for sl in post.get("slides", []))
    if not exact.exists() and rewritten:
        # niente slide CTA caricata per questo manuale: resta la CTA della pagina di origine riscritta per noi
        return paths, failed
    src = cta_file(key)
    if not src or post.get("formato") == "singolo":
        if not src:
            post["_avviso"] = ((post.get("_avviso") or "") + "\nManca la vostra slide CTA finale: mandamela come "
                               "immagine con didascalia \"cta segui\" (o \"cta friendzone\", ecc.).").strip()
        return paths, failed
    slides = post["slides"]
    target = paths[0].parent / f"cta_{key}.jpg"
    target.write_bytes(src.read_bytes())
    cta_idx = [i for i, sl in enumerate(slides) if sl.get("e_cta")]
    if cta_idx and cta_idx[-1] >= len(slides) - 2:          # la CTA originale è in fondo: la sostituiamo
        i = cta_idx[-1]
        paths = paths[:i] + [target] + paths[i + 1:]
        slides[i] = {**slides[i], "_canva": {"tipo": "immagine"}, "testo": "(slide CTA AttraiSeduci)"}
        failed = [n for n in failed if n != i + 1]
    else:
        paths = paths + [target]
        slides.append({"numero": len(slides) + 1, "testo": "(slide CTA AttraiSeduci)", "testo_originale": "",
                       "watermark_originale": "", "e_cta": True, "da_modificare": False,
                       "_canva": {"tipo": "immagine"}})
    return paths, failed


def summary(post: dict, failed: list[int], header: str = "") -> str:
    cta = post.get("cta", {})
    cta_txt = f"manuale {cta.get('manuale')}" if cta.get("tipo") == "manuale" else "segui la pagina"
    lines = [header] if header else []
    lines += [
        f"Tipo: {post.get('formato')} {post.get('natura')} | Hook: {post.get('tipo_hook')}",
        f"CTA: {cta_txt}. {cta.get('motivo', '')}",
    ]
    if failed:
        motivi = []
        for n in failed:
            if 1 <= n <= len(post.get("slides", [])):
                pr = post["slides"][n - 1].get("_problema")
                if pr:
                    motivi.append(f"slide {n}: {pr}")
        lines.insert(0, f"⚠️ POST INCOMPLETO, non pubblicarlo: slide {', '.join(map(str, failed))} non riuscite "
                        "dopo tutti i tentativi e quindi non inviate. Scrivi \"riprova\" per rifarle."
                        + ("\nMotivo: " + " | ".join(motivi)[:600] if motivi else ""))
    if post.get("_avviso"):
        lines.append(post["_avviso"])
    lines.append("Rispondi a questo messaggio per chiedere modifiche.")
    return "\n".join(lines)


async def send_post(bot, chat_id: int, job_id: int, post: dict, paths: list[Path], failed: list[int],
                    only_text: bool = False, header: str = "", numbers: list[int] | None = None) -> None:
    """Invia slide, descrizione e riepilogo. Le slide fallite non vengono inviate.
    `numbers` = numero di slide di ogni percorso (se si invia solo una parte delle slide)."""
    sent: list[Message] = []
    numbers_given = numbers is not None
    if not only_text:
        numbers = numbers or list(range(1, len(paths) + 1))
        good = [(n, p) for n, p in zip(numbers, paths) if n not in set(failed)]
        for start in range(0, len(good), 10):
            chunk = good[start:start + 10]
            if len(chunk) == 1:   # Telegram vuole almeno 2 file in un gruppo
                n, p = chunk[0]
                sent.append(await bot.send_document(chat_id, p.read_bytes(),
                                                    filename=f"AttraiSeduci_{job_id}_slide_{n:02d}.jpg"))
            else:
                sent += await bot.send_media_group(chat_id, [
                    InputMediaDocument(p.read_bytes(), filename=f"AttraiSeduci_{job_id}_slide_{n:02d}.jpg")
                    for n, p in chunk])
    if not only_text and numbers_given is False:
        m = await send_canva(bot, chat_id, job_id, post, paths, failed)
        if m:
            sent.append(m)
    sent.append(await bot.send_message(chat_id, post["descrizione"]))
    sent.append(await bot.send_message(chat_id, summary(post, failed, header)))
    if failed and paths:
        orig_dir = paths[0].parent / "originali"
        for n in failed:
            op = orig_dir / f"{n:02d}.jpg"
            if op.exists():
                pr = post["slides"][n - 1].get("_problema", "") if n <= len(post["slides"]) else ""
                sent.append(await bot.send_photo(chat_id, op.read_bytes(),
                                                 caption=f"Questa è la slide {n} originale, non riuscita. {pr}"[:1000]))
    for m in sent:
        storage.link_message(chat_id, m.message_id, job_id)


async def send_canva(bot, chat_id: int, job_id: int, post: dict, paths: list[Path], failed: list[int]):
    """File PowerPoint da importare in Canva (testi modificabili nelle slide di solo testo)."""
    try:
        if not paths or len(failed) >= len(paths):
            return None
        out = paths[0].parent / f"AttraiSeduci_{job_id}_Canva.pptx"
        await asyncio.to_thread(canva.build, post, paths, out, failed)
        return await bot.send_document(
            chat_id, out.read_bytes(), filename=out.name,
            caption="File per Canva: in Canva → Crea → Importa file. I testi delle slide di solo testo sono modificabili.")
    except Exception:
        import logging
        logging.getLogger(__name__).exception("File Canva non creato")
        return None
