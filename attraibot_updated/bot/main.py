"""Bot Telegram AttraiSeduci: link di un post -> post italiano pronto."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InputMediaDocument, Message, Update
from telegram.constants import ChatAction
from telegram.ext import (Application, CommandHandler, ContextTypes, MessageHandler, filters)

from . import config, images, instagram, report, storage, writer

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("attraibot")

HELP = (
    "Mandami il link di un post pubblico (qualsiasi pagina, anche competitor) e te lo restituisco "
    "pronto per AttraiSeduci: slide in italiano, descrizione e CTA.\n"
    "Se il post ha un watermark, lo sostituisco con il nostro logo; se non ce l'ha, lascio l'immagine pulita.\n\n"
    "Per modificare un post, rispondi ai suoi messaggi (o scrivi subito dopo) con quello che vuoi cambiare, "
    "per esempio: \"slide 2 più corta\", \"cambia l'hook\", \"metti CTA al manuale Potere Sociale\", "
    "\"rifai la grafica della slide 4\".\n\n"
    "Per dare una regola fissa scrivi: regola: ...\n"
    "/regole per vederle, /togli N per toglierne una, /id per il tuo ID Telegram, /report per il report manuale."
)


# ---------------------------------------------------------------- accesso

async def _authorized(update: Update) -> bool:
    user = update.effective_user
    if user and user.id in config.ALLOWED_USER_IDS:
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            f"Questo bot è privato. Il tuo ID Telegram è {user.id if user else '?'}: "
            "aggiungilo alla variabile ALLOWED_USER_IDS su Render per avere accesso."
        )
    return False


# ---------------------------------------------------------------- comandi

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _authorized(update):
        await update.effective_message.reply_text(HELP)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"Il tuo ID Telegram è {update.effective_user.id}")


async def cmd_regole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    rules = storage.list_rules()
    if not rules:
        await update.effective_message.reply_text("Nessuna regola fissa. Aggiungila scrivendo: regola: ...")
        return
    await update.effective_message.reply_text("\n".join(f"{r['id']}. {r['text']}" for r in rules))


async def cmd_togli(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Scrivi il numero della regola, es. /togli 3")
        return
    ok = storage.remove_rule(int(context.args[0]))
    await update.effective_message.reply_text("Regola tolta." if ok else "Non trovo quella regola.")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    await update.effective_message.reply_text("Preparo il report dei post migliori…")
    brand = writer.load_brand()
    await report.run_report(context.bot, update.effective_chat.id, brand)
    await update.effective_message.reply_text("Report completato.")


# ---------------------------------------------------------------- invio risultati

def _job_dir(job_key: str) -> Path:
    return config.DATA_DIR / "jobs" / job_key


def _summary(post: dict, failed: list[int]) -> str:
    cta = post.get("cta", {})
    cta_txt = f"manuale {cta.get('manuale')}" if cta.get("tipo") == "manuale" else "segui la pagina"
    lines = [
        f"Tipo: {post.get('formato')} {post.get('natura')} | Hook: {post.get('tipo_hook')}",
        f"CTA: {cta_txt}. {cta.get('motivo', '')}",
    ]
    if failed:
        lines.append(f"Attenzione: slide {', '.join(map(str, failed))} non rielaborate da Gemini "
                     "(ti mando l'originale con il nostro logo). Scrivi \"rifai slide N\" per riprovare.")
    if post.get("_avviso"):
        lines.append(post["_avviso"])
    lines.append("Rispondi a questo messaggio per chiedere modifiche.")
    return "\n".join(lines)


async def _send_post(context: ContextTypes.DEFAULT_TYPE, chat_id: int, job_id: int, post: dict,
                     paths: list[Path], failed: list[int], only_text: bool = False):
    sent: list[Message] = []
    if not only_text:
        for start in range(0, len(paths), 10):
            group = []
            for p in paths[start:start + 10]:
                group.append(InputMediaDocument(p.read_bytes(), filename=f"AttraiSeduci_{job_id}_{p.stem.split('_v')[0]}.jpg"))
            sent += await context.bot.send_media_group(chat_id, group)
    sent.append(await context.bot.send_message(chat_id, post["descrizione"]))
    sent.append(await context.bot.send_message(chat_id, _summary(post, failed)))
    for m in sent:
        storage.link_message(chat_id, m.message_id, job_id)


# ---------------------------------------------------------------- flusso principale

async def process_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    status = await msg.reply_text("Leggo il post…")
    try:
        brand = writer.load_brand()

        src = await asyncio.to_thread(instagram.fetch_post, url)
        # Nessun filtro: qualsiasi pagina pubblica è accettata come spunto.
        if src.is_video:
            await status.edit_text("Per ora gestisco post con immagini e caroselli, i reel arriveranno più avanti.")
            return

        originals = await asyncio.to_thread(instagram.download_images, src.image_urls)
        await status.edit_text(f"Traduco e adatto {len(originals)} slide…")
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        post = await asyncio.to_thread(writer.create_post, src.caption, src.owner, originals, brand,
                                       storage.list_rules(), storage.recent_style())
        post["slides"] = post["slides"][:len(originals)]

        job_key = re.sub(r"\W", "", url.rstrip("/").split("/")[-1]) + f"_{msg.message_id}"
        jdir = _job_dir(job_key)
        (jdir / "originali").mkdir(parents=True, exist_ok=True)
        for i, data in enumerate(originals, 1):
            (jdir / "originali" / f"{i:02d}.jpg").write_bytes(data)

        await status.edit_text(f"Preparo le slide in italiano ({len(post['slides'])})…")
        paths, failed = await images.render_all(post, originals, brand, jdir)
        job_id = storage.save_job(chat_id, update.effective_user.id, url, src.owner, post, paths)
        await status.delete()
        await _send_post(context, chat_id, job_id, post, paths, failed)
    except instagram.InstagramError as e:
        await status.edit_text(str(e))
    except Exception:
        log.exception("Errore elaborando %s", url)
        await status.edit_text("Qualcosa è andato storto mentre preparavo il post. Riprova; se succede ancora, controlla i log su Render.")


def _changed_slides(old: dict, new: dict) -> set[int]:
    changed = set(new.get("slide_da_rifare") or [])
    olds = old.get("slides", [])
    for i, s in enumerate(new.get("slides", [])):
        if i >= len(olds) or (s.get("testo") or "").strip() != (olds[i].get("testo") or "").strip():
            changed.add(i + 1)
    return changed


async def process_revision(update: Update, context: ContextTypes.DEFAULT_TYPE, job: dict, instruction: str):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    status = await msg.reply_text("Applico la modifica…")
    try:
        brand = writer.load_brand()
        new = await asyncio.to_thread(writer.revise_post, job["post"], instruction, brand,
                                      storage.list_rules(), storage.recent_style())
        jdir = job["slide_paths"][0].parent
        originals = [p.read_bytes() for p in sorted((jdir / "originali").glob("*.jpg"))]
        new["slides"] = new["slides"][:len(originals)]
        changed = _changed_slides(job["post"], new)
        failed: list[int] = []
        paths = job["slide_paths"]
        if changed:
            await status.edit_text(f"Rifaccio le slide {', '.join(map(str, sorted(changed)))}…")
            paths, failed = await images.render_all(new, originals, brand, jdir, only=changed,
                                                    existing=job["slide_paths"])
        new["slide_da_rifare"] = []
        storage.update_job(job["id"], new, paths)
        await status.delete()
        if changed:
            await _send_post(context, chat_id, job["id"], new, [paths[i - 1] for i in sorted(changed)], failed)
        else:
            await _send_post(context, chat_id, job["id"], new, paths, failed, only_text=True)
    except Exception:
        log.exception("Errore nella modifica del post %s", job["id"])
        await status.edit_text("Non sono riuscito ad applicare la modifica. Riprova riformulandola.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()

    m = re.match(r"^\s*regola\s*:\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if m:
        rid = storage.add_rule(m.group(1).strip(), update.effective_user.id)
        await msg.reply_text(f"Regola {rid} salvata: la applico da ora a ogni post.")
        return

    urls = [instagram.find_post_url(part) for part in text.split()]
    urls = list(dict.fromkeys(u for u in urls if u))
    if urls:
        for u in urls:
            context.application.create_task(process_link(update, context, u))
        return

    job = None
    if msg.reply_to_message:
        jid = storage.job_for_message(update.effective_chat.id, msg.reply_to_message.message_id)
        job = storage.get_job(jid) if jid else None
    job = job or storage.last_job(update.effective_chat.id)
    if job:
        await process_revision(update, context, job, text)
    else:
        await msg.reply_text(HELP)


def main():
    missing = config.missing_keys()
    if missing:
        raise SystemExit(f"Mancano queste variabili d'ambiente: {', '.join(missing)}")
    if not config.ALLOWED_USER_IDS:
        log.warning("ALLOWED_USER_IDS è vuoto: il bot risponderà a tutti solo con il loro ID.")
    storage._db()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler(["start", "aiuto", "help"], cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("regole", cmd_regole))
    app.add_handler(CommandHandler("togli", cmd_togli))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Scheduler report mattutino
    brand = writer.load_brand()
    report_cfg = brand.get("report", {})
    if report_cfg.get("ora"):
        tz = ZoneInfo(report_cfg.get("fuso", "Europe/Rome"))
        ora = datetime.strptime(report_cfg["ora"], "%H:%M").time().replace(tzinfo=tz)
        chat_id = int(report_cfg.get("chat_id") or 0)

        if chat_id:
            async def scheduled_report(context: ContextTypes.DEFAULT_TYPE):
                b = writer.load_brand()
                await report.run_report(context.bot, chat_id, b)

            app.job_queue.run_daily(scheduled_report, time=ora, name="report_mattutino")
            log.info("Report mattutino programmato alle %s %s nella chat %s", report_cfg["ora"], tz, chat_id)
        else:
            log.warning("report.chat_id non configurato: il report mattutino non partirà.")

    log.info("Bot avviato.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()