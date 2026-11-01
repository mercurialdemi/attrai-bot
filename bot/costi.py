"""Contatore della spesa Claude: ogni chiamata registra i token usati e il costo, per passaggio e per giorno.
Si vede con /costi. Il file sta nella cartella dati principale (vale per tutte le pagine insieme)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# dollari per milione di token: input, scrittura cache (5 min), lettura cache, output
PREZZI = {
    "opus": (4.0, 5.0, 0.20, 20.0),
    "sonnet": (2.0, 2.5, 0.10, 10.0),
    "haiku": (0.10, 0.125, 0.01, 0.50),
}
EURO_PER_DOLLARO = float(os.getenv("EURO_PER_DOLLARO", "0.92"))

# nomi leggibili dei passaggi (nome dello strumento -> cosa fa)
PASSAGGI = {
    "slide_tradotte": "traduzione slide",
    "slide_rilette": "rilettura slide",
    "descrizione_pronta": "descrizione e CTA",
    "esito_controllo": "controllo slide",
    "post_pronto": "correzioni ai post",
    "modifica_post": "correzioni ai post",
    "carosello_nuovo": "caroselli da zero",
    "serie_storie": "storie",
    "risposta_storia": "storie",
    "temi_storie": "storie",
    "post_in_tema": "report mattutino",
    "seleziona_post": "report mattutino",
    "carosello_studiato": "studio settimanale",
    "studio_pagina": "studio settimanale",
    "studio_virali": "studio settimanale",
    "slide_cta": "studio settimanale",
    "studio_cta": "studio settimanale",
    "cta_conversioni": "studio CTA",
}

_lock = threading.Lock()


def _file() -> Path:
    base = os.getenv("DATA_DIR", "").strip() or str(Path(__file__).resolve().parent.parent / "data")
    return Path(base) / "costi_claude.json"


def _famiglia(model: str) -> str:
    m = (model or "").lower()
    return next((k for k in PREZZI if k in m), "opus")


def costo(model: str, usage) -> float:
    """Costo in euro di una risposta, dai token che Anthropic dichiara."""
    p_in, p_w, p_r, p_out = PREZZI[_famiglia(model)]
    g = lambda k: int(getattr(usage, k, 0) or 0)
    usd = (g("input_tokens") * p_in + g("cache_creation_input_tokens") * p_w
           + g("cache_read_input_tokens") * p_r + g("output_tokens") * p_out) / 1e6
    return usd * EURO_PER_DOLLARO


def registra(strumento: str, model: str, usage) -> None:
    try:
        eur = costo(model, usage)
        passo = PASSAGGI.get(strumento, strumento)
        oggi = dt.date.today().isoformat()
        with _lock:
            f = _file()
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                d = {}
            g = d.setdefault(oggi, {})
            r = g.setdefault(passo, {"chiamate": 0, "euro": 0.0, "in": 0, "out": 0})
            r["chiamate"] += 1
            r["euro"] = round(r["euro"] + eur, 5)
            r["in"] += int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "cache_read_input_tokens", 0) or 0) \
                + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            r["out"] += int(getattr(usage, "output_tokens", 0) or 0)
            for k in sorted(d)[:-40]:          # si tengono gli ultimi 40 giorni
                d.pop(k, None)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(d, indent=1), encoding="utf-8")
        log.info("Claude %s (%s): %.3f €", passo, _famiglia(model), eur)
        _dopo_registrazione()
    except Exception as e:  # il contatore non deve mai bloccare il lavoro
        log.warning("Contatore costi non aggiornato: %s", e)


def riepilogo(giorni: int = 7) -> str:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return "Nessuna spesa registrata ancora (il contatore parte da adesso)."
    oggi = dt.date.today()
    righe = []
    for k in range(giorni):
        g = (oggi - dt.timedelta(days=k)).isoformat()
        if g not in d:
            continue
        tot = sum(v["euro"] for v in d[g].values())
        etichetta = "Oggi" if k == 0 else "Ieri" if k == 1 else g
        righe.append(f"{etichetta}: {tot:.2f} €")
        if k <= 1:
            for passo, v in sorted(d[g].items(), key=lambda x: -x[1]["euro"]):
                righe.append(f"  · {passo}: {v['euro']:.2f} € ({v['chiamate']} chiamate)")
    if not righe:
        return "Nessuna spesa registrata negli ultimi giorni."
    post = 0
    g = oggi.isoformat()
    if g in d and "descrizione e CTA" in d[g]:
        post = d[g]["descrizione e CTA"]["chiamate"]
    tot_oggi = sum(v["euro"] for v in d.get(g, {}).values())
    extra = f"\nPost preparati oggi: {post} (circa {tot_oggi / post:.2f} € a post, correzioni comprese)" if post else ""
    return ("Spesa Claude (stima dai token):\n" + "\n".join(righe) + extra
            + f"\nTetto giornaliero: {tetto():.2f} € (si cambia con /tetto)")


# ---------------------------------------------------------------- tetto di spesa giornaliero

class TettoSpesa(RuntimeError):
    """Tetto di spesa del giorno raggiunto: il bot non chiama più Claude fino a domani (o finché si alza il tetto)."""


avvisa = None          # funzione impostata all'avvio (manda un messaggio Telegram ai soci)
_avvisati: dict[str, set] = {}


def _tetto_file() -> Path:
    return _file().with_name("tetto_claude.json")


def tetto() -> float:
    try:
        return float(json.loads(_tetto_file().read_text(encoding="utf-8"))["euro"])
    except Exception:
        return float(os.getenv("CLAUDE_TETTO_GIORNO", "8"))


def imposta_tetto(euro: float) -> None:
    _tetto_file().parent.mkdir(parents=True, exist_ok=True)
    _tetto_file().write_text(json.dumps({"euro": round(float(euro), 2)}), encoding="utf-8")


def speso_oggi() -> float:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    return sum(v["euro"] for v in d.get(dt.date.today().isoformat(), {}).values())


def _avvisa_una_volta(chiave: str, testo: str) -> None:
    oggi = dt.date.today().isoformat()
    fatti = _avvisati.setdefault(oggi, set())
    if chiave in fatti or avvisa is None:
        return
    fatti.add(chiave)
    try:
        avvisa(testo)
    except Exception as e:
        log.warning("Avviso spesa non mandato: %s", e)


def controlla(strumento: str) -> None:
    """Prima di ogni chiamata a Claude: se il tetto del giorno è raggiunto, si ferma."""
    t, s = tetto(), speso_oggi()
    if t > 0 and s >= t:
        _avvisa_una_volta("100", f"⛔ Tetto di spesa Claude raggiunto: oggi {s:.2f} € su {t:.2f} €. "
                                 "Il bot si ferma fino a domani. Per continuare oggi alza il tetto con /tetto (es. /tetto 12).")
        raise TettoSpesa(f"Tetto di spesa di oggi raggiunto ({s:.2f} € su {t:.2f} €). "
                         "Riparte domani, oppure alza il tetto con /tetto.")


def _dopo_registrazione() -> None:
    t, s = tetto(), speso_oggi()
    if t > 0 and s >= 0.8 * t:
        _avvisa_una_volta("80", f"⚠️ Spesa Claude di oggi: {s:.2f} € su un tetto di {t:.2f} €. "
                                "Al tetto il bot si ferma fino a domani. Dettaglio con /costi.")
