"""/crea: caroselli ORIGINALI da zero, nello stile AttraiSeduci.

1. Claude sceglie tema, dolore, hook e struttura partendo dallo studio dei virali, dalla guida di stile della
   pagina e dai dolori dei manuali, evitando quello che è già stato creato di recente.
2. Scrive le slide (titoletti e parole forti tra [[ ]] in rosso) e un copywriter le rilegge.
3. Descrizione e CTA con le stesse regole dei post tradotti (didattico -> manuale, virale -> segui).
4. Grafica "testo su nero" fatta dal codice: costo zero per le immagini.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from . import config, images, textslide, writer

log = logging.getLogger(__name__)

SIZE = (1080, 1350)
BG = (0, 0, 0)
WHITE = "#FFFFFF"
RED = "#E5605A"

IDEA_TOOL = {
    "name": "carosello_nuovo",
    "description": "Un carosello originale per la nostra pagina.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tema": {"type": "string", "description": "Di cosa parla, in 6-10 parole."},
            "dolore": {"type": "string", "description": "Il dolore preciso del lettore che tocca."},
            "manuale": {"type": "string", "description": "id del manuale collegato al dolore, oppure vuoto."},
            "natura": {"type": "string", "enum": ["didattico", "virale"],
                       "description": "didattico = insegna qualcosa di concreto (meccanismo, trucco, cosa fare); "
                                      "virale = punta su emozione, ego, polemica, riconoscimento."},
            "tipo_hook": {"type": "string"},
            "struttura": {"type": "string", "description": "Ruolo di ogni slide in ordine."},
            "concetto": {"type": "string", "description": "Il messaggio di tutto il carosello in 3-4 frasi."},
            "slides": {"type": "array", "minItems": 5, "maxItems": 9, "items": {"type": "object", "properties": {
                "titolo": {"type": "string", "description": (
                    "Copertina: il gancio, grande, massimo 12-14 parole. Slide interne: un titoletto breve (2-7 "
                    "parole) oppure vuoto. Le parole più forti tra [[ ]] (vengono in rosso).")},
                "testo": {"type": "string", "description": (
                    "Copertina: un sottotitolo breve che apre la curiosità (una frase) oppure vuoto. Slide interne: "
                    "il corpo, 2-5 frasi brevi scritte normalmente (non in maiuscolo), con a capo tra le frasi; "
                    "le 1-3 espressioni chiave tra [[ ]] (vengono in rosso). Ultima slide prima della CTA: una frase "
                    "che chiude e resta. Niente emoji, niente asterischi, niente CTA (la aggiungiamo noi).")},
            }, "required": ["titolo", "testo"]}},
        },
        "required": ["tema", "dolore", "manuale", "natura", "tipo_hook", "struttura", "concetto", "slides"],
    },
}


def _history_file() -> Path:
    return config.DATA_DIR / "creati.json"


def history() -> list[dict]:
    try:
        return json.loads(_history_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def _remember(item: dict) -> None:
    h = history()
    h.append(item)
    try:
        _history_file().write_text(json.dumps(h[-200:], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _system(brand: dict, rules: list[dict]) -> str:
    style = writer.load_style()
    sv = style.get("studio_virali") or {}
    lista = lambda xs: "\n".join(f"- {x}" for x in xs or [])
    studio = ""
    if sv.get("regole"):
        studio = ("STUDIO DEI CAROSELLI VIRALI DELLA NICCHIA (già adattato a noi)\n"
                  f"Temi che rendono di più:\n{lista(sv.get('temi'))}\n"
                  f"Hook vincenti:\n{lista(sv.get('hook'))}\n"
                  f"Strutture vincenti:\n{lista(sv.get('strutture'))}\n"
                  f"Regole:\n{sv.get('regole')}\n")
    manuali = "\n".join(f"- id: {m['id']} | {m['nome']} | dolori: {' '.join(str(m.get('dolori', '')).split())[:400]}"
                        for m in brand.get("manuali", []))
    guida = (style.get("guida") or "").strip()
    return f"""Sei l'autore dei caroselli della pagina Instagram italiana {brand['nome']} (@{brand['handle']}).
Scrivi un carosello ORIGINALE, da zero, che possa diventare virale o vendere, nella nostra voce.

POSIZIONAMENTO
{' '.join(str(brand.get('posizionamento', '')).split())}

TONO
{chr(10).join('- ' + t for t in brand.get('tono', []))}
- Italiano parlato, frasi corte e piene, niente calchi dall'inglese, niente volgarità gratuite.

{writer._scuole(brand)}
{studio}
{('COME SCRIVIAMO NOI (dai nostri post migliori)' + chr(10) + guida[:2500] + chr(10)) if guida else ''}
I NOSTRI MANUALI E I DOLORI CHE RISOLVONO (un post didattico parte da uno di questi dolori, MAI dal prodotto)
{manuali}

