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

from . import analysis, config, crea, diagnostica, images, instagram, instagram_api, pipeline, report, storage, writer

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("attraibot")

# Elenco unico dei comandi: compare nel menu "/" di Telegram e nella guida /comandi.
# Ogni nuovo comando va aggiunto qui (la prova tests/test_comandi.py lo controlla).
COMMANDS = [
    ("Post", [
        ("comandi", "Guida a tutti i comandi", "questa guida"),
        ("crea", "Carosello originale da zero (es. /crea 2 friendzone)",
         "crea caroselli nuovi nello stile AttraiSeduci: /crea, /crea 3, /crea segnali di disinteresse"),
        ("report", "Post migliori dei collaboratori, adesso",
         "lancia subito il report del mattino con i post che stanno andando meglio"),
        ("altri", "Altri post dalla classifica", "i post successivi del report, se vuoi sceglierne altri"),
    ]),
    ("Regole", [
        ("regole", "Le regole fisse che hai dato", "elenco numerato delle regole scritte con \"regola: ...\""),
        ("togli", "Togli una regola (es. /togli 3)", "cancella la regola con quel numero"),
    ]),
    ("Studio della pagina", [
        ("analizza", "Rifai subito lo studio (lo fa già da solo ogni notte)",
         "il bot studia da solo ogni notte i tuoi post, le descrizioni e le slide CTA; usalo solo per averlo subito"),
        ("stile", "Cosa ha imparato il bot", "guida di stile e testi CTA ricavati da /analizza"),
        ("studio", "Perché i post sono andati", "post per post: perché sono andati i post di competitor, collaboratori e nostri, più temi, hook e strutture vincenti (arriva anche da solo ogni 3 giorni)"),
        ("statistiche", "Numeri veri degli ultimi post", "copertura, salvataggi, condivisioni, clic (serve Meta)"),
        ("pagine", "Collaboratori e competitor", "le pagine dei collaboratori (report) e dei competitor (studio)"),
        ("aggiungi", "Aggiungi un collaboratore (es. /aggiungi @pagina)", "pagina autorizzata: entra nel report del mattino"),
        ("studia", "Aggiungi un competitor da studiare", "solo per imparare temi, hook, strutture e CTA: mai nel report"),
        ("rimuovi", "Togli una pagina", "la toglie sia dai collaboratori sia dai competitor"),
    ]),
    ("Controllo", [
        ("diagnosi", "Manda subito a Claude gli errori (di solito non serve)",
         "gli errori e le segnalazioni partono già da soli; usalo solo per mandarli di nuovo"),
        ("id", "Il tuo ID Telegram", "serve per dare accesso al bot a un'altra persona"),
    ]),
]

