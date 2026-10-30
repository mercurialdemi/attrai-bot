"""Storie Instagram (9:16) per la pagina corrente.

- /storie                 -> il bot propone 6 temi (2 didattici, 2 vendita, 1 connessione, 1 virale)
- /storie 3               -> crea la serie del tema 3 della proposta
- /storie <tema>          -> crea la serie su un tema scelto da te (il tipo lo sceglie il bot)
- /storie vendita <tema>  -> forza il tipo: didattica, vendita, connessione, virale

Le strutture (3 per tipo) sono in brands/comuni/storie_strutture.yaml. Il testo lo scrive Claude con il tono e i dolori
della pagina; la grafica la fa il codice (gratis): sfondo a tinta unita della pagina, titolo e testo con caratteri
diversi, due stili che si alternano: 'box' (un riquadro nero dietro al testo) e 'strisce' (una striscia nera dietro
ogni riga). Sotto resta lo spazio per lo sticker (sondaggio, domanda, quiz, link) da aggiungere su Instagram.
Esce anche il file per Canva con i testi modificabili.
"""
from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

from . import config, textslide, writer

log = logging.getLogger(__name__)

W, H = 1080, 1920
TOP_SAFE, BOTTOM_SAFE = 270, 420          # zone coperte dall'interfaccia di Instagram (nome in alto, risposte in basso)
TIPI = ["didattica", "vendita", "connessione", "virale", "lancio"]
SINONIMI = {"didattico": "didattica", "didattiche": "didattica", "marketing": "vendita", "manuale": "vendita",
            "vendite": "vendita", "interazione": "connessione", "sondaggio": "connessione", "virali": "virale",
            "lanci": "lancio"}

# colori per pagina (sfondo a tinta unita, accento per i titoli)
PALETTE_PAGINA = {
    "attraiseduci":     {"bg": (12, 12, 12),  "accento": "#E5605A"},
    "resetrelazionale": {"bg": (196, 88, 28), "accento": "#FFE2C2"},
    "ilpensatore":      {"bg": (18, 38, 74),  "accento": "#8FB8FF"},
    "amoreinfedele":    {"bg": (74, 14, 28),  "accento": "#F2B8C6"},
    "ascesamentale":    {"bg": (22, 22, 22),  "accento": "#D4A853"},
}


def strutture() -> dict:
    p = config.ROOT / "brands" / "comuni" / "storie_strutture.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _proposte_file() -> Path:
    return config.DATA_DIR / "storie_proposte.json"


def _storico_file() -> Path:
    return config.DATA_DIR / "storie_fatte.json"


def _storico() -> list[dict]:
    try:
        return json.loads(_storico_file().read_text(encoding="utf-8"))
    except Exception:
        return []


def _ricorda(item: dict) -> None:
    h = (_storico() + [item])[-60:]
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _storico_file().write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")


def _profilo(brand: dict) -> str:
    manuali = "\n".join(f"- {m['nome']} (titolo: \"{(m.get('titoli') or [m['nome']])[0]}\"): "
                        f"{' '.join(str(m.get('dolori', '')).split())[:500]}" for m in brand.get("manuali") or [])
    return (f"PAGINA: {brand['nome']} (@{brand.get('handle', '')})\n"
            f"PUBBLICO: {brand.get('pubblico') or '-'}\n"
            f"DI COSA PARLA: {' '.join(str(brand.get('temi_report') or brand.get('posizionamento') or '').split())}\n"
            f"POSIZIONAMENTO: {' '.join(str(brand.get('posizionamento') or '').split())}\n"
            f"TONO: {' '.join(brand.get('tono') or [])}\n"
            f"MANUALI IN VENDITA:\n{manuali or '- nessuno: niente storie di vendita, la chiusura è segui la pagina'}\n"
            f"PAROLE DA CENSURARE (penultima lettera con *): {', '.join(brand.get('parole_censurate') or [])}")


# ---------------------------------------------------------------- temi

TEMI_TOOL = {
    "name": "temi_storie",
    "description": "Propone temi per serie di storie Instagram.",
    "input_schema": {"type": "object", "properties": {"temi": {"type": "array", "items": {"type": "object", "properties": {
        "tipo": {"type": "string", "enum": TIPI},
        "tema": {"type": "string", "description": "Il tema in una frase concreta (il dolore o la situazione precisa)."},
        "perche": {"type": "string", "description": "Perché funziona per questo pubblico, in poche parole."},
    }, "required": ["tipo", "tema", "perche"]}}}, "required": ["temi"]},
}


