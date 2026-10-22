"""Rapporto notturno per il miglioramento automatico.

Durante il giorno il bot registra:
- le slide non riuscite (originale, ultimo tentativo, motivo, come è stata lavorata);
- le segnalazioni di Gabriele scritte in chat con "segnala: ..." (riferite all'ultimo post o al post a cui rispondi).

Ogni notte carica tutto su GitHub, nel ramo "diagnostica" (non tocca il codice e non riavvia il bot).
Claude, con un'attività programmata, legge il rapporto, corregge il codice sul ramo principale e ti manda un riepilogo.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image

from . import config, storage

log = logging.getLogger(__name__)
API = "https://api.github.com"


# ---------------------------------------------------------------- registrazione

def record_post(job_id: int, source_url: str, post: dict, paths: list[Path], failed: list[int]) -> None:
    slides = []
    for i, s in enumerate(post.get("slides", []), 1):
        slides.append({
            "numero": i,
            "via": s.get("_via"),
            "tentativi": s.get("_tentativi"),
            "fallita": i in failed,
            "problema": s.get("_problema", ""),
            "testo_originale": s.get("testo_originale", "")[:500],
            "testo": s.get("testo", "")[:500],
            "blocchi": [{k: b.get(k) for k in ("x", "y", "w", "h", "carattere", "colore", "colore_evidenziato", "allineamento")}
                        for b in (s.get("blocchi") or [])],
        })
    storage.save_diag("post", job_id, {
        "fonte": source_url, "fallite": failed, "slides": slides,
        "concetto": post.get("concetto", ""), "descrizione": post.get("descrizione", "")[:2500],
        "cartella": str(paths[0].parent) if paths else "",
        "file": [str(p) for p in paths],
    })


def record_report(job_id: int | None, text: str, user_id: int) -> None:
    storage.save_diag("segnalazione", job_id, {"testo": text, "utente": user_id})


# ---------------------------------------------------------------- caricamento su GitHub

def enabled() -> bool:
    return bool(config.GITHUB_TOKEN and config.GITHUB_REPO)


def _h() -> dict:
    return {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def _ensure_branch(http: httpx.Client) -> None:
    r = http.get(f"{API}/repos/{config.GITHUB_REPO}/git/ref/heads/{config.DIAG_BRANCH}", headers=_h())
    if r.status_code == 200:
        return
    main = http.get(f"{API}/repos/{config.GITHUB_REPO}/git/ref/heads/main", headers=_h()).json()
    http.post(f"{API}/repos/{config.GITHUB_REPO}/git/refs", headers=_h(),
              json={"ref": f"refs/heads/{config.DIAG_BRANCH}", "sha": main["object"]["sha"]}).raise_for_status()


def _put(http: httpx.Client, path: str, data: bytes, message: str) -> None:
    url = f"{API}/repos/{config.GITHUB_REPO}/contents/{path}"
    sha = None
    r = http.get(url, headers=_h(), params={"ref": config.DIAG_BRANCH})
    if r.status_code == 200:
        sha = r.json().get("sha")
    body = {"message": message, "content": base64.b64encode(data).decode(), "branch": config.DIAG_BRANCH}
    if sha:
        body["sha"] = sha
    http.put(url, headers=_h(), json=body).raise_for_status()


def _small_jpeg(path: Path, width: int = 540) -> bytes | None:
    try:
        im = Image.open(path).convert("RGB")
        im.thumbnail((width, width * 2))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=82)
        return b.getvalue()
    except Exception:
        return None


def upload_daily(hours: int = 24) -> str:
    """Carica il rapporto delle ultime ore. Restituisce una riga di riepilogo."""
    if not enabled():
        return "Rapporto non caricato: manca GITHUB_TOKEN su Render."
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = storage.list_diag(since)
    posts = [r for r in rows if r["tipo"] == "post"]
    reports = [r for r in rows if r["tipo"] == "segnalazione"]
    problem_posts = [r for r in posts if r["dati"]["fallite"]]
    reported_jobs = {r["job_id"] for r in reports if r["job_id"]}
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = f"diagnostica/{day}"
    summary = {
        "giorno": day,
        "post_preparati": len(posts),
        "slide_totali": sum(len(r["dati"]["slides"]) for r in posts),
        "slide_fallite": sum(len(r["dati"]["fallite"]) for r in posts),
        "lavorazione": {},
        "segnalazioni": [{"job_id": r["job_id"], "testo": r["dati"]["testo"], "quando": r["creato"]} for r in reports],
        "post_con_problemi": [],
    }
    for r in posts:
        for s in r["dati"]["slides"]:
            v = s.get("via") or "?"
            summary["lavorazione"].setdefault(v, {"totali": 0, "fallite": 0})
            summary["lavorazione"][v]["totali"] += 1
            summary["lavorazione"][v]["fallite"] += int(bool(s.get("fallita")))
    with httpx.Client(timeout=60) as http:
        _ensure_branch(http)
        for r in posts:
            if not (r["dati"]["fallite"] or r["job_id"] in reported_jobs):
                continue
            jid = r["job_id"]
            folder = Path(r["dati"].get("cartella") or "")
            entry = {"job_id": jid, **{k: r["dati"][k] for k in ("fonte", "fallite", "slides", "concetto", "descrizione")},
                     "segnalazioni": [x["dati"]["testo"] for x in reports if x["job_id"] == jid]}
            summary["post_con_problemi"].append(entry)
            # immagini: originale e risultato di ogni slide fallita (o di tutte, se il post è stato segnalato)
            nums = range(1, len(r["dati"]["slides"]) + 1) if jid in reported_jobs else r["dati"]["fallite"]
            files = r["dati"].get("file") or []
            for n in nums:
                orig = folder / "originali" / f"{n:02d}.jpg"
                out = Path(files[n - 1]) if n - 1 < len(files) else None
                for kind, p in (("originale", orig), ("risultato", out)):
                    if p and p.exists():
                        data = _small_jpeg(p)
                        if data:
                            _put(http, f"{base}/post_{jid}/slide_{n:02d}_{kind}.jpg", data, f"diagnostica {day} post {jid}")
        _put(http, f"{base}/rapporto.json", json.dumps(summary, ensure_ascii=False, indent=1).encode(),
             f"diagnostica {day}")
    return (f"Rapporto notturno caricato: {summary['post_preparati']} post, {summary['slide_fallite']} slide fallite, "
            f"{len(reports)} segnalazioni.")