REGOLE
- Si parte sempre dal dolore e dalla dinamica, mai dal manuale.
- Un solo concetto forte per carosello, sviluppato slide dopo slide: ogni slide deve far venire voglia di
  passare alla successiva.
- La prima slide è il gancio: deve fermare il pollice da sola.
- Mai inventare numeri o studi.

REGOLE FISSE DATE DA GABRIELE
{writer._rules_block(rules)}
"""


def write_carousel(brand: dict, rules: list[dict], recent: dict, hint: str = "") -> dict:
    """Idea + testo + descrizione + CTA, nello stesso formato dei post tradotti."""
    recenti = history()[-25:]
    gia = "\n".join(f"- {h.get('tema')} ({h.get('tipo_hook')}, {h.get('struttura', '')[:60]})" for h in recenti)
    req = (f"Richiesta di Gabriele: {hint}\n" if hint else "Scegli tu il tema migliore per oggi.\n")
    content = [{"type": "text", "text": (
        req + ("\nGià creati di recente (NON ripetere tema, hook e struttura):\n" + gia + "\n" if gia else "")
        + "\nScrivi il carosello con lo strumento carosello_nuovo.")}]
    idea = writer._tool_call(_system(brand, rules), content, IDEA_TOOL, model=config.CAPTION_MODEL, max_tokens=6000)
    slides = []
    for i, s_ in enumerate(idea.get("slides") or [], 1):
        tit = writer.clean_text(s_.get("titolo") or "").upper()
        body = writer.clean_text(s_.get("testo") or "")
        if not (tit or body):
            continue
        slides.append({"numero": i, "testo": compose(tit, body), "testo_originale": "", "da_modificare": True,
                       "e_cta": False, "sfondo": "tinta_unita",
                       "blocchi": [{"testo": t} for t in (tit, body) if t]})
    out = {"concetto": idea.get("concetto", ""), "senso": idea.get("tema", ""), "formato": "carosello",
           "natura": idea.get("natura", "virale"), "slides": slides}
    try:
        out = writer.polish_slides(out)                    # rilettura da copywriter italiano
        for sl in out["slides"]:
            bl = [b["testo"] for b in sl.get("blocchi") or []]
            if len(bl) == 2:
                sl["testo"] = compose(bl[0].upper(), bl[1])
            elif len(bl) == 1:
                tit, body = split_parts(sl["testo"])
                sl["testo"] = compose(bl[0].upper(), "") if tit and not body else compose("", bl[0])
    except Exception as e:
        log.warning("Rilettura del carosello non riuscita: %s", e)
    cap = writer.write_caption(out, "", brand, rules, recent)
    cap.pop("slide_cta", None)
    out["_formato_post"] = cap.pop("formato_post", "")
    cta = cap.get("cta") or {}
    out["_testo_cta_finale"] = writer.quote_titles(
        writer.pick_cta_slide_text(writer.clean_text(cap.pop("testo_slide_finale", "") or ""), cta), brand)
    if cap.get("descrizione"):
        cap["descrizione"] = writer.quote_titles(
            writer.align_description_cta(cap["descrizione"], out["_testo_cta_finale"]), brand)
    post = writer._finalize({**out, **cap})
    post["_originale"] = True
    post["_idea"] = {k: idea.get(k) for k in ("tema", "dolore", "manuale", "natura", "tipo_hook", "struttura")}
    return post


def compose(titolo: str, testo: str) -> str:
    """Testo della slide: titolo (in maiuscolo) + riga vuota + corpo. È l'unica fonte per disegnarla."""
    return "\n\n".join(x for x in (titolo.strip(), testo.strip()) if x)


