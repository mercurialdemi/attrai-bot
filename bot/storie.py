"""Storie Instagram (9:16) per la pagina corrente, nel formato del prompt di Gabriele (2/11).

- /storie                 -> il bot propone 6 temi (didattiche, di vendita se la pagina ha manuali, box domande)
- /storie 3               -> crea la serie del tema 3 della proposta
- /storie <tema>          -> serie didattica sul tema
- /storie vendita <tema>  -> forza il tipo: didattica, vendita, domande
- foto del box domande con "risposta" (o /risposta <domanda>) -> risposta in 3 slide

Una serie = 3 slide: gancio (metà alta libera per lo sticker), didattica, chiusura o CTA al manuale. Il prompt è in
brands/comuni/storie_prompt.md (modificabile). Grafica fatta dal codice (gratis): foto della pagina
(assets/storie_sfondi/<pagina>/<categoria>_NN.jpg) con velo nero al 55%, rettangoli neri pieni a sinistra, testo
bianco Carlito Bold con le parole chiave in rosso, handle in basso a destra. Esce anche il file per Canva.
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
TIPI = ["didattica", "vendita", "domande"]
SINONIMI = {"didattico": "didattica", "didattiche": "didattica", "marketing": "vendita", "manuale": "vendita",
            "vendite": "vendita", "domanda": "domande", "box": "domande", "qa": "domande", "risposte": "domande"}

# colori per pagina (sfondo a tinta unita, accento per i titoli)
PALETTE_PAGINA = {
    "attraiseduci":     {"bg": (12, 12, 12),  "accento": "#E5605A"},
    "resetrelazionale": {"bg": (196, 88, 28), "accento": "#FFE2C2"},
    "ilpensatore":      {"bg": (18, 38, 74),  "accento": "#8FB8FF"},
    "amoreinfedele":    {"bg": (74, 14, 28),  "accento": "#F2B8C6"},
    "ascesamentale":    {"bg": (22, 22, 22),  "accento": "#D4A853"},
}



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
            + (f"VOCE DELLE STORIE (ha la precedenza sul tono qui sopra): {' '.join(str(brand['storie_voce']).split())}\n"
               if brand.get("storie_voce") else "")
            + f"MANUALI IN VENDITA:\n{manuali or '- nessuno: niente storie di vendita; chiudi con condividi o rispondi'}\n"
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
    richiesta = (f"Proponi 6 temi per serie di storie: {3 + (2 - vendita)} didattica, {vendita} vendita, "
                 "1 domande (box domande). Temi concreti, sui dolori veri di questo pubblico, tutti diversi tra loro"
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


# ---------------------------------------------------------------- testi (formato del prompt di Gabriele, 2/11)

SFONDI = ["sicura", "distante", "elegante", "primo_piano", "complice"]   # complice = ride, sembra amica (friendzone)
AUTORI_BASE = ["Robert Greene", "Rollo Tomassi", "Neil Strauss", "Mystery", "David DeAngelo", "Esther Perel"]

_SLIDE = {"type": "object", "properties": {
    "blocchi": {"type": "array", "items": {"type": "string"},
                "description": "1-3 blocchi di testo, ognuno un ragionamento compiuto di 3-5 righe; parole chiave tra **...**."},
}, "required": ["blocchi"]}

STORIE_TOOL = {
    "name": "serie_storie",
    "description": "Una serie di storie Instagram pronte (3 slide; 1 slide per il box domande).",
    "input_schema": {"type": "object", "properties": {
        "tipo": {"type": "string", "enum": TIPI},
        "sfondo": {"type": "string", "enum": SFONDI, "description": "Categoria di foto di sfondo più coerente col tema."},
        "slides": {"type": "array", "items": _SLIDE},
        "sticker_testo": {"type": "string", "description": "Solo per il box domande: il testo da scrivere nello sticker DOMANDA (breve). Altrimenti vuoto."},
    }, "required": ["tipo", "sfondo", "slides", "sticker_testo"]},
}


def _prompt_base() -> str:
    p = config.ROOT / "brands" / "comuni" / "storie_prompt.md"
    try:
        return "\n".join(r for r in p.read_text(encoding="utf-8").splitlines() if not r.startswith("#"))
    except Exception:
        return ""


def _system(brand: dict, rules: list[dict]) -> str:
    autori = brand.get("storie_autori") or AUTORI_BASE
    manuali = [f"\"{(m.get('titoli') or [m['nome']])[0]}\"" for m in brand.get("manuali") or []]
    return ("Sei il copywriter delle storie Instagram di una pagina italiana.\n" + _profilo(brand) + "\n"
            f"AUTORI che puoi nominare (solo per idee davvero loro): {', '.join(autori)}\n"
            f"TITOLI DEI MANUALI: {', '.join(manuali) or 'nessuno (niente vendita)'}\n\n"
            + _prompt_base()
            + "\n\nREGOLE FISSE DI GABRIELE (hanno la precedenza):\n"
            + "\n".join(f"- {r['text']}" for r in rules))


def scrivi(brand: dict, tema: str, tipo: str | None, rules: list[dict]) -> dict:
    if tipo is None:
        tipo = "didattica"
    if tipo == "vendita" and not brand.get("manuali"):
        tipo = "didattica"
    richiesta = {
        "didattica": "Serie DIDATTICA: 3 slide (gancio, didattica, chiusura aperta senza vendere).",
        "vendita": "Serie di VENDITA: 3 slide (gancio, didattica, chiusura con il manuale più adatto al tema).",
        "domande": ("BOX DOMANDE: 1 slide sola. Un blocco di 3-4 righe che apre il tema con un gancio e invita a fare "
                    "la domanda che non farebbero a nessuno (la metà alta resta libera per lo sticker DOMANDA). "
                    "Scrivi anche sticker_testo, breve (es. 'Chiedimi tutto su [tema]')."),
    }[tipo]
    out = writer._tool_call(_system(brand, rules),
                            [{"type": "text", "text": f"Tema: {tema}\n{richiesta}\nScrivi con lo strumento serie_storie."}],
                            STORIE_TOOL, model=config.CAPTION_MODEL, max_tokens=4000)
    out["tipo"] = tipo
    out["slides"] = (out.get("slides") or [])[:1 if tipo == "domande" else 3]
    _pulisci(out, brand)
    return out


def _pulisci(out: dict, brand: dict) -> None:
    for s in out.get("slides") or []:
        s["blocchi"] = [_pulisci_blocco(b, brand) for b in (s.get("blocchi") or []) if (b or "").strip()][:3]


def _pulisci_blocco(testo: str, brand: dict) -> str:
    """Pulisce e censura il testo tenendo i ** delle parole chiave (la pulizia normale toglierebbe gli asterischi)."""
    pezzi = (testo or "").split("**")
    def pulito(p: str) -> str:            # la pulizia toglie gli spazi ai bordi: si rimettono (servono tra le parole)
        if not p.strip():
            return p
        testa = " " if p[0].isspace() else ""
        coda = " " if p[-1].isspace() else ""
        return testa + writer.clean_text(writer.censor(p, brand)) + coda
    return "**".join(pulito(p) for p in pezzi)


# ---------------------------------------------------------------- grafica

ROSSO = (230, 57, 70)            # #E63946
GRIGIO = (221, 221, 221)          # #DDDDDD
FONT_BOLD = config.ROOT / "assets" / "carlito_bold.ttf"
FONT_CORSIVO = config.ROOT / "assets" / "carlito_italic.ttf"
FONT_GRASSETTO_CORSIVO = config.ROOT / "assets" / "carlito_bold_italic.ttf"
MARGINE_X = 54                   # rettangoli larghi il 90% della slide
PAD_X, PAD_Y, GAP = 24, 12, 28


def _font(path: Path, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return textslide._font("bold", size)


def _parole(testo: str) -> list[list[tuple[str, bool]]]:
    """'**Non ne dipendono.** Le usano **voto**.' -> parole fatte di pezzi (testo, rosso), così la punteggiatura
    attaccata a una parola rossa ('voto' rosso + '.' bianco) resta senza spazio in mezzo."""
    parole: list[list[tuple[str, bool]]] = []
    attaccato = False
    for i, pezzo in enumerate(re.split(r"\*\*", testo or "")):
        rosso = i % 2 == 1
        if not pezzo:
            continue
        toks = pezzo.split()
        if not toks:
            attaccato = False
            continue
        for k, t in enumerate(toks):
            if k == 0 and parole and attaccato and not pezzo[0].isspace():
                parole[-1].append((t, rosso))
            else:
                parole.append([(t, rosso)])
        attaccato = not pezzo[-1].isspace()
    return parole


def _testo(parola: list[tuple[str, bool]]) -> str:
    return "".join(t for t, _ in parola)


def _righe(draw, testo: str, font, max_w: int) -> list[list[list[tuple[str, bool]]]]:
    righe, cur = [], []
    for p in _parole(testo):
        prova = " ".join(_testo(w) for w in cur + [p])
        if cur and draw.textlength(prova, font=font) > max_w:
            righe.append(cur)
            cur = [p]
        else:
            cur.append(p)
    if cur:
        righe.append(cur)
    return righe


def textslide_wrap(draw, testo: str, font, max_w: int) -> list[str]:
    """La domanda in alto su due righe di lunghezza simile (come nel modello di Gabriele); se è lunga, a capo
    normale, massimo 3 righe."""
    parole = (testo or "").split()
    if len(parole) >= 4:
        migliore = None
        for i in range(1, len(parole)):
            a, b = " ".join(parole[:i]), " ".join(parole[i:])
            la, lb = draw.textlength(a, font=font), draw.textlength(b, font=font)
            if max(la, lb) <= max_w and (migliore is None or max(la, lb) < migliore[0]):
                migliore = (max(la, lb), [a, b])
        if migliore:
            return migliore[1]
    righe, cur = [], ""
    for p in (testo or "").split():
        prova = (cur + " " + p).strip()
        if cur and draw.textlength(prova, font=font) > max_w:
            righe.append(cur)
            cur = p
        else:
            cur = prova
    if cur:
        righe.append(cur)
    return righe[:3]


def sfondi_disponibili(slug: str) -> list[Path]:
    d = config.ROOT / "assets" / "storie_sfondi" / slug
    return sorted(d.glob("*.jpg")) if d.exists() else []


def scegli_sfondo(slug: str, categoria: str) -> Path | None:
    tutte = sfondi_disponibili(slug)
    if not tutte:
        return None
    usate = [s.get("sfondo_file") for s in _storico()[-10:]]
    giuste = [p for p in tutte if p.name.startswith(categoria + "_")] or tutte
    fresche = [p for p in giuste if p.name not in usate] or giuste
    return random.choice(fresche)


def _base(slug: str, foto: Path | None) -> Image.Image:
    """Foto a tutta pagina (ritaglio 9:16) con velo nero al 55%; senza foto: tinta unita della pagina."""
    if foto is None:
        return Image.new("RGB", (W, H), PALETTE_PAGINA.get(slug, PALETTE_PAGINA["attraiseduci"])["bg"])
    im = Image.open(foto).convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x, y = (im.width - W) // 2, max(0, int((im.height - H) * 0.3))      # il viso di solito sta in alto
    im = im.crop((x, y, x + W, y + H))
    return Image.blend(im, Image.new("RGB", (W, H), (0, 0, 0)), 0.55)


def disegna(blocchi: list[str], prima: bool, brand: dict, base: Image.Image,
            placements: list | None = None, domanda: str = "") -> tuple[Image.Image, Image.Image]:
    """Rettangoli neri pieni allineati a sinistra, testo bianco con parole chiave rosse. Restituisce
    (storia finita, sfondo con i rettangoli ma senza testo, per Canva)."""
    img = base.copy()
    draw = ImageDraw.Draw(img)
    max_w = W - 2 * MARGINE_X - 2 * PAD_X
    zona_alta = H // 2 + 40 if prima else TOP_SAFE         # slide 1: metà alta libera per lo sticker
    zona_bassa = H - BOTTOM_SAFE
    if prima and domanda:
        # risposta al box domande: domanda del follower in alto (25%), gancio al centro (45-50%)
        zona_alta, zona_bassa = int(H * 0.36), int(H * 0.64)
    size = 58 if prima else 50
    while True:
        f = _font(FONT_BOLD, size)
        lh = int(size * 1.22)
        tutte = [_righe(draw, b, f, max_w) for b in blocchi]
        alto = sum(len(r) * lh + 2 * PAD_Y for r in tutte) + GAP * (len(tutte) - 1)
        if alto <= zona_bassa - zona_alta or size <= 34:
            break
        size -= 2
    y = zona_alta + max(0, (zona_bassa - zona_alta - alto) // 2)
    sfondo = base.copy()
    righe_out = []
    for righe in tutte:
        h_blocco = len(righe) * lh + 2 * PAD_Y
        for d in (draw, ImageDraw.Draw(sfondo)):
            d.rectangle((MARGINE_X, y, W - MARGINE_X, y + h_blocco), fill=(0, 0, 0))
        ty = y + PAD_Y - int(size * 0.08)
        for r in righe:
            x = MARGINE_X + PAD_X
            runs = []
            for k, parola in enumerate(r):
                for j, (t, rosso) in enumerate(parola):
                    col = ROSSO if rosso else (255, 255, 255)
                    draw.text((x, ty), t, font=f, fill=col)
                    x += draw.textlength(t, font=f)
                    ultimo = j == len(parola) - 1
                    runs.append([t + (" " if ultimo and k < len(r) - 1 else ""), col])
                if k < len(r) - 1:
                    x += draw.textlength("a a", font=f) - draw.textlength("aa", font=f)   # lo spazio da solo misura 0
            righe_out.append({"testo": " ".join(_testo(w) for w in r), "x": float(MARGINE_X + PAD_X), "y": float(ty),
                              "size": size, "weight": "bold_carlito", "color": (255, 255, 255), "runs": runs})
            ty += lh
        y += h_blocco + GAP
    if prima and domanda:
        qf = _font(FONT_GRASSETTO_CORSIVO, 30)                  # piccola e stretta (modello: 17pt contro 46 del gancio)
        q_righe = textslide_wrap(draw, domanda, qf, int(W * 0.62))
        qy = int(H * 0.25) - len(q_righe) * 38 // 2
        for r in q_righe:
            qx = (W - draw.textlength(r, font=qf)) / 2
            draw.text((qx, qy), r, font=qf, fill=(255, 255, 255))
            righe_out.append({"testo": r, "x": float(qx), "y": float(qy), "size": 30, "weight": "bold_carlito",
                              "color": (255, 255, 255)})
            qy += 38
    nome = "@" + (brand.get("handle") or brand["nome"])
    nf = _font(FONT_CORSIVO, 30)
    nx, ny = W - MARGINE_X - draw.textlength(nome, font=nf), H - BOTTOM_SAFE + 40
    draw.text((nx, ny), nome, font=nf, fill=GRIGIO)
    righe_out.append({"testo": nome, "x": float(nx), "y": float(ny), "size": 30, "weight": "italic_carlito", "color": GRIGIO})
    if placements is not None:
        placements.extend(righe_out)
    return img, sfondo


def _disegna_serie(serie: dict, brand: dict, slug: str, jdir: Path, nome: str) -> tuple[list[Path], Path | None]:
    foto = scegli_sfondo(slug, serie.get("sfondo") or "sicura")
    base = _base(slug, foto)
    serie["sfondo_file"] = foto.name if foto else ""
    jdir.mkdir(parents=True, exist_ok=True)
    paths, slides = [], []
    for i, s in enumerate(serie.get("slides") or [], 1):
        pl: list = []
        img, sf = disegna(s.get("blocchi") or [], i == 1, brand, base, pl,
                          domanda=(serie.get("domanda") or "").strip() if serie.get("_mostra_domanda") else "")
        p = jdir / f"{nome}_{i:02d}.jpg"
        img.save(p, quality=95)
        bg = jdir / f"{nome}_{i:02d}_sfondo.jpg"
        sf.save(bg, quality=95)
        paths.append(p)
        slides.append({"_canva": {"tipo": "testo", "righe": pl, "sfondo": str(bg)}})
    canva_path = None
    try:
        from . import canva
        canva_path = canva.build({"slides": slides}, paths, jdir / f"{nome.capitalize()}_{slug}.pptx")
    except Exception as e:
        log.warning("File Canva delle storie non creato: %s", e)
    return paths, canva_path


def crea(brand: dict, slug: str, tema: str, tipo: str | None, rules: list[dict], jdir: Path) -> tuple[dict, list[Path], Path | None]:
    serie = scrivi(brand, tema, tipo, rules)
    paths, canva_path = _disegna_serie(serie, brand, slug, jdir, "storia")
    serie["tema"] = tema
    _ricorda({"tipo": serie.get("tipo"), "tema": tema, "sfondo_file": serie.get("sfondo_file"),
              "quando": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return serie, paths, canva_path


def istruzioni(serie: dict) -> str:
    """Il testo che accompagna le storie."""
    tipo = serie.get("tipo", "")
    righe = [f"STORIE · {tipo} · {serie.get('tema', '')}"]
    if tipo == "domande":
        righe.append(f"Metti lo sticker DOMANDA nella metà alta: \"{serie.get('sticker_testo', '')}\".")
        righe.append("Quando arrivano le domande, mandami lo screenshot di ognuna con scritto \"risposta\" "
                     "(o /risposta <domanda>): ogni risposta sono 3 slide, con la metà alta libera per lo sticker.")
    else:
        righe.append("Slide 1: la metà alta è libera per lo sticker (es. box domanda).")
    righe.append("Il file Canva ha i testi modificabili.")
    return "\n".join(righe)


# ---------------------------------------------------------------- risposte ai box domanda

RISPOSTA_TOOL = {
    "name": "risposta_storia",
    "description": "Risposta in 3 slide a una domanda arrivata dal box domande delle storie.",
    "input_schema": {"type": "object", "properties": {
        "domanda": {"type": "string", "description": "La domanda trascritta parola per parola (corretta solo negli errori di battitura evidenti)."},
        "chiusura": {"type": "string", "enum": ["didattica", "vendita"],
                     "description": "vendita se la domanda tocca da vicino il dolore di un nostro manuale, altrimenti didattica."},
        "sfondo": {"type": "string", "enum": SFONDI},
        "slides": {"type": "array", "items": _SLIDE, "description": "Esattamente 3 slide: gancio che risponde alla domanda, didattica, chiusura."},
    }, "required": ["domanda", "chiusura", "sfondo", "slides"]},
}


def rispondi(brand: dict, slug: str, rules: list[dict], jdir: Path, foto: bytes | None = None,
             domanda: str = "", chiusura: str | None = None) -> tuple[dict, list[Path], Path | None]:
    """Legge la domanda (dalla foto del box domande o dal testo) e prepara la risposta in 3 slide nel formato
    delle storie: sulla prima la metà alta resta libera per lo sticker con la domanda (lo mette Instagram)."""
    content = []
    if foto:
        content += [{"type": "text", "text": "Screenshot della domanda arrivata nel box domande:"}, writer._image_block(foto, 1024)]
    if domanda:
        content.append({"type": "text", "text": f"Domanda: {domanda}"})
    if not brand.get("manuali"):
        chiusura = "didattica"
    forza = (f"La chiusura deve essere {chiusura.upper()}." if chiusura else
             "Scegli tu la chiusura: vendita solo se la domanda tocca da vicino il dolore di un manuale.")
    content.append({"type": "text", "text": "MODELLO RISPOSTA BOX DOMANDE: rispondi in 3 slide (gancio che risponde alla "
                    "domanda, didattica, chiusura). In domanda scrivi solo la domanda del follower (niente 'Risposta a "
                    "@username', niente nomi o dati personali): il bot la stampa in alto sulla prima slide. Slide 1: un "
                    "solo blocco di 3-4 righe, negazione netta, verità ribaltata, conseguenza. Slide 2: 2-3 blocchi; nel "
                    "primo, se l'idea è davvero di un autore della lista, nominalo (regola autori). Slide 3: chiusura "
                    "didattica aperta ('Finché non capirai…, continuerai a…') oppure, se vendita, 3 blocchi: amplifica il "
                    "problema, il manuale come tassello mancante ('Nel manuale \"[titolo]\" spiego…'), costo del non "
                    "agire + 'Ti lascio qui il link del manuale.' Tono duro e lucido, mai moralizzante, mai "
                    "motivazionale, mai da coach gentile. " + forza + " Usa lo strumento risposta_storia."})
    out = writer._tool_call(_system(brand, rules), content, RISPOSTA_TOOL, model=config.CAPTION_MODEL, max_tokens=4000)
    if chiusura:
        out["chiusura"] = chiusura
    out["slides"] = (out.get("slides") or [])[:3]
    _pulisci(out, brand)
    out["domanda"] = writer.clean_text(writer.censor(out.get("domanda") or domanda or "", brand))
    out["_mostra_domanda"] = True                       # la domanda in alto sulla prima slide, al 25%
    paths, canva_path = _disegna_serie(out, brand, slug, jdir, "risposta")
    _ricorda({"tipo": "risposta_" + str(out.get("chiusura")), "tema": out.get("domanda", ""),
              "sfondo_file": out.get("sfondo_file"), "quando": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return out, paths, canva_path
