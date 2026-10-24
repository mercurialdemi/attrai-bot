"""Lettura dei post Instagram tramite Apify e download delle slide."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx
from apify_client import ApifyClient

from . import config

log = logging.getLogger(__name__)

# Accetta: instagram.com/p/CODICE, /reel/CODICE, /tv/CODICE, anche con /nomeutente/ davanti
POST_RE = re.compile(
    r"https?://(?:www\.|m\.)?instagram\.com/(?:[A-Za-z0-9._]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


class InstagramError(Exception):
    """Errore mostrabile all'utente."""


@dataclass
class SourcePost:
    url: str
    owner: str
    caption: str
    kind: str                      # Image | Sidecar | Video
    image_urls: list[str] = field(default_factory=list)
    likes: int | None = None
    comments: int | None = None
    timestamp: str = ""            # ISO, ora di pubblicazione
    score: float = 0.0             # rendimento rispetto alla media della pagina (report)
    motivo: str = ""               # perché è stato scelto (report)
    natura: str = ""               # didattico | virale (report)
    musica: str = ""               # brano usato nel post originale ("Artista – Titolo"), se Instagram lo indica

    @property
    def is_video(self) -> bool:
        return self.kind.lower() == "video"


def find_post_url(text: str) -> str | None:
    m = POST_RE.search(text or "")
    if not m:
        return None
    kind = "reel" if m.group(1).lower() in ("reel", "reels") else m.group(1).lower()
    return f"https://www.instagram.com/{kind}/{m.group(2)}/"


def _dedupe(urls: list[str]) -> list[str]:
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _music(it) -> str:
    """Cerca nei dati del post il brano usato (i nomi dei campi cambiano a seconda di come Instagram li dà)."""
    found = []

    def walk(o, depth=0):
        if depth > 6 or found:
            return
        if isinstance(o, dict):
            keys = {k.lower(): k for k in o}
            artist = next((o[keys[k]] for k in ("artist_name", "artistname", "artist", "display_artist") if k in keys
                           and isinstance(o[keys[k]], str) and o[keys[k]].strip()), "")
            song = next((o[keys[k]] for k in ("song_name", "songname", "title", "song_title", "audio_title") if k in keys
                         and isinstance(o[keys[k]], str) and o[keys[k]].strip()), "")
            if song and (artist or any(m in " ".join(keys) for m in ("music", "audio", "song"))):
                found.append(f"{artist} – {song}" if artist else song)
                return
            for k, v in o.items():
                if any(m in k.lower() for m in ("music", "audio", "song", "clips")) or isinstance(v, (dict, list)):
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o[:20]:
                walk(v, depth + 1)

    for key in it:
        if any(m in key.lower() for m in ("music", "audio", "song", "clips")):
            walk(it[key])
    if not found:
        walk({k: v for k, v in it.items() if k not in ("childPosts", "latestComments")})
    return found[0][:120] if found else ""


def _parse_item(url: str, it: dict) -> SourcePost:
    images: list[str] = []
    for child in it.get("childPosts") or []:
        if (child.get("type") or "").lower() != "video":
            images.append(child.get("displayUrl") or child.get("imageUrl") or child.get("url") or "")
    if not images:
        images = list(it.get("images") or [])
    if not images and it.get("displayUrl"):
        images = [it["displayUrl"]]
    return SourcePost(
        url=url,
        owner=(it.get("ownerUsername") or "").lower(),
        caption=it.get("caption") or "",
        kind=it.get("type") or "Image",
        image_urls=_dedupe(images)[:20],
        likes=it.get("likesCount"),
        comments=it.get("commentsCount"),
        timestamp=it.get("timestamp") or "",
        musica=_music(it),
    )


def _run_login_actor(targets: list[str], limit: int = 30, newer_than: str | None = None) -> list[dict]:
    """Legge con l'accesso di un account (cookie): serve per pagine e post con limite d'età."""
    client = ApifyClient(config.APIFY_TOKEN)
    run_input = {
        "username": targets,
        "resultsLimit": limit,
        "dataDetailLevel": "detailedData",
        "latestCommentsLimit": 0,
        "cookies": config.INSTAGRAM_COOKIES,
    }
    if newer_than:
        run_input["onlyPostsNewerThan"] = newer_than
    run = client.actor(config.APIFY_LOGIN_ACTOR).call(run_input=run_input, timeout_secs=900)
    if not run:
        raise InstagramError("Apify non ha risposto (lettura con accesso).")
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


