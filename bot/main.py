"""Bot Telegram AttraiSeduci: link di un post dei collaboratori -> post italiano pronto, più report mattutino."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (Application, CommandHandler, ContextTypes, MessageHandler, filters)

from . import analysis, config, diagnostica, images, instagram, instagram_api, pipeline, report, storage, writer

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("attraibot")

HELP = (
    "Mandami il link di un post dei nostri collaboratori e te lo restituisco pronto per AttraiSeduci: "
    "slide in italiano con il nostro logo, descrizione e CTA.\n"
    "Se Instagram blocca il link, mandami direttamente le slide come album (meglio come File), "
    "con la descrizione originale nella didascalia.\n\n"
    "Per modificare un post, rispondi ai suoi messaggi (o scrivi subito dopo) con quello che vuoi cambiare, "
    "per esempio: \"slide 2 più corta\", \"cambia l'hook\", \"metti CTA al manuale Potere Sociale\", "
    "\"rifai la grafica della slide 4\".\n\n"
    "Per dare una regola fissa scrivi: regola: ...\n"
    "Se un post ha un difetto scrivi: segnala: ... (rispondendo al post): stanotte lo correggo nel codice.\n"
    "/regole per vederle, /togli N per toglierne una\n"
    "/report per lanciare subito il report, /altri per i post successivi della classifica\n"
    "/pagine per le pagine monitorate, /statistiche per i numeri veri degli ultimi post\n"
    "/analizza per studiare i post di AttraiSeduci e scrivere descrizioni come le tue migliori, /stile per vedere la guida\n"
    "Slide CTA finale: mandami l'immagine con didascalia \"cta segui\" (o \"cta friendzone\", \"cta potere-sociale\"...)\n"
    "/id per il tuo ID Telegram"
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


async def cmd_pagine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    brand = writer.load_brand()
    pages = [h for h in brand.get("pagine_monitorate") or [] if h]
    cfg = brand.get("report") or {}
    text = ("Pagine monitorate dal report:\n" + "\n".join(f"@{h}" for h in pages)) if pages else \
        "Nessuna pagina monitorata: aggiungile in pagine_monitorate nel profilo del brand."
    text += (f"\n\nReport ogni giorno alle {cfg.get('ora', '08:00')}, ultime {cfg.get('finestra_ore', 48)} ore, "
             f"{cfg.get('candidati', 15)} candidati, {cfg.get('quanti_post', 5)} post pronti.")
    await update.effective_message.reply_text(text)


async def cmd_analizza(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    msg = update.effective_message
    status = await msg.reply_text("Studio i post di AttraiSeduci: leggo gli ultimi 80 e confronto i migliori con i peggiori…")

    async def go():
        try:
            style = await asyncio.to_thread(analysis.run_analysis, writer.load_brand())
            cta_txt = "\n".join(f"• {c['rendimento']}x · {c['testo'][:140]}" for c in style.get("cta_migliori", [])[:5])
            slides_txt = ", ".join(style.get("slide_cta", {}).keys()) or "nessuna trovata"
            await status.edit_text(
                f"Studio fatto su {style['post_analizzati']} post. Da ora scrivo le descrizioni con questa guida "
                f"e {len(style['esempi'])} esempi tuoi:\n\n{style['guida']}"[:2500]
                + f"\n\nCTA che rendono di più:\n{cta_txt or '(nessuna)'}"
                + f"\n\n{style.get('lezioni_cta', '')}"[:800]
                + f"\n\nSlide CTA prese dai tuoi caroselli: {slides_txt}.")
        except instagram.InstagramError as e:
            await status.edit_text(str(e))
        except Exception:
            log.exception("Analisi fallita")
            await status.edit_text("Lo studio della pagina non è riuscito. Riprova tra qualche minuto.")

    context.application.create_task(go())


async def cmd_stile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    style = writer.load_style()
    if not style:
        await update.effective_message.reply_text("Non ho ancora studiato la pagina: lancia /analizza.")
        return
    await update.effective_message.reply_text(
        f"Guida di stile del {style.get('creato', '')[:10]} ({style.get('post_analizzati')} post, "
        f"{len(style.get('esempi', []))} esempi):\n\n{style.get('guida', '')}"[:4000])


async def cmd_statistiche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    if not instagram_api.enabled():
        await update.effective_message.reply_text("Le statistiche di Instagram non sono collegate: manca INSTAGRAM_TOKEN su Render.")
        return
    msg = await update.effective_message.reply_text("Leggo le statistiche degli ultimi post…")
    try:
        acc = await asyncio.to_thread(instagram_api.account)
        media = await asyncio.to_thread(instagram_api.recent_media, 8)
        righe = [f"@{acc.get('username')} · {acc.get('followers_count')} follower\n"]
        for m in media:
            st = await asyncio.to_thread(instagram_api.media_insights, m["id"])
            cap = " ".join((m.get("caption") or "").split())[:50]
            righe.append(f"{m['timestamp'][:10]} · {cap}…\n  copertura {st.get('reach', '-')} · salvati {st.get('saved', '-')} · "
                         f"condivisi {st.get('shares', '-')} · follow {st.get('follows', '-')} · clic link {st.get('clic_link', '-')}")
        await msg.edit_text("\n".join(righe)[:4000])
    except Exception as e:
        log.exception("Statistiche Instagram")
        await msg.edit_text(f"Non riesco a leggere le statistiche: {str(e)[:300]}")


async def cmd_altri(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    context.application.create_task(report.run_more(context.bot, [update.effective_chat.id]))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    context.application.create_task(report.run_report(context.bot, [update.effective_chat.id]))


# ---------------------------------------------------------------- link manuale

async def process_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    status = await msg.reply_text("Leggo il post…")
    work = storage.start_work(chat_id, url)
    try:
        brand = writer.load_brand()
        src = await asyncio.to_thread(instagram.fetch_post, url)
        if src.is_video:
            await status.edit_text("Per ora gestisco post con immagini e caroselli, i reel arriveranno più avanti.")
            return
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        post, paths, failed = await pipeline.build_post(src, brand, str(msg.message_id), on_status=status.edit_text)
        job_id = storage.save_job(chat_id, update.effective_user.id, url, src.owner, post, paths)
        pipeline.after_save(job_id, src.url, post, paths, failed)
        await status.delete()
        await pipeline.send_post(context.bot, chat_id, job_id, post, paths, failed)
    except instagram.InstagramError as e:
        await status.edit_text(str(e))
    except Exception:
        log.exception("Errore elaborando %s", url)
        await status.edit_text("Qualcosa è andato storto mentre preparavo il post. Riprova; se succede ancora, controlla i log su Render.")
    finally:
        storage.end_work(work)


# ---------------------------------------------------------------- modifiche

RETRY_RE = re.compile(r"^\s*(riprova|rifai le slide (fallite|mancanti))\s*[.!]?\s*$", re.IGNORECASE)

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
        old = job["post"]
        old_failed = set(old.get("_fallite") or [])
        jdir = job["slide_paths"][0].parent
        originals = [p.read_bytes() for p in sorted((jdir / "originali").glob("*.jpg"))]
        n_orig = len(originals)
        # slide aggiunte dopo l'originale (es. la nostra CTA finale): restano com'erano
        extra_slides = old["slides"][n_orig:]
        extra_paths = job["slide_paths"][n_orig:]

        if RETRY_RE.match(instruction):
            if not old_failed:
                await status.edit_text("Questo post non ha slide da rifare. Per cambiare qualcosa scrivimi cosa.")
                return
            new = dict(old)
            changed = set(old_failed)
        else:
            new = await asyncio.to_thread(writer.revise_post, old, instruction, brand,
                                          storage.list_rules(), storage.recent_style())
            new["slides"] = new["slides"][:len(originals)]
            changed = {n for n in _changed_slides(old, new)
                       if n <= n_orig and "(slide CTA" not in (new["slides"][n - 1].get("testo") or "")}
            for n in changed:                       # una slide che Gabriele vuole cambiare va rifatta anche se era già italiana
                if 1 <= n <= len(new["slides"]):
                    new["slides"][n - 1]["da_modificare"] = True

        paths = job["slide_paths"]
        new_failed: list[int] = []
        if changed:
            await status.edit_text(f"Rifaccio le slide {', '.join(map(str, sorted(changed)))}…")
            new["slides"] = new["slides"][:n_orig]
            paths, new_failed = await images.render_all(new, originals, brand, jdir, only=changed,
                                                        existing=job["slide_paths"][:n_orig])
            paths = paths + extra_paths
        new["slides"] = new["slides"][:n_orig] + extra_slides
        failed = sorted((old_failed - changed) | set(new_failed))
        new["slide_da_rifare"] = []
        new["_fallite"] = failed
        if old.get("_avviso") and not new.get("_avviso"):
            new.pop("_avviso", None)
        storage.update_job(job["id"], new, paths)
        await status.delete()
        if changed:
            nums = sorted(changed)
            await pipeline.send_post(context.bot, chat_id, job["id"], new, [paths[i - 1] for i in nums],
                                     failed, numbers=nums)
            m = await pipeline.send_canva(context.bot, chat_id, job["id"], new, paths, failed)
            if m:
                storage.link_message(chat_id, m.message_id, job["id"])
        else:
            await pipeline.send_post(context.bot, chat_id, job["id"], new, paths, failed, only_text=True)
    except Exception:
        log.exception("Errore nella modifica del post %s", job["id"])
        await status.edit_text("Non sono riuscito ad applicare la modifica. Riprova riformulandola.")


# ---------------------------------------------------------------- slide inviate direttamente (album)

_albums: dict[str, dict] = {}
ALBUM_WAIT = 6  # secondi di attesa dopo l'ultima immagine: gli album inviati di seguito diventano un solo post


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    msg = update.effective_message
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        file_id = msg.document.file_id
    else:
        return
    cm = re.match(r"^\s*cta\s*:?\s*([\w-]+)\s*$", msg.caption or "", re.IGNORECASE)
    if cm:   # slide CTA di AttraiSeduci da mettere in fondo ai caroselli
        brand = writer.load_brand()
        ids = {"segui"} | {m["id"] for m in brand.get("manuali", [])}
        k = cm.group(1).lower()
        if k not in ids:
            await msg.reply_text(f"Non conosco \"{k}\". Usa uno di questi: {', '.join(sorted(ids))}.")
            return
        f = await context.bot.get_file(file_id)
        pipeline.cta_dir().mkdir(parents=True, exist_ok=True)
        data = bytes(await f.download_as_bytearray())
        from PIL import Image as _I
        import io as _io
        _I.open(_io.BytesIO(data)).convert("RGB").save(pipeline.cta_dir() / f"{k}.jpg", "JPEG", quality=95)
        (pipeline.cta_dir() / f"{k}.manuale").write_text("caricata a mano")
        await msg.reply_text(f"Slide CTA \"{k}\" salvata: la metto in fondo ai caroselli con CTA {k}."
                             + (" Se manca quella di un manuale, uso questa." if k == "segui" else ""))
        return
    key = str(msg.chat_id)   # tutte le immagini inviate di seguito nella chat (anche più album) = un post
    album = _albums.setdefault(key, {"items": [], "caption": "", "first": msg, "task": None})
    album["items"].append((msg.message_id, file_id, bool(msg.photo)))
    if msg.caption:
        album["caption"] = msg.caption
    if album["task"]:
        album["task"].cancel()

    async def later():
        try:
            await asyncio.sleep(ALBUM_WAIT)
        except asyncio.CancelledError:
            return
        data = _albums.pop(key, None)
        if data:
            await process_album(update, context, data)

    album["task"] = context.application.create_task(later())


async def process_album(update: Update, context: ContextTypes.DEFAULT_TYPE, album: dict):
    first = album["first"]
    chat_id = first.chat_id
    items = sorted(album["items"])
    status = await first.reply_text(f"Ricevute {len(items)} slide, le preparo…")
    work = storage.start_work(chat_id, f"le {len(items)} slide che mi hai mandato")
    try:
        originals = []
        for _, file_id, _is_photo in items:
            f = await context.bot.get_file(file_id)
            originals.append(bytes(await f.download_as_bytearray()))
        if any(is_photo for *_, is_photo in items):
            await first.reply_text("Consiglio: la prossima volta inviale come File (graffetta → File), "
                                   "così Telegram non le comprime e le slide restano in alta qualità.")
        caption = album["caption"].strip()
        url = instagram.find_post_url(caption) if caption else None
        if url:
            caption = caption.replace(url, "").strip()
        if len(originals) > 20:
            await first.reply_text(f"Hai mandato {len(originals)} immagini: Instagram accetta al massimo 20 slide "
                                   "per carosello, preparo le prime 20.")
            originals = originals[:20]
        src = instagram.SourcePost(url=url or f"telegram/{first.message_id}",
                                   owner="inviato", caption=caption, kind="Sidecar" if len(items) > 1 else "Image")
        brand = writer.load_brand()
        post, paths, failed = await pipeline.build_post(src, brand, str(first.message_id),
                                                        on_status=status.edit_text, originals=originals)
        job_id = storage.save_job(chat_id, update.effective_user.id, src.url, src.owner, post, paths)
        pipeline.after_save(job_id, src.url, post, paths, failed)
        await status.delete()
        await pipeline.send_post(context.bot, chat_id, job_id, post, paths, failed)
    except Exception:
        log.exception("Errore elaborando le slide inviate")
        await status.edit_text("Qualcosa è andato storto mentre preparavo le slide. Riprova; se succede ancora, controlla i log su Render.")
    finally:
        storage.end_work(work)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()

    sm = re.match(r"^\s*(segnala|problema)\s*:?\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if sm:
        jid = None
        if msg.reply_to_message:
            jid = storage.job_for_message(update.effective_chat.id, msg.reply_to_message.message_id)
        if not jid:
            last = storage.last_job(update.effective_chat.id)
            jid = last["id"] if last else None
        diagnostica.record_report(jid, sm.group(2).strip(), update.effective_user.id)
        await msg.reply_text("Segnalazione salvata" + (f" sul post {jid}" if jid else "")
                             + ": stanotte la guardo insieme alle immagini e correggo il bot. Domattina ti arriva il riepilogo.")
        return

    m = re.match(r"^\s*regola\s*:\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if m:
        rid = storage.add_rule(m.group(1).strip(), update.effective_user.id)
        await msg.reply_text(f"Regola {rid} salvata: la applico da ora a ogni post.")
        return

    urls = list(dict.fromkeys(u for u in (instagram.find_post_url(part) for part in text.split()) if u))
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


# ---------------------------------------------------------------- avvio

def _schedule_report(app: Application):
    cfg = (writer.load_brand().get("report") or {})
    if not cfg.get("ora"):
        log.info("Report mattutino disattivato (report.ora vuoto).")
        return
    chat_ids = sorted(config.REPORT_CHAT_IDS or config.ALLOWED_USER_IDS)
    if not chat_ids:
        log.warning("Nessun destinatario per il report: imposta ALLOWED_USER_IDS.")
        return
    tz = ZoneInfo(cfg.get("fuso", "Europe/Rome"))
    at = datetime.strptime(str(cfg["ora"]), "%H:%M").time().replace(tzinfo=tz)

    async def job(context: ContextTypes.DEFAULT_TYPE):
        await report.run_report(context.bot, chat_ids)

    app.job_queue.run_daily(job, time=at, name="report_mattutino")
    log.info("Report programmato ogni giorno alle %s (%s) per %s", cfg["ora"], tz, chat_ids)


def _schedule_analysis(app: Application):
    """Ogni lunedì rifà lo studio della pagina: guida di stile, CTA migliori, risultati dei post del bot."""
    cfg = (writer.load_brand().get("studio") or {})
    if cfg.get("attivo", True) is False:
        return
    chat_ids = sorted(config.REPORT_CHAT_IDS or config.ALLOWED_USER_IDS)
    tz = ZoneInfo((writer.load_brand().get("report") or {}).get("fuso", "Europe/Rome"))
    at = datetime.strptime(str(cfg.get("ora", "07:30")), "%H:%M").time().replace(tzinfo=tz)

    async def job(context: ContextTypes.DEFAULT_TYPE):
        try:
            style = await asyncio.to_thread(analysis.run_analysis, writer.load_brand())
        except Exception as e:
            log.exception("Studio settimanale fallito")
            for c in chat_ids:
                await context.bot.send_message(c, f"Studio settimanale non riuscito: {str(e)[:300]}")
            return
        fb = style.get("feedback_bot") or {}
        righe = [f"Studio settimanale aggiornato su {style['post_analizzati']} post."]
        if fb.get("pubblicati_dal_bot"):
            righe.append(f"Post preparati dal bot e pubblicati: {fb['pubblicati_dal_bot']}, "
                         f"rendimento medio {fb.get('rendimento_medio')}x la media della pagina.")
            for nome, chiave in (("Hook", "per_hook"), ("CTA", "per_cta")):
                d = fb.get(chiave) or {}
                if d:
                    righe.append(nome + ": " + "; ".join(f"{k} {v['rendimento']}x" for k, v in list(d.items())[:5]))
        top = style.get("cta_migliori") or []
        if top:
            righe.append("CTA migliore: " + top[0]["testo"][:200])
        for c in chat_ids:
            await context.bot.send_message(c, "\n".join(righe))

    app.job_queue.run_daily(job, time=at, days=(int(cfg.get("giorno", 1)),), name="studio_settimanale")

    async def refresh(context: ContextTypes.DEFAULT_TYPE):   # la chiave Instagram dura 60 giorni: la rinnoviamo ogni settimana
        if instagram_api.enabled():
            ok = await asyncio.to_thread(instagram_api.refresh_token)
            log.info("Rinnovo chiave Instagram: %s", "ok" if ok else "non riuscito")

    app.job_queue.run_daily(refresh, time=at, days=(3,), name="rinnovo_token_instagram")


def _schedule_diagnostics(app: Application):
    """Ogni notte alle 2:30 carica su GitHub il rapporto con slide fallite e segnalazioni."""
    tz = ZoneInfo((writer.load_brand().get("report") or {}).get("fuso", "Europe/Rome"))
    at = datetime.strptime("02:30", "%H:%M").time().replace(tzinfo=tz)

    async def job(context: ContextTypes.DEFAULT_TYPE):
        try:
            res = await asyncio.to_thread(diagnostica.upload_daily)
            log.info(res)
        except Exception:
            log.exception("Rapporto notturno non caricato")

    app.job_queue.run_daily(job, time=at, name="rapporto_notturno")
    log.info("Studio della pagina programmato ogni settimana alle %s", at)


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
    app.add_handler(CommandHandler("pagine", cmd_pagine))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("altri", cmd_altri))
    app.add_handler(CommandHandler("statistiche", cmd_statistiche))
    app.add_handler(CommandHandler("analizza", cmd_analizza))
    app.add_handler(CommandHandler("stile", cmd_stile))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    async def notify_restart(context: ContextTypes.DEFAULT_TYPE):
        for w in storage.pop_unfinished():
            try:
                await context.bot.send_message(
                    w["chat_id"], f"Il bot è stato aggiornato mentre lavorava e il lavoro si è interrotto: "
                                  f"rimandami {w['descrizione']}.")
            except Exception:
                log.warning("Avviso di riavvio non inviato a %s", w["chat_id"])

    app.job_queue.run_once(notify_restart, when=5, name="avviso_riavvio")
    _schedule_report(app)
    _schedule_analysis(app)
    _schedule_diagnostics(app)
    log.info("Bot avviato.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
