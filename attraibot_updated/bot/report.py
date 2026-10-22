"""Report mattutino: analizza i post recenti delle pagine monitorate,
seleziona i migliori 5 in linea con nicchia/tono/posizionamento e li prepara già pronti."""
from __future__ import annotations

import asyncio
import logging
import re
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
from apify_client import ApifyClient

from . import config, images, instagram, storage, writer

log = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


SELECT_TOOL = {
    "name": "seleziona_post",
    "description": "Seleziona i migliori post da cui prendere spunto per AttraiSeduci.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scelti": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "url": {"type": "string"},
                        "motivo": {"type": "string", "description": "Perché è in linea con nicchia/tono/posizionamento."},
                    },
                    "required": ["owner", "url", "motivo"],
                },
            },
        },
        "required": ["scelti"],
    },
}


def _fetch_recent_sync(handles: list[str], hours: int = 24) -> list[instagram.SourcePost]:
    """Scarica i post recenti delle pagine monitorate (sincrono, da chiamare in thread)."""
    client_apify = ApifyClient(config.APIFY_TOKEN)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    posts: list[instagram.SourcePost] = []

    for handle in handles:
        try:
            run = client_apify.actor(config.APIFY_ACTOR).call(
                run_input={
                    "directUrls": [f"https://www.instagram.com/{handle}/"],
                    "resultsType": "posts",
                    "resultsLimit": 20,
                    "onlyPostsNewerThan": since.isoformat(),
                },
                timeout_secs=300,
            )
            if not run:
                continue
            items = list(client_apify.dataset(run["defaultDatasetId"]).iterate_items())
            for it in items:
                if it.get("error"):
                    continue
                shortcode = it.get("shortCode") or it.get("shortcode") or it.get("code")
                post_url = it.get("url") or (f"https://www.instagram.com/p/{shortcode}/" if shortcode else "")
                if not instagram.find_post_url(post_url):
                    continue
                post = instagram._parse_item(post_url, it)
                if post.image_urls and not post.is_video:
                    posts.append(post)
        except Exception as e:
            log.warning("Fetch %s fallito: %s", handle, e)

    return posts


def _select_best_sync(posts: list[instagram.SourcePost], brand: dict, quanti: int) -> list[instagram.SourcePost]:
    """Usa Claude per selezionare i migliori post in linea con nicchia/tono."""
    if not posts:
        return []

    riassunto = []
    for p in posts:
        riassunto.append({
            "owner": p.owner,
            "url": p.url,
            "caption": (p.caption or "")[:600],
            "likes": p.likes,
            "comments": p.comments,
            "kind": p.kind,
        })

    system = f"""Sei il copywriter senior di {brand['nome']} (@{brand['handle']}).
Devi selezionare i migliori post da cui prendere spunto per la nostra pagina.

POSIZIONAMENTO
{brand.get('posizionamento', '').strip()}

TONO
{chr(10).join('- ' + t for t in brand.get('tono', []))}

REGOLE
{brand['hook']['regola'].strip()}
{brand['cta']['regola'].strip()}

Criteri di selezione:
- coerenza con posizionamento e nicchia (psicologia maschile, attrazione, seduzione)
- forza del dolore toccato
- potenziale virale o didattico
- originalità rispetto ai post già pubblicati (evita ripetizioni)
Seleziona esattamente {quanti} post. Per ognuno scrivi il motivo in una frase.
"""

    content = [{"type": "text", "text": (
        "Post recenti delle pagine monitorate (scegli solo URL esistenti nell’elenco):\n"
        f"{riassunto}\n\n"
        f"Seleziona i migliori {quanti} con lo strumento seleziona_post."
    )}]

    resp = client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2000,
        system=system,
        tools=[SELECT_TOOL],
        tool_choice={"type": "tool", "name": "seleziona_post"},
        messages=[{"role": "user", "content": content}],
    )
    scelti_raw = []
    for block in resp.content:
        if block.type == "tool_use":
            scelti_raw = block.input.get("scelti", [])
            break

    by_url = {p.url: p for p in posts}
    scelti: list[instagram.SourcePost] = []
    for s in scelti_raw:
        p = by_url.get(s["url"])
        if p:
            p.motivo = s.get("motivo", "")
            scelti.append(p)
    return scelti[:quanti]


