"""Passaggi comuni a link manuali e report: dal post sorgente al post pronto, e invio su Telegram."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from telegram import InputMediaDocument, Message

from . import config, images, instagram, storage, writer


def job_dir(src_url: str, suffix: str) -> Path:
    code = re.sub(r"\W", "", src_url.rstrip("/").split("/")[-1])
    return config.DATA_DIR / "jobs" / f"{code}_{suffix}"


async def build_post(src: instagram.SourcePost, brand: dict, suffix: str,
                     on_status=None) -> tuple[dict, list[Path], list[int]]:
    """Scarica le slide, scrive il post con Claude e rigenera le slide con Gemini."""
    async def status(text: str):
        if on_status:
            await on_status(text)

    originals = await asyncio.to_thread(instagram.download_images, src.image_urls)
    await status(f"Traduco e adatto {len(originals)} slide…")
    post = await asyncio.to_thread(writer.create_post, src.caption, src.owner, originals, brand,
                                   storage.list_rules(), storage.recent_style())
    post["slides"] = post["slides"][:len(originals)]

    jdir = job_dir(src.url, suffix)
    (jdir / "originali").mkdir(parents=True, exist_ok=True)
    for i, data in enumerate(originals, 1):
        (jdir / "originali" / f"{i:02d}.jpg").write_bytes(data)

    await status(f"Preparo le slide in italiano con il nostro logo ({len(post['slides'])})…")
    paths, failed = await images.render_all(post, originals, brand, jdir)
    post["_fallite"] = failed
    return post, paths, failed


def summary(post: dict, failed: list[int], header: str = "") -> str:
    cta = post.get("cta", {})
    cta_txt = f"manuale {cta.get('manuale')}" if cta.get("tipo") == "manuale" else "segui la pagina"
    lines = [header] if header else []
    lines += [
        f"Tipo: {post.get('formato')} {post.get('natura')} | Hook: {post.get('tipo_hook')}",
        f"CTA: {cta_txt}. {cta.get('motivo', '')}",
    ]
    if failed:
        lines.insert(0, f"⚠️ POST INCOMPLETO, non pubblicarlo: slide {', '.join(map(str, failed))} non riuscite "
                        "dopo tutti i tentativi e quindi non inviate. Scrivi \"riprova\" per rifarle.")
    if post.get("_avviso"):
        lines.append(post["_avviso"])
    lines.append("Rispondi a questo messaggio per chiedere modifiche.")
    return "\n".join(lines)


async def send_post(bot, chat_id: int, job_id: int, post: dict, paths: list[Path], failed: list[int],
                    only_text: bool = False, header: str = "", numbers: list[int] | None = None) -> None:
    """Invia slide, descrizione e riepilogo. Le slide fallite non vengono inviate.
    `numbers` = numero di slide di ogni percorso (se si invia solo una parte delle slide)."""
    sent: list[Message] = []
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
    sent.append(await bot.send_message(chat_id, post["descrizione"]))
    sent.append(await bot.send_message(chat_id, summary(post, failed, header)))
    for m in sent:
        storage.link_message(chat_id, m.message_id, job_id)