def proponi_temi(brand: dict) -> list[dict]:
    fatti = [f"{s.get('tipo')}: {s.get('tema')}" for s in _storico()[-25:]]
    vendita = 2 if brand.get("manuali") else 0
    richiesta = (f"Proponi 6 temi per serie di storie: {2 + (2 - vendita)} didattica, {vendita} vendita, "
                 "1 connessione, 1 virale. Temi concreti, sui dolori veri di questo pubblico, tutti diversi tra loro"
                 + (" e diversi da quelli già fatti:\n" + "\n".join(fatti) if fatti else "."))
    out = writer._tool_call("Sei lo stratega delle storie Instagram di una pagina italiana.\n" + _profilo(brand),
                            [{"type": "text", "text": richiesta}], TEMI_TOOL, model=config.CLAUDE_MODEL, max_tokens=2000)
    temi = [t for t in out.get("temi") or [] if t.get("tipo") in TIPI and (t.get("tema") or "").strip()][:6]
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _proposte_file().write_text(json.dumps(temi, ensure_ascii=False), encoding="utf-8")
    return temi


def proposta(n: int) -> dict | None:
    try:
        temi = json.loads(_proposte_file().read_text(encoding="utf-8"))
        return temi[n - 1] if 1 <= n <= len(temi) else None
    except Exception:
        return None


def capisci_richiesta(testo: str) -> tuple[str | None, str]:
    """'vendita friendzone e gelosia' -> ('vendita', 'friendzone e gelosia'); 'gelosia' -> (None, 'gelosia')."""
    parole = (testo or "").strip().split(maxsplit=1)
    if parole:
        t = SINONIMI.get(parole[0].lower(), parole[0].lower())
        if t in TIPI:
            return t, (parole[1] if len(parole) > 1 else "")
    return None, (testo or "").strip()


# ---------------------------------------------------------------- testi

STORIE_TOOL = {
    "name": "serie_storie",
    "description": "Una serie di storie Instagram pronte.",
    "input_schema": {"type": "object", "properties": {
        "tipo": {"type": "string", "enum": TIPI},
        "struttura": {"type": "string", "description": "L'id della struttura usata (tra quelle date)."},
        "storie": {"type": "array", "items": {"type": "object", "properties": {
            "titolo": {"type": "string", "description": "Frase breve e forte in alto (max 8 parole), oppure vuoto."},
            "testo": {"type": "string", "description": "Il testo della storia: frasi brevi, si legge in 3-4 secondi (max 30 parole)."},
            "sticker": {"type": "string", "enum": ["nessuno", "sondaggio", "domanda", "quiz", "link", "slider"]},
            "sticker_testo": {"type": "string", "description": "Cosa scrivere nello sticker: domanda e risposte del sondaggio/quiz, testo del box domanda, testo del link. Vuoto se nessuno."},
        }, "required": ["titolo", "testo", "sticker", "sticker_testo"]}},
    }, "required": ["tipo", "struttura", "storie"]},
}