async def run_report(bot, chat_id: int, brand: dict):
    """Esegue il report: fetch, selezione, generazione e invio dei post pronti."""
    handles = brand.get("pagine_monitorate", [])
    if not handles:
        await bot.send_message(chat_id, "Nessuna pagina monitorata configurata in pagine_monitorate.")
        return

    cfg = brand.get("report", {})
    quanti = int(cfg.get("quanti_post", 5))
    candidati = max(quanti, int(cfg.get("candidati", 15)))
    ore = int(cfg.get("finestra_ore", 24))

    await bot.send_message(chat_id, f"Report mattutino: analizzo {len(handles)} pagine (ultime {ore}h)…")

    posts = await asyncio.to_thread(_fetch_recent_sync, handles, ore)
    if not posts:
        await bot.send_message(chat_id, "Nessun post recente trovato nelle pagine monitorate.")
        return

    # Classifica 15 candidati, poi prepara soltanto i 5 migliori.
    candidati_scelti = await asyncio.to_thread(_select_best_sync, posts, brand, min(candidati, len(posts)))
    scelti = candidati_scelti[:quanti]
    if candidati_scelti:
        elenco = "\n".join(f"{i}. {p.url}" for i, p in enumerate(candidati_scelti, 1))
        await bot.send_message(chat_id, f"Classifica dei {len(candidati_scelti)} candidati (ultime {ore} ore):\n{elenco}")
    if not scelti:
        await bot.send_message(chat_id, "Claude non ha selezionato post. Riprovo domani.")
        return

    await bot.send_message(chat_id, f"Selezionati {len(scelti)} post. Li preparo…")

    for i, src in enumerate(scelti, 1):
        try:
            await bot.send_message(
                chat_id,
                f"Post {i}/{len(scelti)}: @{src.owner}\nMotivo: {getattr(src, 'motivo', '')}\nPreparo…",
            )
            originals = await asyncio.to_thread(instagram.download_images, src.image_urls)
            post = await asyncio.to_thread(
                writer.create_post, src.caption, src.owner, originals, brand,
                storage.list_rules(), storage.recent_style(),
            )
            post["slides"] = post["slides"][:len(originals)]
            post["_motivo_report"] = getattr(src, "motivo", "")

            job_key = re.sub(r"\W", "", src.url.rstrip("/").split("/")[-1]) + f"_report_{i}"
            jdir = config.DATA_DIR / "jobs" / job_key
            (jdir / "originali").mkdir(parents=True, exist_ok=True)
            for k, data in enumerate(originals, 1):
                (jdir / "originali" / f"{k:02d}.jpg").write_bytes(data)

            paths, failed = await images.render_all(post, originals, brand, jdir)
            job_id = storage.save_job(chat_id, 0, src.url, src.owner, post, paths)
            await _send_report_post(bot, chat_id, job_id, post, paths, failed)
        except Exception:
            log.exception("Errore preparando post %s", src.url)
            await bot.send_message(chat_id, f"Post {i} fallito: {src.url}")


async def _send_report_post(bot, chat_id: int, job_id: int, post: dict, paths: list[Path], failed: list[int]):
    """Invia un post del report: slide + descrizione + motivo."""
    from telegram import InputMediaDocument

    sent = []
    for start in range(0, len(paths), 10):
        group = []
        for p in paths[start:start + 10]:
            group.append(InputMediaDocument(p.read_bytes(), filename=f"AttraiSeduci_{job_id}_{p.stem}.jpg"))
        sent += await bot.send_media_group(chat_id, group)

    motivo = post.get("_motivo_report", "")
    testo = f"{post['descrizione']}\n\n— Motivo selezione: {motivo}\n— CTA: {post['cta'].get('tipo')} {post['cta'].get('manuale', '')}"
    sent.append(await bot.send_message(chat_id, testo))

    for m in sent:
        storage.link_message(chat_id, m.message_id, job_id)