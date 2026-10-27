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

from . import config, images, memoria, textslide, writer

log = logging.getLogger(__name__)

SIZE = (1080, 1350)
BG = (0, 0, 0)
WHITE = "#FFFFFF"
RED = "#E5605A"

# Palette monocolore: sfondo pieno, testo chiaro, un colore d'accento per le parole chiave.
PALETTES = {
    "nero":        {"bg": (0, 0, 0),       "testo": "#FFFFFF", "sotto": "#D9D9D9", "accento": "#E5605A"},
    "verde_scuro": {"bg": (16, 42, 32),    "testo": "#F3EBDD", "sotto": "#D8CFC0", "accento": "#D4A853"},
    "blu_notte":   {"bg": (13, 22, 38),    "testo": "#FFFFFF", "sotto": "#D5DAE3", "accento": "#E8873A"},
    "bordeaux":    {"bg": (43, 10, 16),    "testo": "#F5E9E0", "sotto": "#E3D3C8", "accento": "#E8B04A"},
    "antracite":   {"bg": (27, 27, 27),    "testo": "#FFFFFF", "sotto": "#D9D9D9", "accento": "#E5605A"},
}
# Stili di carattere: 'impatto' (condensato maiuscolo, grande) per i contenuti emotivi/virali,
# 'autorevole' (serif elegante) per i contenuti didattici.
STILI = {
    "impatto":    {"carattere": "titolo", "peso": None, "maiuscolo": True},
    "autorevole": {"carattere": "normale", "peso": "elegante", "maiuscolo": False},
}


def choose_look(natura: str) -> dict:
    """Sceglie palette e stile. Stile: di norma impatto per i virali e autorevole per i didattici (1 volta su 5
    l'altro, per imparare). Palette: se lo studio della pagina ha già i risultati dei caroselli pubblicati,
    si preferiscono i colori che rendono di più; altrimenti si ruota, senza ripetere l'ultima."""
    stile = "autorevole" if natura == "didattico" else "impatto"
    if random.random() < 0.2:
        stile = "impatto" if stile == "autorevole" else "autorevole"
    fb = (writer.load_style().get("feedback_bot") or {})
    per_pal = {k: v for k, v in (fb.get("per_palette") or {}).items() if k in PALETTES and v.get("post", 0) >= 2}
    last = (history()[-1].get("palette") if history() else None)
    names = [k for k in PALETTES if k != last] or list(PALETTES)
    if per_pal and random.random() < 0.7:
        best = sorted(per_pal.items(), key=lambda kv: -kv[1]["rendimento"])
        palette = next((k for k, _ in best if k != last), best[0][0])
    else:
        palette = random.choice(names)
    cop = memoria.trends().get("copertina_migliore") or "grande"
    return {"palette": palette, "stile": stile, "copertina": cop}

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
            "funzione": {"type": "string", "enum": memoria.FUNZIONI,
                         "description": "Il ruolo del carosello nel profilo (te lo indichiamo noi nella richiesta)."},
            "formato": {"type": "string", "enum": memoria.FORMATI,
                        "description": "Come è raccontato (pov, prima persona, immersivo, elenco, dialogo...): segui i "
                                       "formati in crescita e quelli che rendono per noi."},
            "struttura": {"type": "string", "description": "Ruolo di ogni slide in ordine."},
            "concetto": {"type": "string", "description": "Il messaggio di tutto il carosello in 3-4 frasi."},
            "slides": {"type": "array", "minItems": 5, "maxItems": 9, "items": {"type": "object", "properties": {
                "titolo": {"type": "string", "description": (
                    "Copertina: il gancio, grande, massimo 10 parole (meglio 6-8). Slide interne: un titoletto breve (2-7 "
                    "parole) oppure vuoto. Le parole più forti tra [[ ]] (vengono in rosso).")},
                "testo": {"type": "string", "description": (
                    "Copertina: un sottotitolo breve che apre la curiosità (una frase) oppure vuoto. Slide interne: "
                    "il corpo, 2-5 frasi brevi scritte normalmente (non in maiuscolo), con a capo tra le frasi; "
                    "le 1-3 espressioni chiave tra [[ ]] (vengono in rosso). Ultima slide prima della CTA: una frase "
                    "che chiude e resta. Niente emoji, niente asterischi, niente CTA (la aggiungiamo noi).")},
            }, "required": ["titolo", "testo"]}},
        },
        "required": ["tema", "dolore", "manuale", "natura", "tipo_hook", "funzione", "formato", "struttura", "concetto",
                     "slides"],
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
{memoria.prompt_block()}
{('COME SCRIVIAMO NOI (dai nostri post migliori)' + chr(10) + guida[:2500] + chr(10)) if guida else ''}
I NOSTRI MANUALI E I DOLORI CHE RISOLVONO (un post didattico parte da uno di questi dolori, MAI dal prodotto)
{manuali}

