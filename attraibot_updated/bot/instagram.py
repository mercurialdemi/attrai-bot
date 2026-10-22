"""Lettura dei post Instagram tramite Apify e download delle slide."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx
from apify_client import ApifyClient

from . import config

log = logging.getLogger(__name__)

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
    kind: str
    image_urls: list[str] = field(default_factory=list)
    likes: int | None = None
    comments: int | None = None

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


def _parse_item(url: str, it: dict) -> SourcePost:
    images: list[str] = []
    for child in it.get("childPosts") or []:
        if (child.get("type") or "").lower() != "video":
            images.append(child.get("displayUrl") or "")
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
    )


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
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    items = [i for i in items if not i.get("error")]
    if not items:
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