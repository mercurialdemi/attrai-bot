"""Più pagine con lo stesso bot: ogni pagina (AttraiSeduci, ResetRelazionale, Il Pensatore, AmoreInfedele...)
ha la sua scheda (brands/<pagina>.yaml), le sue CTA (brands/<pagina>_cta.yaml), il suo logo
(assets/loghi/<pagina>.png) e i suoi dati (report, studio, memoria, post) in una cartella tutta sua.

La pagina "corrente" vale per il messaggio o il lavoro in corso: nei gruppi Telegram collegati con /collega
è la pagina del gruppo, in chat privata è AttraiSeduci. Tutto il resto del codice legge config.DATA_DIR,
config.BRAND_FILE e config.LOGO_PATH, che puntano da soli alla pagina corrente.

AttraiSeduci (la pagina principale) usa gli stessi percorsi di sempre: nessun dato esistente si sposta.
"""
from __future__ import annotations

import contextvars
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRINCIPALE = os.getenv("PAGINA_PRINCIPALE", "attraiseduci").strip() or "attraiseduci"

_corrente: contextvars.ContextVar[str] = contextvars.ContextVar("pagina_corrente", default=PRINCIPALE)


def corrente() -> str:
    return _corrente.get()


def imposta(slug: str | None) -> None:
    """Imposta la pagina per il lavoro in corso (vale anche per i thread e i task avviati da qui)."""
    _corrente.set(slug if slug in elenco() else PRINCIPALE)


def _base_data() -> Path:
    return Path(os.getenv("DATA_DIR", str(ROOT / "data")).strip() or str(ROOT / "data"))


def _brands_dir() -> Path:
    env = os.getenv("BRAND_FILE", "").strip()
    return Path(env).parent if env else ROOT / "brands"


def elenco() -> list[str]:
    """Le pagine configurate (una scheda brands/<pagina>.yaml ciascuna), la principale per prima."""
    found = sorted(p.stem for p in _brands_dir().glob("*.yaml") if not p.stem.endswith("_cta"))
    return [PRINCIPALE] + [s for s in found if s != PRINCIPALE]


def data_dir(slug: str | None = None) -> Path:
    slug = slug or corrente()
    base = _base_data()
    return base if slug == PRINCIPALE else base / "pagine" / slug


def brand_file(slug: str | None = None) -> Path:
    slug = slug or corrente()
    env = os.getenv("BRAND_FILE", "").strip()
    if env and slug == PRINCIPALE:
        return Path(env)
    return _brands_dir() / f"{slug}.yaml"


def logo_path(slug: str | None = None) -> Path:
    slug = slug or corrente()
    env = os.getenv("LOGO_PATH", "").strip()
    if slug == PRINCIPALE:
        return Path(env) if env else ROOT / "assets" / "logo.png"
    return ROOT / "assets" / "loghi" / f"{slug}.png"


# ---------------------------------------------------------------- gruppi Telegram collegati alle pagine

def _chat_file() -> Path:
    return _base_data() / "pagine_chat.json"


def _chats() -> dict:
    try:
        return json.loads(_chat_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def collega(chat_id: int, slug: str) -> None:
    d = {k: v for k, v in _chats().items() if v != slug}       # un gruppo per pagina: il nuovo sostituisce il vecchio
    d[str(chat_id)] = slug
    _chat_file().parent.mkdir(parents=True, exist_ok=True)
    _chat_file().write_text(json.dumps(d, indent=1), encoding="utf-8")


def scollega(chat_id: int) -> str | None:
    d = _chats()
    slug = d.pop(str(chat_id), None)
    _chat_file().write_text(json.dumps(d, indent=1), encoding="utf-8")
    return slug


def di_chat(chat_id: int | None) -> str:
    """La pagina di una chat: quella del gruppo collegato, altrimenti la principale."""
    if chat_id is None:
        return PRINCIPALE
    slug = _chats().get(str(chat_id))
    return slug if slug in elenco() else PRINCIPALE


def gruppo_di(slug: str) -> int | None:
    """Il gruppo Telegram collegato a una pagina (None se non c'è)."""
    for k, v in _chats().items():
        if v == slug:
            try:
                return int(k)
            except ValueError:
                return None
    return None


def trova(testo: str) -> str | None:
    """Riconosce una pagina da come la scrivi: 'reset', '@resetrelazionale', 'Il Pensatore'..."""
    t = "".join(ch for ch in (testo or "").lower() if ch.isalnum())
    if not t:
        return None
    import yaml
    for slug in elenco():
        nomi = {slug}
        try:
            b = yaml.safe_load(brand_file(slug).read_text(encoding="utf-8")) or {}
            nomi |= {str(b.get("nome", "")), str(b.get("handle", ""))}
        except Exception:
            pass
        nomi = {"".join(ch for ch in n.lower() if ch.isalnum()) for n in nomi} - {""}
        if t in nomi or any(n.startswith(t) or t in n for n in nomi if len(t) >= 4):
            return slug
    return None