SCRITTURA DA COPYWRITER (il carosello deve sembrare scritto da un professionista, non da un divulgatore)
- Il GANCIO non spiega e non riassume: apre una ferita o un vuoto di curiosità che si chiude solo scorrendo.
  NO titoli-tesi generici tipo "Perché lei si allontana quando sei troppo disponibile" (dicono già tutto).
  SÌ scene, verità scomode, frasi che il lettore ha vissuto: "Le hai risposto in 3 secondi. Lei ci ha messo 3
  ore. Indovina chi dei due sta già perdendo." / "Non ti ha lasciato perché eri troppo buono."
- Specifico batte generico: un dettaglio reale (l'orario, la frase esatta, il gesto) vale più di un concetto.
- Ogni slide finisce lasciando una domanda aperta che spinge alla successiva (open loop); mai due slide che
  dicono la stessa cosa con parole diverse.
- Ritmo: frasi brevi, colpi secchi, alternati a una frase più lunga che spiega. Mai paragrafi da manuale.
- La penultima slide è la svolta (il meccanismo nascosto o il ribaltamento); l'ultima chiude con una frase che
  il lettore vorrebbe salvare o mandare a un amico.
- Prima di consegnare rileggi solo la copertina: se non fermeresti il pollice, riscrivila.
- PORTA IL LETTORE DENTRO (come fanno le pagine migliori): fallo sentire compreso ("ti è mai successo che..."),
  mettilo nella scena, fagli farsi le domande giuste e lascialo arrivare DA SOLO alla soluzione invece di
  dargliela come una lezione. Deve pensare "è esattamente quello che mi succede" e poi "ora ho capito".
- Copertina: poche parole, scritte GRANDI. Un gancio lungo in piccolo non lo legge nessuno. Niente dialoghi
  interi in copertina: una sola frase che fa male o incuriosisce, il resto va nelle slide dopo.

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
    gia = "\n".join(f"- {h.get('tema')} ({h.get('formato')}, {h.get('tipo_hook')}, {h.get('struttura', '')[:60]})"
                    for h in recenti)
    funz = memoria.next_function([h.get("funzione") for h in history()])
    ruoli = {"portata": "PORTATA: virale, emotivo, polarizzante, fatto per essere condiviso e portare follower "
                        "(CTA segui la pagina)",
             "autorevolezza": "AUTOREVOLEZZA: didattico, insegna un meccanismo o un trucco concreto, fa vedere che "
                              "sappiamo di cosa parliamo",
             "vendita": "VENDITA: tocca un dolore preciso di uno dei nostri manuali e porta alla CTA del manuale",
             "connessione": "CONNESSIONE: il lettore si sente compreso, si riconosce, commenta e si identifica"}
    req = (f"Richiesta di Gabriele: {hint}\n" if hint else
           f"Scegli tu il tema migliore per oggi. FUNZIONE di questo carosello nel mix del profilo: {ruoli[funz]}.\n")
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
    post["_look"] = choose_look(post.get("natura") or idea.get("natura", "virale"))
    post["_idea"] = {k: idea.get(k) for k in ("tema", "dolore", "manuale", "natura", "tipo_hook", "funzione", "formato",
                                               "struttura")}
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
                 cta: bool = False, look: dict | None = None) -> Image.Image:
    """Sfondo monocolore con gerarchia: TITOLO (condensato maiuscolo o serif elegante, secondo lo stile),
    sottotitolo/corpo in Poppins scritto normalmente, parole chiave nel colore d'accento."""
    look = look or {"palette": "nero", "stile": "impatto"}
    pal = PALETTES.get(look.get("palette"), PALETTES["nero"])
    st = STILI.get(look.get("stile"), STILI["impatto"])
    SS = 2                                    # disegno a doppia risoluzione e poi riduco: bordi delle lettere nitidi
    W, H = SIZE[0] * SS, SIZE[1] * SS
    img = Image.new("RGB", (W, H), pal["bg"])
    tit, body = split_parts(text)
    if cta and not tit:
        paras = [p for p in text.split("\n\n") if p.strip()]
        if len(paras) > 1:
            tit, body = paras[0].upper(), "\n\n".join(paras[1:])
    if cta and tit and body and tit.rstrip()[-1:] in ",;":
        # la frase della CTA continua nel corpo: niente virgola appesa in fondo al titolo, corpo con la maiuscola
        tit = tit.rstrip().rstrip(",;").rstrip()
        body = body.lstrip()
        body = body[:1].upper() + body[1:]
    if not st["maiuscolo"] and tit:
        tit = _sentence(tit)
    if st["carattere"] == "titolo":
        tit = _virgolette(tit)
    tfont = {"carattere": st["carattere"], "maiuscolo": st["maiuscolo"]}
    if st["peso"]:
        tfont["peso"] = st["peso"]
    # il serif è più largo del condensato: meno caratteri per riga
    cpl_cover, cpl_head = (18, 28) if st["maiuscolo"] else (16, 24)
    blocks = []
    if cover:
        big = look.get("copertina") in ("enorme", "grande")
        if big:
            cpl_cover = max(10, cpl_cover - 5)          # testo più grande: meno caratteri per riga
        ht = min(600 if big else 520, (150 if big else 118) * _righe(tit, cpl_cover)) if tit else 0
        hs = min(220, 52 * _righe(body, 36)) if body else 0
        gap = 45 if (tit and body) else 0
        top = (1000 - (ht + gap + hs)) // 2 + 30
        if tit:
            blocks.append({"testo": tit, "x": 80, "y": top, "w": 840, "h": ht, "allineamento": "center",
                           **tfont, "colore": pal["testo"], "colore_evidenziato": pal["accento"]})
        if body:
            blocks.append({"testo": body, "x": 120, "y": top + ht + gap, "w": 760, "h": hs,
                           "allineamento": "center", "carattere": "normale", "peso": "regular_poppins",
                           "colore": pal["sotto"], "colore_evidenziato": pal["accento"]})
    else:
        ht = min(260, 64 * _righe(tit, cpl_head)) if tit else 0
        hb = min(600, 47 * _righe(body, 34)) if body else 0
        gap = 42 if (tit and body) else 0
        top = (1000 - (ht + gap + hb)) // 2
        if tit:
            blocks.append({"testo": tit, "x": 95, "y": top, "w": 810, "h": ht, "allineamento": "left", **tfont,
                           "colore": pal["accento"] if "[[" not in tit else pal["testo"],
                           "colore_evidenziato": pal["accento"]})
        if body:
            blocks.append({"testo": body, "x": 95, "y": top + ht + gap, "w": 810, "h": hb,
                           "allineamento": "left", "carattere": "normale", "peso": "regular_poppins",
                           "colore": pal["testo"], "colore_evidenziato": pal["accento"]})
    boxes = [(textslide.block_box(b, W, H), None) for b in blocks]
    pl: list = []
    out = textslide.render_blocks(img, img, blocks, pl, boxes=boxes)
    if logo:
        out = images.add_brand_mark(out, brand, [b for b, _ in boxes])
    out = out.resize(SIZE, Image.LANCZOS)
    if placements is not None:                # coordinate per Canva riportate alla grandezza vera
        for r in pl:
            for k in ("x", "y"):
                if isinstance(r.get(k), (int, float)):
                    r[k] = r[k] / SS
            if isinstance(r.get("size"), (int, float)):
                r["size"] = max(1, round(r["size"] / SS))
        placements.extend(pl)
    return out