HELP = (
    "GUIDA RAPIDA (quello che ti serve ogni giorno)\n"
    "1. Alle 8 ti arriva il report con i post migliori dei collaboratori: mandami i link che ti piacciono.\n"
    "2. Alle 8:30 ti arrivano 2 caroselli creati da zero.\n"
    "3. Per correggere qualcosa rispondi al post: \"slide 3 più corta\", \"rifai la CTA con la casella\".\n"
    "4. Se una slide è sbagliata: rispondi alla slide con \"segnala: ...\" e Claude la corregge.\n"
    "Tutto il resto (studio della pagina, trend, correzioni) il bot lo fa da solo di notte.\n\n"
    "COSA PUOI FARE\n\n"
    "• Mandami il link di un post dei collaboratori: te lo restituisco pronto per AttraiSeduci "
    "(slide in italiano, logo, descrizione e CTA). Puoi mandare più link insieme.\n"
    "• Se Instagram blocca il link, mandami le slide come album (meglio come File) con la descrizione originale.\n"
    "• Per modificare un post rispondi ai suoi messaggi con cosa cambiare, es. \"slide 2 più corta\", "
    "\"cambia l'hook\", \"metti CTA al manuale Potere Sociale\", \"rifai la grafica della slide 4\".\n"
    "• regola: ...  → una regola fissa che il bot segue sempre.\n"
    "• segnala: ...  (rispondendo alla slide sbagliata) → il post parte subito verso Claude, che lo corregge "
    "e lo aggiunge alle prove fisse.\n"
    "• Slide CTA finale tua: mandami l'immagine con didascalia \"cta segui\" (o \"cta friendzone\"...).\n\n"
    "COMANDI (toccali per usarli, oppure scrivi / per il menu)\n"
    + "\n".join(f"\n{group.upper()}\n" + "\n".join(f"/{c} – {long}" for c, _short, long in cmds)
                for group, cmds in COMMANDS)
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


def _handles(args: list[str]) -> list[str]:
    import re as _re
    out = []
    for a in args:
        m = _re.search(r"(?:instagram\.com/)?@?([A-Za-z0-9._]{2,30})/?$", a.strip().rstrip("/"))
        if m:
            out.append(m.group(1).lower())
    return out


async def cmd_pagine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    brand = writer.load_brand()
    own = (brand.get("handle") or "").lower()
    pages = [h for h in brand.get("pagine_monitorate") or [] if h and h != own]
    study = [h for h in brand.get("pagine_studio") or [] if h]
    cfg = brand.get("report") or {}
    text = ("COLLABORATORI (report del mattino, post da ripubblicare):\n"
            + ("\n".join(f"@{h}" for h in pages) or "nessuna")
            + "\n\nCOMPETITOR DA STUDIARE (solo per imparare, mai nel report):\n"
            + ("\n".join(f"@{h}" for h in study) or "nessuna")
            + f"\n\nReport ogni giorno alle {cfg.get('ora', '08:00')}, ultime {cfg.get('finestra_ore', 48)} ore."
            + "\n\nPer cambiare: /aggiungi @pagina (collaboratore), /studia @pagina (competitor), /rimuovi @pagina")
    await update.effective_message.reply_text(text)


async def _change_pages(update, context, kind: str):
    if not await _authorized(update):
        return
    hs = _handles(context.args or [])
    if not hs:
        esempio = {"monitorate": "/aggiungi @pagina", "studio": "/studia @pagina", "rimosse": "/rimuovi @pagina"}[kind]
        await update.effective_message.reply_text(f"Scrivi il nome della pagina, es. {esempio} (anche più di una).")
        return
    ch = writer.load_page_changes()
    for k in ("monitorate", "studio", "rimosse"):
        ch[k] = [h for h in ch.get(k) or [] if h not in hs]
    if kind != "rimosse":
        ch[kind] = list(dict.fromkeys((ch.get(kind) or []) + hs))
    else:
        ch["rimosse"] = list(dict.fromkeys((ch.get("rimosse") or []) + hs))
    writer.save_page_changes(ch)
    nomi = ", ".join("@" + h for h in hs)
    msg = {"monitorate": f"Aggiunti ai collaboratori: {nomi}. Da domattina sono nel report.",
           "studio": f"Aggiunti ai competitor da studiare: {nomi}. Entrano nel prossimo studio settimanale "
                     "(temi, hook, strutture, CTA); i loro post non finiscono mai nel report.",
           "rimosse": f"Tolti: {nomi}."}[kind]
    await update.effective_message.reply_text(msg)


async def cmd_aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _change_pages(update, context, "monitorate")


async def cmd_studia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _change_pages(update, context, "studio")


async def cmd_rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _change_pages(update, context, "rimosse")


async def cmd_analizza(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        return
    msg = update.effective_message
    status = await msg.reply_text("Studio i post di AttraiSeduci: leggo gli ultimi 80 e confronto i migliori con i peggiori…")

    async def go():
        try:
            style = await asyncio.to_thread(analysis.run_analysis, writer.load_brand())
            cta_txt = "\n".join(f"• {c['rendimento']}x · {c['testo'][:140]}" for c in style.get("cta_migliori", [])[:5])
            slides_txt = ", ".join(style.get("slide_cta", {}).keys()) or (
                "nessuna trovata" + (f" ({style['slide_cta_motivo']})" if style.get("slide_cta_motivo") else ""))
            await status.edit_text(
                f"Studio fatto su {style['post_analizzati']} post. Da ora scrivo le descrizioni con questa guida "
                f"e {len(style['esempi'])} esempi tuoi:\n\n{style['guida']}"[:2500]
                + f"\n\nCTA che rendono di più:\n{cta_txt or '(nessuna)'}"
                + f"\n\n{style.get('lezioni_cta', '')}"[:800]
                + f"\n\nSlide CTA prese dai tuoi caroselli: {slides_txt}. Testi CTA letti dalle slide: "
                  f"{len(style.get('testi_slide_cta') or [])} (li vedi con /stile).")
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
    testi = style.get("testi_slide_cta") or []
    righe = "\n\n".join(f"{i}. [{t['tipo']}{' ' + t['manuale'] if t.get('manuale') else ''}]\n{t['testo']}"
                         for i, t in enumerate(testi, 1))
    await update.effective_message.reply_text(
        (f"Testi delle tue slide CTA che uso a fine post ({len(testi)}):\n\n{righe}" if testi else
         "Non ho ancora letto i testi delle tue slide CTA: lancia /analizza.")[:4000])
    st = style.get("cta_studio") or {}
    if st.get("lezioni"):
        schemi = "\n".join(f"• {x}" for x in st.get("schemi_aggancio") or [])
        await update.effective_message.reply_text(
            (f"Come {', '.join('@' + p for p in st.get('pagine') or [])} collegano la CTA al post "
             f"({st.get('post_studiati')} caroselli, {st.get('creato', '')[:10]}):\n\n{st['lezioni']}"
             + (f"\n\nSchemi di aggancio:\n{schemi}" if schemi else ""))[:4000])


async def _send_studio(bot, chat_ids: list[int], sv: dict) -> None:
    """Manda lo studio post per post: perché i post di competitor, collaboratori e nostri sono andati (o no)."""
    parti = analysis.schede_studio(sv)
    if not parti:
        return
    testa = (f"🧠 STUDIO DELLA NICCHIA ({len(sv.get('caroselli') or [])} post analizzati, {str(sv.get('creato', ''))[:10]})\n"
             "Per ogni post: perché è andato, dolore, gancio, struttura, formato e cosa ci portiamo a casa noi.")
    msgs, cur = [], testa
    for p_ in parti:
        if len(cur) + len(p_) + 2 > 3800:
            msgs.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + p_
    msgs.append(cur)
    for c in chat_ids:
        for m in msgs:
            try:
                await bot.send_message(c, m, disable_web_page_preview=True)
            except Exception as e:
                log.warning("Invio studio a %s fallito: %s", c, e)


async def cmd_studio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cosa ha imparato il bot dai caroselli virali: temi, hook, strutture, formati, regole."""
    if not await _authorized(update):
        return
    sv = writer.load_style().get("studio_virali") or {}
    if not sv.get("regole"):
        await update.effective_message.reply_text(
            "Lo studio dei caroselli virali non è ancora pronto: si fa da solo ogni settimana durante la notte.")
        return
    lista = lambda xs: "\n".join(f"• {x}" for x in xs or [])
    parti = [
        f"Studio dei caroselli virali ({len(sv.get('caroselli') or [])} caroselli di "
        f"{', '.join('@' + p for p in sv.get('pagine') or [])}, {sv.get('creato', '')[:10]})",
        "TEMI CHE RENDONO DI PIÙ\n" + lista(sv.get("temi")),
        "HOOK VINCENTI\n" + lista(sv.get("hook")),
        "STRUTTURE VINCENTI\n" + lista(sv.get("strutture")),
        "FORMATI\n" + (sv.get("formati") or ""),
        "REGOLE\n" + (sv.get("regole") or ""),
    ]
    from . import memoria
    t = memoria.trends()
    if t.get("post_in_memoria"):
        lst = lambda xs: "\n".join(f"• {x}" for x in xs) or "• (ancora pochi dati)"
        parti.append(
            f"TREND (memoria di {t['post_in_memoria']} caroselli studiati)\n"
            f"Formati in crescita:\n{lst(t.get('formati_in_crescita'))}\n"
            f"Temi in crescita:\n{lst(t.get('temi_in_crescita'))}\n"
            f"Cosa spinge i virali: {', '.join(t.get('cosa_spinge') or []) or '-'}\n\n"
            f"I NOSTRI POST\nFormati che rendono:\n{lst(t.get('nostri_formati_migliori'))}\n"
            f"Da non riproporre:\n{lst(t.get('nostri_formati_peggiori'))}\n"
            f"Perché no:\n{lst(t.get('perche_no'))}")
    for p_ in parti:
        await update.effective_message.reply_text(p_[:4000])
    await _send_studio(context.bot, [update.effective_chat.id], sv)


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


async def _crea_one(bot, chat_id: int, user_id: int, hint: str, label: str = "") -> None:
    status = await bot.send_message(chat_id, f"Creo un carosello da zero{label}…")
    work = storage.start_work(chat_id, "crea " + hint)
    try:
        brand = writer.load_brand()
        post, paths = await asyncio.to_thread(crea.make, brand, storage.list_rules(), storage.recent_style(), hint)
        job_id = storage.save_job(chat_id, user_id, "creato", brand.get("handle", ""), post, paths)
        pipeline.after_save(job_id, "creato", post, paths, [])
        await status.delete()
        idea = post.get("_idea") or {}
        header = (f"CAROSELLO ORIGINALE · {idea.get('tema', '')} · "
                  f"{'📚 didattico' if idea.get('natura') == 'didattico' else '🔥 virale'} · hook: {idea.get('tipo_hook', '')}"
                  f" · stile {(post.get('_look') or {}).get('stile', '')}, sfondo {(post.get('_look') or {}).get('palette', '').replace('_', ' ')}")
        await pipeline.send_post(bot, chat_id, job_id, post, paths, [], header=header)
    except Exception:
        log.exception("Carosello da zero non creato")
        await status.edit_text("Non sono riuscito a creare il carosello. Riprova tra poco.")
    finally:
        storage.end_work(work)


async def cmd_crea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/crea [quanti] [tema o indicazioni]: caroselli originali da zero, testo su nero, nello stile della pagina."""
    if not await _authorized(update):
        return
    args = list(context.args or [])
    n = 1
    if args and args[0].isdigit():
        n = max(1, min(5, int(args.pop(0))))
    hint = " ".join(args).strip()

    async def go():
        for k in range(n):
            await _crea_one(context.bot, update.effective_chat.id, update.effective_user.id, hint,
                            f" ({k + 1} di {n})" if n > 1 else "")
    context.application.create_task(go())


async def cmd_diagnosi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Carica subito su GitHub il rapporto degli ultimi 3 giorni (slide fallite e segnalazioni)."""
    if not await _authorized(update):
        return
    try:
        res = await asyncio.to_thread(diagnostica.upload_daily, 72)
    except Exception as e:
        log.exception("Rapporto non caricato")
        res = f"Rapporto non caricato: {str(e)[:200]}"
        res += "\n\n" + await asyncio.to_thread(diagnostica.token_check, e)
    await update.effective_message.reply_text(res)


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


async def _revise_created(update, context, job: dict, instruction: str, status):
    """Modifica di un carosello creato da zero: si rifanno solo le slide cambiate, nello stesso stile."""
    chat_id = update.effective_chat.id
    brand = writer.load_brand()
    old = job["post"]
    jdir = job["slide_paths"][0].parent
    new = await asyncio.to_thread(writer.revise_post, old, instruction, brand, storage.list_rules(),
                                  storage.recent_style())
    new["_originale"] = True
    for k in ("_look", "_idea"):
        if old.get(k):
            new.setdefault(k, old[k])
    n_main = len([s for s in old["slides"] if not s.get("e_cta")])
    cta_new = next((s.get("testo") for s in new["slides"] if s.get("e_cta")), None)
    if cta_new and cta_new.strip() != (old.get("_testo_cta_finale") or "").strip():
        new["_testo_cta_finale"] = cta_new
    changed = _changed_slides(old, new)
    if any(k in instruction.lower() for k in ("cta", "ultima slide")):
        changed.add(n_main + 1)
    paths = await asyncio.to_thread(crea.render_post, new, brand, jdir, changed or set())
    new["slide_da_rifare"] = []
    new["_fallite"] = []
    storage.update_job(job["id"], new, paths)
    await status.delete()
    nums = sorted(n for n in changed if 1 <= n <= len(paths))
    same_desc = (new.get("descrizione") or "").strip() == (old.get("descrizione") or "").strip()
    if nums:
        await pipeline.send_post(context.bot, chat_id, job["id"], new, [paths[n - 1] for n in nums], [],
                                 numbers=nums, con_descrizione=not same_desc)
    elif not same_desc:
        await pipeline.send_post(context.bot, chat_id, job["id"], new, paths, [], only_text=True)


async def process_revision(update: Update, context: ContextTypes.DEFAULT_TYPE, job: dict, instruction: str):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    status = await msg.reply_text("Applico la modifica…")
    if job["post"].get("_originale"):
        try:
            await _revise_created(update, context, job, instruction, status)
        except Exception:
            log.exception("Errore nella modifica del carosello creato %s", job["id"])
            await status.edit_text("Non sono riuscito ad applicare la modifica. Riprova riformulandola.")
        return
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
        # la NOSTRA slide CTA finale non si rifà partendo dall'originale (avrebbe i libri dell'altra pagina):
        # si ricrea con le nostre copertine, nello stile chiesto
        cta_i = next((i for i in range(len(old["slides"]) - 1, -1, -1)
                      if str(old["slides"][i].get("_via") or "").startswith("cta-")), None)
        low = instruction.lower()
        wants_cta = cta_i is not None and (
            (cta_i + 1) in changed
            or any(k in low for k in ("cta", "ultima slide", "libri", "copertin", "manual", "casella", "alone")))
        if wants_cta:
            changed.discard(cta_i + 1)
            if cta_i < len(new["slides"]) and new["slides"][cta_i].get("testo") and \
                    (new["slides"][cta_i].get("testo") or "").strip() != (old["slides"][cta_i].get("testo") or "").strip():
                new["_testo_cta_finale"] = new["slides"][cta_i]["testo"]
            new.setdefault("_testo_cta_finale", old.get("_testo_cta_finale"))
            style = ("alone" if any(k in low for k in ("casella", "alone", "riquadro", "box", "sfondo del post"))
                     else "libri" if any(k in low for k in ("libri", "uomo", "copertin", "personaggio"))
                     else ("libri" if old["slides"][cta_i].get("_via") == "cta-alone" else "alone"))
            await status.edit_text("Rifaccio la slide CTA con le nostre copertine…")
            r = await asyncio.to_thread(pipeline.illustrated_cta, {**new, "slides": old["slides"]}, jdir, style)
            if r:
                target, info = r
                info["numero"] = cta_i + 1
                all_slides = (new["slides"][:n_orig] + extra_slides)
                if cta_i < len(all_slides):
                    all_slides[cta_i] = info
                new["slides"] = all_slides[:n_orig]
                extra_slides = all_slides[n_orig:]
                paths = list(paths)
                paths[cta_i] = target
                if cta_i >= n_orig:
                    extra_paths = paths[n_orig:]
                await pipeline.send_post(context.bot, chat_id, job["id"], new, [target], [],
                                         numbers=[cta_i + 1], con_descrizione=False)
        if changed:
            await status.edit_text(f"Rifaccio le slide {', '.join(map(str, sorted(changed)))}…")
            new["slides"] = new["slides"][:n_orig]
            paths, new_failed = await images.render_all(new, originals, brand, jdir, only=changed,
                                                        existing=list(paths)[:n_orig])
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
            # se la descrizione non è cambiata, si rimandano solo le slide rifatte
            same_desc = (new.get("descrizione") or "").strip() == (old.get("descrizione") or "").strip()
            await pipeline.send_post(context.bot, chat_id, job["id"], new, [paths[i - 1] for i in nums],
                                     failed, numbers=nums, con_descrizione=not same_desc)
            m = await pipeline.send_canva(context.bot, chat_id, job["id"], new, paths, failed)
            if m:
                storage.link_message(chat_id, m.message_id, job["id"])
        elif not wants_cta or (new.get("descrizione") or "").strip() != (old.get("descrizione") or "").strip():
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
                             + ": la mando subito a Claude con le immagini del post. Stanotte la corregge "
                               "e la aggiunge alle prove fisse; domattina ti arriva il riepilogo.")

        async def invia():                     # niente /diagnosi: il post segnalato parte subito
            try:
                res = await asyncio.to_thread(diagnostica.upload_daily, 72)
                log.info("Segnalazione caricata: %s", res)
            except Exception:
                log.exception("Segnalazione non caricata subito (riprovo stanotte)")
        if diagnostica.enabled():
            context.application.create_task(invia())
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

def _schedule_crea(app: Application):
    """Ogni mattina N caroselli originali da zero (brand: crea.al_giorno, crea.ora)."""
    cfg = (writer.load_brand().get("crea") or {})
    n = int(cfg.get("al_giorno", 0) or 0)
    chat_ids = sorted(config.REPORT_CHAT_IDS or config.ALLOWED_USER_IDS)
    if n <= 0 or not cfg.get("ora") or not chat_ids:
        return
    tz = ZoneInfo((writer.load_brand().get("report") or {}).get("fuso", "Europe/Rome"))
    at = datetime.strptime(str(cfg["ora"]), "%H:%M").time().replace(tzinfo=tz)

    async def job(context: ContextTypes.DEFAULT_TYPE):
        for k in range(n):
            await _crea_one(context.bot, chat_ids[0], chat_ids[0], "", f" ({k + 1} di {n} di oggi)")

    app.job_queue.run_daily(job, time=at, name="crea_mattutino")
    log.info("Caroselli da zero programmati: %s ogni giorno alle %s", n, cfg["ora"])


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
    """Ogni notte rifà da solo lo studio della pagina (guida di stile, CTA migliori, testi delle slide CTA,
    risultati dei post del bot): /analizza non serve più. Il riepilogo arriva solo il lunedì, gli errori sempre.
    Se lo studio non è mai stato fatto, parte subito all'avvio."""
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
            log.exception("Studio della pagina fallito")
            for c in chat_ids:
                await context.bot.send_message(c, f"Studio settimanale non riuscito: {str(e)[:300]}")
            return
        if style.get("studio_virali_nuovo"):                     # ogni 3 giorni: il perché di ogni post studiato
            await _send_studio(context.bot, chat_ids, style.get("studio_virali") or {})
        if datetime.now(tz).weekday() != 0 and not (context.job.data or {}).get("primo"):
            return                                               # niente messaggi tutti i giorni
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

    app.job_queue.run_daily(job, time=at, name="studio_giornaliero")
    if "testi_slide_cta" not in writer.load_style():          # mai fatto, o fatto prima che leggesse le slide CTA
        app.job_queue.run_once(job, when=120, data={"primo": True}, name="primo_studio")

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


def _low_memory_restart():
    """Python su Linux tende a tenere la memoria già usata per le immagini: con MALLOC_ARENA_MAX=2
    la restituisce e il server (512 MB) non si riempie. Va impostato prima dell'avvio, quindi si riparte una volta."""
    import os
    import sys
    if os.environ.get("MALLOC_ARENA_MAX") is None:
        os.environ["MALLOC_ARENA_MAX"] = "2"
        os.execv(sys.executable, [sys.executable, "-m", "bot.main"])


def _cleanup_old_jobs(days: int = 10) -> int:
    """Cancella dal disco le cartelle dei post più vecchie di `days` giorni (il disco è da 1 GB)."""
    import shutil
    import time
    root = config.DATA_DIR / "jobs"
    if not root.exists():
        return 0
    limit, n = time.time() - days * 86400, 0
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except OSError:
            pass
    return n


def main():
    _low_memory_restart()
    missing = config.missing_keys()
    if missing:
        raise SystemExit(f"Mancano queste variabili d'ambiente: {', '.join(missing)}")
    if not config.ALLOWED_USER_IDS:
        log.warning("ALLOWED_USER_IDS è vuoto: il bot risponderà a tutti solo con il loro ID.")
    storage._db()
    async def _menu(application: Application):
        """Menu dei comandi che Telegram mostra scrivendo "/" o col pulsante Menu."""
        from telegram import BotCommand
        try:
            await application.bot.set_my_commands(
                [BotCommand(c, short) for _g, cmds in COMMANDS for c, short, _l in cmds])
        except Exception:
            log.exception("Menu comandi non impostato")

    app = (Application.builder().token(config.TELEGRAM_BOT_TOKEN).concurrent_updates(True)
           .post_init(_menu).build())
    app.add_handler(CommandHandler(["start", "aiuto", "help", "comandi"], cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("regole", cmd_regole))
    app.add_handler(CommandHandler("togli", cmd_togli))
    app.add_handler(CommandHandler("pagine", cmd_pagine))
    app.add_handler(CommandHandler("aggiungi", cmd_aggiungi))
    app.add_handler(CommandHandler("studia", cmd_studia))
    app.add_handler(CommandHandler("rimuovi", cmd_rimuovi))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("diagnosi", cmd_diagnosi))
    app.add_handler(CommandHandler("altri", cmd_altri))
    app.add_handler(CommandHandler("statistiche", cmd_statistiche))
    app.add_handler(CommandHandler("analizza", cmd_analizza))
    app.add_handler(CommandHandler("stile", cmd_stile))
    app.add_handler(CommandHandler("studio", cmd_studio))
    app.add_handler(CommandHandler("crea", cmd_crea))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    async def notify_restart(context: ContextTypes.DEFAULT_TYPE):
        for w in storage.pop_unfinished():
            try:
                await context.bot.send_message(
                    w["chat_id"], f"Il bot si è riavviato (aggiornamento o memoria del server piena) mentre lavorava: "
                                  f"rimandami {w['descrizione']}.")
            except Exception:
                log.warning("Avviso di riavvio non inviato a %s", w["chat_id"])

    app.job_queue.run_once(notify_restart, when=5, name="avviso_riavvio")

    async def cleanup(context: ContextTypes.DEFAULT_TYPE):
        n = await asyncio.to_thread(_cleanup_old_jobs)
        if n:
            log.info("Pulizia disco: cancellate %s cartelle di post vecchi", n)
    app.job_queue.run_repeating(cleanup, interval=86400, first=60, name="pulizia_disco")
    _schedule_report(app)
    _schedule_crea(app)
    _schedule_analysis(app)
    _schedule_diagnostics(app)
    log.info("Bot avviato.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
