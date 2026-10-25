"""Memoria dei contenuti: ogni carosello studiato (virali dei competitor e dei collaboratori, e i NOSTRI post,
sia quelli andati bene sia quelli andati male) resta salvato con:
formato narrativo (POV, prima persona, immersivo, elenco, dialogo...), tema, dolore, hook, struttura,
quanto ha reso rispetto alla sua pagina e soprattutto PERCHÉ ha funzionato o no (struttura, dolore forte,
tema caldo, formato, grafica...).

Da qui il bot ricava i TREND (formati e temi che stanno crescendo nelle ultime settimane rispetto a prima),
cosa funziona per noi e cosa non riproporre. /crea li usa ogni volta che inventa un carosello.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import datetime, timedelta, timezone

from . import config

log = logging.getLogger(__name__)

FORMATI = ["pov", "prima_persona", "immersivo", "elenco", "dialogo_scena", "verita_scomoda", "storia",
           "confronto", "domanda", "citazione", "tutorial", "altro"]
FATTORI = ["formato", "struttura", "dolore_forte", "tema_caldo", "hook", "grafica", "polemica", "identificazione",
           "altro"]


def _file():
    return config.DATA_DIR / "memoria_contenuti.json"


def load() -> list[dict]:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def save(items: list[dict]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(items[-1500:], ensure_ascii=False, indent=1), encoding="utf-8")


def add(entries: list[dict]) -> int:
    """Aggiunge o aggiorna (stesso url: si aggiorna il rendimento e l'analisi)."""
    items = load()
    by_url = {it.get("url"): i for i, it in enumerate(items) if it.get("url")}
    n = 0
    for e in entries:
        e.setdefault("studiato", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if e.get("url") in by_url:
            items[by_url[e["url"]]].update(e)
        else:
            items.append(e)
            n += 1
    save(items)
    return n


def _when(it: dict) -> datetime:
    for k in ("data_post", "studiato"):
        try:
            return datetime.fromisoformat(str(it.get(k)).replace("Z", "+00:00"))
        except Exception:
            continue
    return datetime.now(timezone.utc)


def _weight(it: dict, now: datetime, half_life_days: float = 21) -> float:
    """I post recenti contano di più: dopo 3 settimane valgono la metà (i trend cambiano)."""
    age = max(0.0, (now - _when(it)).total_seconds() / 86400)
    return 0.5 ** (age / half_life_days)


def trends(days_recent: int = 30) -> dict:
    """Formati e temi in crescita o in calo tra i post virali (ultimi 30 giorni contro i 60 prima),
    cosa rende per noi e cosa non riproporre."""
    items = load()
    if not items:
        return {}
    now = datetime.now(timezone.utc)
    cut = now - timedelta(days=days_recent)
    old_cut = now - timedelta(days=days_recent * 3)
    vir = [it for it in items if (it.get("rendimento") or 0) >= 1.5]

    def share(group, key):
        tot = len(group) or 1
        c: dict = {}
        for it in group:
            k = it.get(key) or "altro"
            c[k] = c.get(k, 0) + 1
        return {k: v / tot for k, v in c.items()}

    rec = [it for it in vir if _when(it) >= cut]
    prev = [it for it in vir if old_cut <= _when(it) < cut]
    out = {"post_in_memoria": len(items), "virali_recenti": len(rec)}
    for key, label in (("formato", "formati"), ("tema", "temi")):
        sr, sp = share(rec, key), share(prev, key)
        rows = []
        for k in (set(sr) | set(sp)) - {"altro", "", None}:
            d = sr.get(k, 0) - sp.get(k, 0)
            rows.append((k, round(sr.get(k, 0) * 100), round(d * 100)))
        rows.sort(key=lambda r: (-r[2], -r[1]))
        out[label + "_in_crescita"] = [f"{k} ({p}% dei virali recenti, {d:+d} punti)" for k, p, d in rows if d > 0][:6]
        out[label + "_in_calo"] = [f"{k} ({d:+d} punti)" for k, p, d in rows if d < -5][:5]
    # cosa rende per noi, pesato per recenza
    ours = [it for it in items if it.get("fonte") == "nostro"]

    def best_by(key, rev=True):
        agg: dict = {}
        for it in ours:
            w = _weight(it, now)
            k = it.get(key) or "altro"
            a = agg.setdefault(k, [0.0, 0.0, 0])
            a[0] += w * math.log(max(it.get("rendimento") or 0.01, 0.01))
            a[1] += w
            a[2] += 1
        rows = [(k, math.exp(s / ws), n) for k, (s, ws, n) in agg.items() if ws > 0 and n >= 2]
        rows.sort(key=lambda r: -r[1] if rev else r[1])
        return [f"{k}: {r:.1f}x ({n} post)" for k, r, n in rows]

    out["nostri_formati_migliori"] = [x for x in best_by("formato") if float(x.split(": ")[1].split("x")[0]) >= 1.0][:5]
    out["nostri_formati_peggiori"] = [x for x in best_by("formato", rev=False) if float(x.split(": ")[1].split("x")[0]) < 0.9][:4]
    # le lezioni più recenti: perché ha funzionato / perché no
    rec_all = sorted(items, key=lambda it: _when(it), reverse=True)
    seen, pf = set(), []
    for it in rec_all:
        lz = (it.get("lezione") or "").strip()
        if (it.get("rendimento") or 0) >= 1.5 and lz and lz.lower() not in seen:
            seen.add(lz.lower())
            pf.append(f"[{it.get('fonte')}, {it.get('rendimento')}x] {lz}")
    out["perche_funziona"] = pf[:8]
    out["perche_no"] = [f"[{it.get('rendimento')}x] {it.get('lezione')}"
                        for it in rec_all if it.get("fonte") == "nostro" and (it.get("rendimento") or 9) < 0.8
                        and it.get("lezione")][:5]
    fatt = share([it for it in rec if it.get("fattore")], "fattore")
    out["cosa_spinge"] = [f"{k} {round(v * 100)}%" for k, v in sorted(fatt.items(), key=lambda kv: -kv[1])][:5]
    return out


def prompt_block() -> str:
    """Testo per chi crea i caroselli: trend, cosa funziona per noi, cosa evitare."""
    t = trends()
    if not t or not t.get("post_in_memoria"):
        return ""
    lst = lambda xs: "\n".join(f"- {x}" for x in xs) if xs else "- (ancora pochi dati)"
    return ("MEMORIA DEI CONTENUTI (aggiornata ogni settimana: post virali della nicchia e i NOSTRI risultati)\n"
            f"Formati in crescita (trend da seguire, adattati a noi):\n{lst(t.get('formati_in_crescita'))}\n"
            f"Temi in crescita:\n{lst(t.get('temi_in_crescita'))}\n"
            f"Formati e temi in calo:\n{lst((t.get('formati_in_calo') or []) + (t.get('temi_in_calo') or []))}\n"
            f"Cosa spinge davvero i virali recenti: {', '.join(t.get('cosa_spinge') or []) or '(pochi dati)'}\n"
            f"I nostri formati che rendono di più:\n{lst(t.get('nostri_formati_migliori'))}\n"
            f"I nostri formati da NON riproporre:\n{lst(t.get('nostri_formati_peggiori'))}\n"
            f"Perché hanno funzionato (lezioni recenti):\n{lst(t.get('perche_funziona'))}\n"
            f"Perché i nostri post deboli non hanno funzionato:\n{lst(t.get('perche_no'))}\n"
            "Usa questi dati: segui i trend in crescita, riprendi ciò che ha funzionato capendo QUALE fattore l'ha "
            "spinto (formato, dolore, tema caldo...), evita ciò che per noi non rende.\n")