def _is_caps(t: str) -> bool:
    letters = [c for c in t.replace("[[", "").replace("]]", "") if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


def split_parts(text: str) -> tuple[str, str]:
    """(titolo, corpo): il primo paragrafo in MAIUSCOLO è il titolo, il resto è il corpo."""
    paras = [p for p in (text or "").split("\n\n") if p.strip()]
    if paras and _is_caps(paras[0]):
        return paras[0], "\n\n".join(paras[1:])
    return "", "\n\n".join(paras)


def _righe(text: str, per_riga: int) -> int:
    """Righe che occuperà il testo (le righe lunghe vanno a capo): serve a dare a ogni slide la stessa grandezza."""
    import math
    return max(1, sum(math.ceil(len(l.replace("[[", "").replace("]]", "")) / per_riga)
                      for l in text.split("\n") if l.strip()))


def render_slide(text: str, cover: bool, brand: dict, logo: bool, placements: list | None = None,
                 cta: bool = False) -> Image.Image:
    """Testo su nero con gerarchia: TITOLO condensato maiuscolo, sottotitolo/corpo in carattere leggibile
    (Poppins) scritto normalmente, parole chiave in rosso. Grandezze fisse per tipo di testo."""
    img = Image.new("RGB", SIZE, BG)
    W, H = SIZE
    tit, body = split_parts(text)
    if cta and not tit:
        paras = [p for p in text.split("\n\n") if p.strip()]
        if len(paras) > 1:
            tit, body = paras[0].upper(), "\n\n".join(paras[1:])
    blocks = []
    if cover:
        ht = min(520, 118 * _righe(tit, 18)) if tit else 0
        hs = min(220, 52 * _righe(body, 36)) if body else 0
        gap = 45 if (tit and body) else 0
        top = (1000 - (ht + gap + hs)) // 2 + 30
        if tit:
            blocks.append({"testo": tit, "x": 80, "y": top, "w": 840, "h": ht, "allineamento": "center",
                           "carattere": "titolo", "maiuscolo": True, "colore": WHITE, "colore_evidenziato": RED})
        if body:
            blocks.append({"testo": body, "x": 120, "y": top + ht + gap, "w": 760, "h": hs,
                           "allineamento": "center", "carattere": "normale", "peso": "regular_poppins",
                           "colore": "#D9D9D9", "colore_evidenziato": RED})
    else:
        ht = min(260, 64 * _righe(tit, 28)) if tit else 0
        hb = min(600, 47 * _righe(body, 34)) if body else 0
        gap = 42 if (tit and body) else 0
        top = (1000 - (ht + gap + hb)) // 2
        if tit:
            blocks.append({"testo": tit, "x": 95, "y": top, "w": 810, "h": ht, "allineamento": "left",
                           "carattere": "titolo", "maiuscolo": True, "colore": RED if "[[" not in tit else WHITE,
                           "colore_evidenziato": RED})
        if body:
            blocks.append({"testo": body, "x": 95, "y": top + ht + gap, "w": 810, "h": hb,
                           "allineamento": "left", "carattere": "normale", "peso": "regular_poppins",
                           "colore": WHITE, "colore_evidenziato": RED})
    boxes = [(textslide.block_box(b, W, H), None) for b in blocks]
    out = textslide.render_blocks(img, img, blocks, placements if placements is not None else [], boxes=boxes)
    if logo:
        out = images.add_brand_mark(out, brand, [b for b, _ in boxes])
    return out


def _black(jdir: Path) -> Path:
    p = jdir / "sfondo_nero.jpg"
    if not p.exists():
        Image.new("RGB", SIZE, BG).save(p, "JPEG", quality=95)
    return p


def render_post(post: dict, brand: dict, jdir: Path, only: set[int] | None = None) -> list[Path]:
    """Slide del carosello + slide CTA finale nello stesso stile. `only` = numeri delle slide da rifare."""
    jdir.mkdir(parents=True, exist_ok=True)
    main = [sl for sl in post["slides"] if not sl.get("e_cta")]
    paths = []
    for i, sl in enumerate(main, 1):
        p = jdir / f"slide_{i:02d}.jpg"
        if only is None or i in only or not p.exists():
            pl: list = []
            render_slide(sl["testo"], cover=(i == 1), brand=brand, logo=(i == 1), placements=pl).save(
                p, "JPEG", quality=95, subsampling=0)
            sl["_canva"] = {"tipo": "testo", "righe": pl, "sfondo": str(_black(jdir))}   # testo modificabile in Canva
        sl["numero"], sl["_via"] = i, "creato-testo-su-nero"
        paths.append(p)
    cta_text = (post.get("_testo_cta_finale") or "").strip()
    n = len(main) + 1
    if cta_text:
        p = jdir / "cta_creata.jpg"
        pl = []
        if only is None or n in only or not p.exists():
            render_slide(cta_text, cover=False, brand=brand, logo=False, placements=pl, cta=True).save(
                p, "JPEG", quality=95, subsampling=0)
        old_cta = next((s_ for s_ in post["slides"] if s_.get("e_cta")), {})
        post["slides"] = main + [{"numero": n, "testo": cta_text, "e_cta": True, "_via": "cta-creata",
                                  "_canva": ({"tipo": "testo", "righe": pl, "sfondo": str(_black(jdir))} if pl
                                             else old_cta.get("_canva") or {"tipo": "immagine"})}]
        paths.append(p)
    else:
        post["slides"] = main
    return paths


def make(brand: dict, rules: list[dict], recent: dict, hint: str = "") -> tuple[dict, list[Path]]:
    post = write_carousel(brand, rules, recent, hint)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{random.randint(100, 999)}"
    jdir = config.DATA_DIR / "jobs" / f"creato_{stamp}"
    paths = render_post(post, brand, jdir)
    _remember({**post["_idea"], "quando": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return post, paths