def fetch_post(url: str) -> SourcePost:
    """Scarica i dati di un singolo post (testo, autore, URL delle slide)."""
    client = ApifyClient(config.APIFY_TOKEN)
    run = client.actor(config.APIFY_ACTOR).call(
        run_input={
            "directUrls": [url],
            "resultsType": "posts",
            "resultsLimit": 1,
            "addParentData": False,
        },
        timeout_secs=240,
    )
    if not run:
        raise InstagramError("Apify non ha risposto. Riprova tra un minuto.")
    all_items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    items = [i for i in all_items if not i.get("error")]
    if not items and config.INSTAGRAM_COOKIES:
        log.info("Lettura normale bloccata, riprovo con l'accesso: %s", url)
        try:
            all_items = _run_login_actor([url], 1)
            items = [i for i in all_items if not i.get("error") and (i.get("ownerUsername") or i.get("caption") is not None)]
        except Exception as e:
            log.warning("Lettura con accesso fallita: %s", e)
    if not items:
        errs = [i for i in all_items if i.get("error")]
        if any(e.get("error") == "restricted_page" for e in errs):
            raise InstagramError(
                "Instagram non mi fa leggere questo post: la pagina ha un limite d'età e i contenuti si vedono solo "
                "dopo il login." + ("" if config.INSTAGRAM_COOKIES else
                " Per leggerli inserisci su Render la variabile INSTAGRAM_COOKIES (vedi la guida).")
                + "\n\nIntanto puoi mandarmi direttamente le slide come album (meglio come File), "
                "con la descrizione originale nella didascalia.")
        if errs:
            raise InstagramError(f"Instagram ha rifiutato la lettura del post ({errs[0].get('errorDescription') or errs[0].get('error')}).")
        raise InstagramError("Non riesco a leggere il post: è pubblico e il link è giusto?")
    post = _parse_item(url, items[0])
    if not post.owner:
        raise InstagramError("Non riesco a capire di quale pagina è il post.")
    if not post.image_urls:
        raise InstagramError("Il post non ha immagini leggibili.")
    return post


def download_images(urls: list[str]) -> list[bytes]:
    """Scarica le slide alla massima qualità che Instagram fornisce."""
    out: list[bytes] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AttraiBot/1.0)"}
    with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as http:
        for u in urls:
            try:
                r = http.get(u)
                r.raise_for_status()
                out.append(r.content)
            except httpx.HTTPError as e:
                log.warning("Download slide fallito %s: %s", u[:80], e)
    if not out:
        raise InstagramError("Non sono riuscito a scaricare le slide del post.")
    return out


def fetch_profiles(handles: list[str], per_page: int = 30) -> list[SourcePost]:
    """Ultimi post di più pagine in una sola esecuzione Apify (più veloce ed economico)."""
    client = ApifyClient(config.APIFY_TOKEN)
    run = client.actor(config.APIFY_ACTOR).call(
        run_input={
            "directUrls": [f"https://www.instagram.com/{h}/" for h in handles],
            "resultsType": "posts",
            "resultsLimit": per_page,
            "addParentData": False,
        },
        timeout_secs=900,
    )
    if not run:
        raise InstagramError("Apify non ha risposto durante il report.")
    out: list[SourcePost] = []
    for it in client.dataset(run["defaultDatasetId"]).iterate_items():
        if it.get("error"):
            continue
        url = find_post_url(it.get("url") or "") or (
            f"https://www.instagram.com/p/{it['shortCode']}/" if it.get("shortCode") else None)
        if not url:
            continue
        out.append(_parse_item(url, it))
    missing = [h for h in handles if h not in {p.owner for p in out}]
    if missing and config.INSTAGRAM_COOKIES:
        log.info("Pagine non lette, riprovo con l'accesso: %s", missing)
        try:
            for it in _run_login_actor([f"https://www.instagram.com/{h}/" for h in missing], per_page):
                if it.get("error"):
                    continue
                url = find_post_url(it.get("url") or "") or (
                    f"https://www.instagram.com/p/{it['shortCode']}/" if it.get("shortCode") else None)
                if url:
                    out.append(_parse_item(url, it))
        except Exception as e:
            log.warning("Lettura con accesso fallita: %s", e)
    return out