def scrivi(brand: dict, tema: str, tipo: str | None, rules: list[dict]) -> dict:
    st = strutture()
    tipi = [tipo] if tipo else [t for t in TIPI if t not in ("vendita", "lancio") or (brand.get("manuali") and t != "lancio")]
    usate = [s.get("struttura") for s in _storico()[-6:]]
    blocchi = []
    for t in tipi:
        cat = st.get(t) or {}
        righe = []
        for s in cat.get("strutture") or []:
            flag = " (usata di recente: evitala se puoi)" if s["id"] in usate else ""
            righe.append(f"  - id {s['id']}: {s['nome']}{flag}" + (f" (quando: {s['quando']})" if s.get("quando") else "")
                         + "\n" + "\n".join(f"      {i}. {r}" for i, r in enumerate(s['storie'], 1)))
        blocchi.append(f"TIPO {t.upper()}: {cat.get('descrizione', '')}\n" + "\n".join(righe))
    system = ("Sei il copywriter delle storie Instagram di una pagina italiana. Scrivi storie brevi, dirette, che si "
              "leggono in pochi secondi e fanno passare alla storia dopo.\n" + _profilo(brand) + "\n\n"
              "REGOLE: segui UNA delle strutture date, storia per storia, nello stesso ordine. Ogni storia: un titolo "
              "breve (o vuoto) e poche frasi corte. Niente emoji, niente hashtag, niente asterischi di grassetto, niente "
              "trattini lunghi. Italiano parlato, non tradotto. Mai partire dal manuale: si parte dal dolore. Il titolo "
              "di un manuale va tra virgolette. Dove la struttura prevede uno sticker, scegli il tipo e scrivi il suo "
              "testo.\nREGOLE FISSE DI GABRIELE:\n" + "\n".join(f"- {r['text']}" for r in rules) + "\n\n"
              "REGOLE DELLE STORIE CHE FUNZIONANO (valgono sempre):\n"
              + "\n".join(f"- {r}" for r in st.get("regole_generali") or []) + "\n\n"
              "STRUTTURE DISPONIBILI (scegli la più adatta al tema, guardando 'quando'):\n" + "\n\n".join(blocchi))
    out = writer._tool_call(system, [{"type": "text", "text": f"Tema: {tema}\nScrivi la serie con lo strumento serie_storie."}],
                            STORIE_TOOL, model=config.CAPTION_MODEL, max_tokens=4000)
    _pulisci(out, brand)
    return out


def _pulisci(out: dict, brand: dict) -> None:
    for s in out.get("storie") or []:
        for k in ("titolo", "testo"):
            s[k] = writer.clean_text(writer.censor(s.get(k) or "", brand))


# ---------------------------------------------------------------- grafica

