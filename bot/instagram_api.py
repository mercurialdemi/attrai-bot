"""Statistiche vere della nostra pagina tramite l'API ufficiale di Instagram (Instagram Login).

Serve la variabile INSTAGRAM_TOKEN (chiave a lunga durata generata dall'app Meta).
La chiave dura 60 giorni: il bot la rinnova da solo ogni settimana e la salva su disco.

Per ogni post: copertura, visualizzazioni, like, commenti, salvataggi, condivisioni, nuovi follower,
visite al profilo e clic sul link in bio.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from . import config

log = logging.getLogger(__name__)

BASE = "https://graph.instagram.com"
METRICS = ["reach", "views", "likes", "comments", "saved", "shares", "follows", "profile_visits"]


class ApiError(Exception):
    pass


def _token_file() -> Path:
    return config.DATA_DIR / "instagram_token.txt"


def token() -> str:
    """La chiave rinnovata (salvata su disco) ha la precedenza su quella iniziale messa su Render."""
    f = _token_file()
    if f.exists():
        t = f.read_text().strip()
        if t:
            return t
    return config.INSTAGRAM_TOKEN


def enabled() -> bool:
    return bool(token())


def _get(path: str, params: dict) -> dict:
    params = {**params, "access_token": token()}
    with httpx.Client(timeout=40) as http:
        r = http.get(f"{BASE}/{config.IG_API_VERSION}/{path.lstrip('/')}", params=params)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "error" in data:
        raise ApiError((data.get("error") or {}).get("message") or f"HTTP {r.status_code}")
    return data


def refresh_token() -> bool:
    """Rinnova la chiave (valida altri 60 giorni)."""
    if not token():
        return False
    with httpx.Client(timeout=30) as http:
        r = http.get(f"{BASE}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token()})
    data = r.json() if r.status_code == 200 else {}
    new = data.get("access_token")
    if new:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _token_file().write_text(new)
        return True
    log.warning("Rinnovo chiave Instagram non riuscito: %s", r.text[:300])
    return False


def account() -> dict:
    return _get("me", {"fields": "user_id,username,followers_count,media_count"})


def recent_media(limit: int = 80) -> list[dict]:
    out, params = [], {"fields": "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count",
                       "limit": 50}
    path = "me/media"
    while len(out) < limit:
        data = _get(path, params)
        out += data.get("data", [])
        nxt = (data.get("paging") or {}).get("cursors", {}).get("after")
        if not nxt or not data.get("data"):
            break
        params = {**params, "after": nxt}
    return out[:limit]


def media_insights(media_id: str) -> dict:
    """Statistiche di un post. I valori che Instagram non fornisce per quel tipo di post restano assenti."""
    res: dict = {}
    try:
        data = _get(f"{media_id}/insights", {"metric": ",".join(METRICS), "period": "lifetime"})
        for m in data.get("data", []):
            vals = m.get("values") or [{}]
            res[m["name"]] = (m.get("total_value") or {}).get("value", vals[0].get("value"))
    except ApiError:
        for metric in METRICS:          # alcuni tipi di post non accettano tutte le metriche insieme
            try:
                d = _get(f"{media_id}/insights", {"metric": metric, "period": "lifetime"})
                for m in d.get("data", []):
                    vals = m.get("values") or [{}]
                    res[m["name"]] = (m.get("total_value") or {}).get("value", vals[0].get("value"))
            except ApiError:
                pass
    try:
        d = _get(f"{media_id}/insights", {"metric": "profile_activity", "breakdown": "action_type", "period": "lifetime"})
        for m in d.get("data", []):
            for br in ((m.get("total_value") or {}).get("breakdowns") or []):
                for r in br.get("results", []):
                    if "bio_link" in str(r.get("dimension_values", "")).lower():
                        res["clic_link"] = r.get("value", 0)
        res.setdefault("clic_link", 0)
    except ApiError:
        pass
    return res


def shortcode(permalink: str) -> str:
    parts = [p for p in (permalink or "").rstrip("/").split("/") if p]
    return parts[-1] if parts else ""


def stats_by_shortcode(limit: int = 80) -> dict[str, dict]:
    """{codice del post: statistiche} per gli ultimi post della pagina."""
    out = {}
    for m in recent_media(limit):
        try:
            ins = media_insights(m["id"])
        except Exception as e:
            log.warning("Statistiche di %s non lette: %s", m.get("permalink"), e)
            continue
        ins.setdefault("likes", m.get("like_count"))
        ins.setdefault("comments", m.get("comments_count"))
        out[shortcode(m.get("permalink", ""))] = ins
    return out


def score(s: dict) -> float | None:
    """Rendimento di un post: pesa di più le azioni che contano per la crescita e le vendite."""
    if not s or not s.get("reach"):
        return None
    return (
        (s.get("likes") or 0)
        + 2 * (s.get("comments") or 0)
        + 3 * (s.get("saved") or 0)
        + 4 * (s.get("shares") or 0)
        + 6 * (s.get("follows") or 0)
        + 8 * (s.get("clic_link") or 0)
    )