def _sentence(t: str) -> str:
    """TITOLO IN MAIUSCOLO -> Titolo scritto normalmente (per il serif elegante)."""
    import re as _re
    low = t.lower()
    out, cap = [], True
    for ch in low:
        if cap and ch.isalpha():
            out.append(ch.upper())
            cap = False
        else:
            out.append(ch)
        if ch in ".!?":                  # un a capo NON è inizio frase: "familiare,\nse anche tu" resta minuscolo
            cap = True
    return _re.sub(r"\[\[(.)", lambda m: "[[" + m.group(1), "".join(out))


def _virgolette(t: str) -> str:
    """Nel carattere stretto dei titoli le « » sembrano due frecce << >>: lì si usano le virgolette alte “ ”."""
    return (t or "").replace("«", "“").replace("»", "”")


def _black(jdir: Path, look: dict | None = None) -> Path:
    pal = PALETTES.get((look or {}).get("palette"), PALETTES["nero"])
    p = jdir / f"sfondo_{(look or {}).get('palette') or 'nero'}.jpg"
    if not p.exists():
        Image.new("RGB", SIZE, pal["bg"]).save(p, "JPEG", quality=95)
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
            render_slide(sl["testo"], cover=(i == 1), brand=brand, logo=(i == 1), placements=pl,
                         look=post.get("_look")).save(p, "JPEG", quality=95, subsampling=0)
            sl["_canva"] = {"tipo": "testo", "righe": pl, "sfondo": str(_black(jdir, post.get("_look")))}   # testo modificabile in Canva
        sl["numero"], sl["_via"] = i, "creato-testo-su-nero"
        paths.append(p)
    cta_text = (post.get("_testo_cta_finale") or "").strip()
    n = len(main) + 1
    if cta_text:
        p = jdir / "cta_creata.jpg"
        pl = []
        if only is None or n in only or not p.exists():
            render_slide(cta_text, cover=False, brand=brand, logo=False, placements=pl, cta=True,
                         look=post.get("_look")).save(
                p, "JPEG", quality=95, subsampling=0)
        old_cta = next((s_ for s_ in post["slides"] if s_.get("e_cta")), {})
        post["slides"] = main + [{"numero": n, "testo": cta_text, "e_cta": True, "_via": "cta-creata",
                                  "_canva": ({"tipo": "testo", "righe": pl, "sfondo": str(_black(jdir, post.get("_look")))} if pl
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
    _remember({**post["_idea"], **(post.get("_look") or {}),
               "quando": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return post, paths