def _wrap(draw, testo: str, font, max_w: int) -> list[str]:
    righe = []
    for par in testo.split("\n"):
        parole, cur = par.split(), ""
        if not parole:
            righe.append("")
            continue
        for p in parole:
            prova = (cur + " " + p).strip()
            if draw.textlength(prova, font=font) <= max_w or not cur:
                cur = prova
            else:
                righe.append(cur)
                cur = p
        righe.append(cur)
    return righe


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def disegna(storia: dict, brand: dict, stile: str, slug: str, placements: list | None = None) -> tuple[Image.Image, Image.Image]:
    """Restituisce (storia finita, sfondo senza scritte per Canva). Stile 'box' o 'strisce'."""
    pal = PALETTE_PAGINA.get(slug, PALETTE_PAGINA["attraiseduci"])
    img = Image.new("RGB", (W, H), pal["bg"])
    draw = ImageDraw.Draw(img)
    margine, max_w = 110, W - 220
    domanda = (storia.get("domanda") or "").strip()
    titolo = (storia.get("titolo") or "").strip().upper()
    testo = (storia.get("testo") or "").strip()
    sticker = storia.get("sticker") not in (None, "", "nessuno")
    zona_bassa = BOTTOM_SAFE + (330 if sticker else 0)            # spazio libero per lo sticker
    top_extra = 0
    if domanda:
        # risposta a un box domanda: lo sticker con la domanda lo mette Instagram quando rispondi. Qui resta
        # solo lo spazio libero in alto dove andrà, la risposta sta sotto.
        top_extra = 560
    disponibile = H - TOP_SAFE - top_extra - zona_bassa
    t_size, b_size = 92, 54
    while True:
        tf = textslide._font("titolo", t_size)
        bf = textslide._font("semibold_poppins" if stile == "strisce" else "regular_poppins", b_size)
        t_lines = _wrap(draw, titolo, tf, max_w - 60) if titolo else []
        b_lines = _wrap(draw, testo, bf, max_w - 60) if testo else []
        t_lh, b_lh = int(t_size * 1.12), int(b_size * 1.42)
        gap = 46 if (t_lines and b_lines) else 0
        alto = len(t_lines) * t_lh + gap + len(b_lines) * b_lh
        if alto <= disponibile - 80 or b_size <= 34:
            break
        t_size, b_size = t_size - 6, b_size - 3
    scuro = sum(pal["bg"]) / 3 < 30                 # sfondo quasi nero: riquadri e strisce bianchi, testo nero
    fondo_testo = (255, 255, 255) if scuro else (0, 0, 0)
    col_testo = (15, 15, 15) if scuro else (255, 255, 255)
    y0 = TOP_SAFE + top_extra + max(0, (disponibile - alto) // 2)
    sfondo = img.copy()
    if stile == "box":
        larghezza = max([draw.textlength(r, font=tf) for r in t_lines] + [draw.textlength(r, font=bf) for r in b_lines] + [400])
        bx0, bx1 = (W - larghezza) / 2 - 55, (W + larghezza) / 2 + 55
        for d in (draw, ImageDraw.Draw(sfondo)):
            d.rounded_rectangle((bx0, y0 - 55, bx1, y0 + alto + 55), radius=34, fill=fondo_testo)
    y = y0
    righe_out = []
    col_titolo = (_hex(pal["accento"]) if stile == "box" and not scuro else
                  (200, 40, 40) if stile == "box" else col_testo)
    for lines, f, lh, col, peso, size in ((t_lines, tf, t_lh, col_titolo, "titolo", t_size),
                                           (b_lines, bf, b_lh, col_testo, "semibold_poppins" if stile == "strisce" else "regular_poppins", b_size)):
        for r in lines:
            if not r:
                y += lh // 2
                continue
            w = draw.textlength(r, font=f)
            x = (W - w) / 2
            if stile == "strisce":
                pad_x, pad_y = 22, int(size * 0.18)
                bb = draw.textbbox((x, y), r, font=f)
                for d in (draw, ImageDraw.Draw(sfondo)):
                    d.rectangle((bb[0] - pad_x, bb[1] - pad_y, bb[2] + pad_x, bb[3] + pad_y), fill=fondo_testo)
            draw.text((x, y), r, font=f, fill=col)
            righe_out.append({"testo": r, "x": float(x), "y": float(y), "size": size, "weight": peso, "color": col})
            y += lh
        if lines is t_lines and t_lines:
            y += gap
    # nome della pagina in alto, piccolo
    nome = "@" + (brand.get("handle") or brand["nome"])
    nf = textslide._font("semibold_poppins", 34)
    nx = (W - draw.textlength(nome, font=nf)) / 2
    for d in (draw,):
        d.text((nx, TOP_SAFE - 110), nome, font=nf, fill=(255, 255, 255))
    righe_out.append({"testo": nome, "x": float(nx), "y": float(TOP_SAFE - 110), "size": 34, "weight": "semibold_poppins",
                      "color": (255, 255, 255)})
    if placements is not None:
        placements.extend(righe_out)
    return img, sfondo


def crea(brand: dict, slug: str, tema: str, tipo: str | None, rules: list[dict], jdir: Path) -> tuple[dict, list[Path], Path | None]:
    serie = scrivi(brand, tema, tipo, rules)
    stile = random.choice(["box", "strisce"])
    jdir.mkdir(parents=True, exist_ok=True)
    paths, slides = [], []
    for i, s in enumerate(serie.get("storie") or [], 1):
        pl: list = []
        img, sfondo = disegna(s, brand, stile, slug, pl)
        p = jdir / f"storia_{i:02d}.jpg"
        img.save(p, quality=95)
        bg = jdir / f"storia_{i:02d}_sfondo.jpg"
        sfondo.save(bg, quality=95)
        paths.append(p)
        slides.append({"_canva": {"tipo": "testo", "righe": pl, "sfondo": str(bg)}})
    canva_path = None
    try:
        from . import canva
        canva_path = canva.build({"slides": slides}, paths, jdir / f"Storie_{slug}.pptx")
    except Exception as e:
        log.warning("File Canva delle storie non creato: %s", e)
    serie["stile"] = stile
    serie["tema"] = tema
    _ricorda({"tipo": serie.get("tipo"), "struttura": serie.get("struttura"), "tema": tema,
              "quando": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return serie, paths, canva_path


def istruzioni(serie: dict) -> str:
    """Il testo che accompagna le storie: cosa mettere negli sticker, storia per storia."""
    st = strutture()
    nome_s = next((s["nome"] for c in st.values() if isinstance(c, dict) for s in c.get("strutture") or []
                   if s.get("id") == serie.get("struttura")), serie.get("struttura", ""))
    righe = [f"STORIE · {serie.get('tipo', '')} · struttura: {nome_s} · stile {serie.get('stile')}",
             f"Tema: {serie.get('tema')}"]
    for i, s in enumerate(serie.get("storie") or [], 1):
        if s.get("sticker") not in (None, "", "nessuno"):
            righe.append(f"Storia {i}: aggiungi lo sticker {s['sticker'].upper()} nello spazio libero in basso → {s.get('sticker_testo', '')}")
    if serie.get("tipo") == "lancio":
        n = len(serie.get("storie") or [])
        terzo = max(1, round(n / 3))
        righe.append(f"Lancio in 3 giorni: storie 1-{terzo} il primo giorno, {terzo + 1}-{2 * terzo} il secondo, "
                     f"{2 * terzo + 1}-{n} il terzo.")
    righe.append("Il file Canva ha i testi modificabili.")
    return "\n".join(righe)


# ---------------------------------------------------------------- risposte ai box domanda

RISPOSTA_TOOL = {
    "name": "risposta_storia",
    "description": "Risposta a una domanda arrivata dal box domande delle storie.",
    "input_schema": {"type": "object", "properties": {
        "domanda": {"type": "string", "description": "La domanda trascritta parola per parola (corretta solo negli errori di battitura evidenti)."},
        "storie": {"type": "array", "description": "1-3 storie: sulla prima Instagram mette lo sticker con la domanda in alto, sotto la risposta.", "items": {"type": "object", "properties": {
            "titolo": {"type": "string", "description": "Breve e forte, oppure vuoto."},
            "testo": {"type": "string", "description": "La risposta: diretta, concreta, frasi corte (max 45 parole per storia)."},
        }, "required": ["titolo", "testo"]}},
    }, "required": ["domanda", "storie"]},
}


def rispondi(brand: dict, slug: str, rules: list[dict], jdir: Path, foto: bytes | None = None,
             domanda: str = "") -> tuple[dict, list[Path], Path | None]:
    """Legge la domanda (dalla foto del box domande o dal testo) e prepara 1-3 storie di risposta nello stile
    della pagina: in alto lo spazio libero per lo sticker della domanda (lo mette Instagram), la risposta sotto."""
    content = []
    if foto:
        content += [{"type": "text", "text": "Screenshot della domanda arrivata nel box domande:"}, writer._image_block(foto, 1024)]
    if domanda:
        content.append({"type": "text", "text": f"Domanda: {domanda}"})
    content.append({"type": "text", "text": "Rispondi con lo strumento risposta_storia."})
    system = ("Rispondi alle domande del pubblico nelle storie Instagram di una pagina italiana, con la sua voce. "
              "Risposta onesta, utile e concreta: prima la verità (anche scomoda), poi cosa fare. Se la domanda tocca "
              "il dolore di un nostro manuale, chiudi l'ultima storia con un rimando in una riga (titolo tra virgolette, "
              "link in bio), altrimenti chiudi con un invito a mandare altre domande. Niente emoji, niente hashtag, "
              "italiano parlato. Se nella domanda ci sono nomi o dati personali di chi scrive, non riportarli.\n"
              + _profilo(brand) + "\nREGOLE FISSE DI GABRIELE:\n" + "\n".join(f"- {r['text']}" for r in rules))
    out = writer._tool_call(system, content, RISPOSTA_TOOL, model=config.CAPTION_MODEL, max_tokens=2500)
    _pulisci(out, brand)
    storie = (out.get("storie") or [])[:3]
    if storie:
        storie[0]["domanda"] = (out.get("domanda") or domanda).strip()
    stile = random.choice(["box", "strisce"])
    jdir.mkdir(parents=True, exist_ok=True)
    paths, slides = [], []
    for i, s in enumerate(storie, 1):
        pl: list = []
        img, sfondo = disegna({**s, "sticker": "domanda" if i == len(storie) else "nessuno"}, brand, stile, slug, pl)
        p = jdir / f"risposta_{i:02d}.jpg"
        img.save(p, quality=95)
        bg = jdir / f"risposta_{i:02d}_sfondo.jpg"
        sfondo.save(bg, quality=95)
        paths.append(p)
        slides.append({"_canva": {"tipo": "testo", "righe": pl, "sfondo": str(bg)}})
    canva_path = None
    try:
        from . import canva
        canva_path = canva.build({"slides": slides}, paths, jdir / f"Risposta_{slug}.pptx")
    except Exception as e:
        log.warning("File Canva della risposta non creato: %s", e)
    out["stile"] = stile
    return out, paths, canva_path
