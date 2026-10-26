"""Slide di solo testo su sfondo uniforme: il testo italiano lo scrive il codice, non Gemini.

Per questo tipo di slide (frasi su sfondo scuro o chiaro, stile tweet) l'IA che genera immagini
sbaglia spesso le lettere. Qui invece:
1. si riconosce lo sfondo (colore dei bordi) e si verifica che la slide sia quasi tutta sfondo;
2. si trovano i pixel del testo (e di eventuali watermark/avatar) e si cancellano col colore dello sfondo;
3. si misurano posizione, allineamento, dimensione, colore e peso del testo originale;
4. si scrive il testo italiano nello stesso punto e nello stesso stile, con ortografia perfetta.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import config

FONTS = {
    "regular": config.ROOT / "assets" / "testo_regular.otf",
    "semibold": config.ROOT / "assets" / "testo_semibold.otf",
    "bold": config.ROOT / "assets" / "testo_bold.otf",
    "titolo": config.ROOT / "assets" / "titolo_anton.ttf",          # titoli stretti e pesanti (stile Anton/Bebas)
    "stretto": config.ROOT / "assets" / "titolo_oswald_bold.ttf",   # stretto ma meno pesante (stile Oswald)
    "fumetto": config.ROOT / "assets" / "fumetto_chewy.ttf",        # lettering a mano dei fumetti
    "elegante": config.ROOT / "assets" / "serif_dm.ttf",            # titoli eleganti e autorevoli (post creati)
    # testo con lettere strette e ravvicinate (stile Helvetica Neue / SF compatto)
    "regular_tight": config.ROOT / "assets" / "stretto_testo_regular.ttf",
    "semibold_tight": config.ROOT / "assets" / "stretto_testo_semibold.ttf",
    "bold_tight": config.ROOT / "assets" / "stretto_testo_bold.ttf",
}
for _fam in ("montserrat", "poppins", "arimo"):
    for _w in ("regular", "semibold", "bold"):
        FONTS[f"{_w}_{_fam}"] = config.ROOT / "assets" / f"{_fam}_{_w}.ttf"
SAFE_X = 0.065   # margine laterale minimo: dentro la zona che Instagram mostra sempre (anche nella griglia 3:4)
SAFE_Y = 0.05    # margine minimo sopra e sotto
for _fam, _file in (("gaegu", "mano_gaegu.ttf"), ("schoolbell", "mano_schoolbell.ttf"), ("architects", "mano_architects.ttf")):
    for _w in ("regular", "semibold", "bold"):                 # scrittura a mano: un solo spessore
        FONTS[f"{_w}_{_fam}"] = config.ROOT / "assets" / _file
FAMILIES = ["", "_tight", "_montserrat", "_poppins", "_arimo", "_gaegu", "_schoolbell", "_architects"]   # Inter, Inter Tight, Montserrat, Poppins, Arimo (tipo Helvetica/Arial)


@dataclass
class Layout:
    bg: tuple
    color: tuple
    weight: str
    align: str            # left | center
    left: int
    top: int
    bottom: int
    width: int            # larghezza massima del blocco di testo
    size: int             # dimensione del carattere stimata
    centered_v: bool
    mask: np.ndarray      # pixel da cancellare (testo + marchi)
    line_ratio: float = 1.32
    blank_ratio: float = 0.8
    right: int = 0        # bordo destro del testo originale (per capire se il blocco era centrato nella slide)
    head_color: tuple | None = None   # colore del primo paragrafo (titoletto) se diverso dal resto
    para_colors: list | None = None   # parti di colore diverso dell'originale [(colore, righe)] (es. grigio poi bianco)
    head_weight: str = ""


import re as _re
_EMOJI = _re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u2B00-\u2BFF]")


def no_emoji(text: str) -> str:
    """Le emoji non esistono nei font delle slide (uscirebbe un quadratino): si tolgono."""
    t = _re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text or ""))
    return "\n".join(line.strip() for line in t.split("\n")).strip()


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    p = FONTS.get(weight) or FONTS["regular"]
    if not p.exists():
        p = FONTS["bold"] if weight in ("titolo", "stretto") else FONTS["regular"]
    if p.exists():
        return ImageFont.truetype(str(p), size)
    return ImageFont.truetype(str(config.FONT_PATH), size) if config.FONT_PATH.exists() else ImageFont.load_default(size=size)


def _runs(flags: np.ndarray, min_gap: int = 1) -> list[tuple[int, int]]:
    out, start, gap = [], None, 0
    for i, f in enumerate(flags):
        if f:
            if start is None:
                start = i
            gap = 0
            end = i
        elif start is not None:
            gap += 1
            if gap > min_gap:
                out.append((start, end))
                start, gap = None, 0
    if start is not None:
        out.append((start, end))
    return out


def _size_from_text(text_lines, original_text: str, weight: str) -> int | None:
    """Corpo del carattere ricavato confrontando la larghezza delle righe misurate con il testo letto da Claude."""
    orig = [ln.strip() for ln in (original_text or "").split("\n") if ln.strip()]
    if not orig or len(orig) != len(text_lines):
        return None
    probe = _font(weight, 100)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    est = []
    for ln, (y0, y1, x0, x1) in zip(orig, text_lines):
        w100 = d.textlength(ln, font=probe)
        if w100 > 50 and len(ln) >= 4:
            est.append(100 * (x1 - x0 + 1) / w100)
    return int(np.median(est)) if est else None


def _identify_font(ink: np.ndarray, text_lines: list, orig_lines: list[str]):
    """Restituisce (nome font, dimensione) del font disponibile più simile all'originale, o None."""
    if not orig_lines or len(orig_lines) != len(text_lines):
        return None
    pairs = sorted([(t, l) for t, l in zip(orig_lines, text_lines) if len(t) >= 6],
                   key=lambda p: -(p[1][3] - p[1][2]))[:3]
    if not pairs:
        return None
    crops = []
    for t, (y0, y1, x0, x1) in pairs:
        c = ink[y0:y1 + 1, x0:x1 + 1]
        ys, xs = np.where(c)
        if len(xs) < 30:
            continue
        crops.append((t, c[ys.min():ys.max() + 1, xs.min():xs.max() + 1], x1 - x0 + 1))
    if not crops:
        return None
    cand = []
    for fam in FAMILIES:
        for w in ("regular", "semibold", "bold"):
            name = w + fam
            f = _font(name, 100)
            scores, sizes = [], []
            for t, crop, width in crops:
                bb = f.getbbox(t)
                tw, th = bb[2] - bb[0], bb[3] - bb[1]
                if tw <= 0 or th <= 0:
                    continue
                im = Image.new("L", (tw + 4, th + 4), 0)
                ImageDraw.Draw(im).text((2 - bb[0], 2 - bb[1]), t, font=f, fill=255)
                ch, cw = crop.shape
                r = np.asarray(im.resize((cw, ch))) > 110
                # tratti sottili (scrittura a mano): si confrontano le forme un po' "ingrossate",
                # altrimenti basta un pixel di spostamento per non sovrapporsi
                import cv2
                prop = min(th / tw, ch / cw) / max(th / tw, ch / cw)   # a parità di larghezza l'altezza deve tornare
                raw = (r & crop).sum() / max(1, (r | crop).sum()) * prop
                k = max(1, int(ch * 0.06))
                ker = np.ones((k, k), np.uint8)
                rd = cv2.dilate(r.astype(np.uint8), ker) > 0
                cd = cv2.dilate(crop.astype(np.uint8), ker) > 0
                thick = (rd & cd).sum() / max(1, (rd | cd).sum()) * prop
                scores.append((raw, thick))
                # dimensione: media tra quella che dà la stessa larghezza e quella che dà la stessa altezza
                sizes.append(50 * width / tw + 50 * ch / th)
            if scores:
                cand.append((name, int(np.median(sizes)), float(np.mean([a_ for a_, _ in scores])),
                             float(np.mean([b_ for _, b_ in scores]))))
    if not cand:
        return None
    # prima il confronto preciso; se nessun font somiglia abbastanza (tratti sottili, scrittura a mano)
    # si confrontano le forme ingrossate
    top = max(cand, key=lambda c_: c_[2])
    if top[2] > 0.25:
        return top[0], top[1]
    HAND = ("_gaegu", "_schoolbell", "_architects")
    hand = [c_ for c_ in cand if c_[0].endswith(HAND)]
    if not hand:
        return None
    top = max(hand, key=lambda c_: c_[3])                                   # solo i font scritti a mano
    base = max((c_[3] for c_ in cand if not c_[0].endswith(HAND)), default=0)
    # si cambia font solo se è chiaramente più simile di Inter (altrimenti resta il riconoscimento normale)
    return (top[0], top[1]) if top[3] > 0.25 and top[3] > base else None


def analyze(img: Image.Image, original_text: str = "", has_mark: bool = False) -> Layout | None:
    """Restituisce il layout se la slide è testo su sfondo uniforme, altrimenti None."""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    H, W, _ = a.shape
    b = max(4, int(min(H, W) * 0.02))
    border = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3),
                             a[:, :b].reshape(-1, 3), a[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.abs(a - bg).max(axis=2)
    if (dist < 30).mean() < 0.78:          # troppa grafica: non è una slide di solo testo
        return None
    if np.percentile(np.abs(border - bg).max(axis=1), 90) > 25:   # bordi non uniformi (gradiente o foto)
        return None

    ink = dist > 70
    if ink.sum() < 50:
        return None
    lines = []
    prof = ink.sum(axis=1)
    raw = _runs(prof >= 2, min_gap=1)
    # quante righe di testo ha l'originale (dalla trascrizione di Claude, che mantiene gli a capo)
    n_orig = len([ln for ln in (original_text or "").split("\n") if ln.strip()])
    text_runs = [r for r in raw if r[0] < H * 0.9 and r[1] - r[0] >= 6]
    unit = None
    if n_orig > len(text_runs) >= 1:
        # interlinea serrata: più righe si toccano e sembrano un blocco unico. Si divide ogni blocco
        # in parti uguali, tante quante le righe che contiene
        unit = sum(r[1] - r[0] + 1 for r in text_runs) / n_orig
    for y0, y1 in raw:
        parts = [(y0, y1)]
        if unit and y0 < H * 0.9:
            k = int(round((y1 - y0 + 1) / unit))
            if k >= 2:
                step = (y1 - y0 + 1) / k
                parts = [(int(y0 + q * step), int(y0 + (q + 1) * step) - 1) for q in range(k)]
        for p0, p1 in parts:
            cols = np.where(ink[p0:p1 + 1].any(axis=0))[0]
            if len(cols):
                lines.append((p0, p1, int(cols.min()), int(cols.max())))
    if not lines:
        return None
    heights = np.array([l[1] - l[0] + 1 for l in lines])
    med_h = float(np.median(heights))
    # un blocco molto più alto di una riga di testo, fuori dalle fasce alta e bassa dei marchi, è un'illustrazione:
    # quella slide la lascia a Gemini per non cancellarla
    header_rows = []
    for (y0, y1, x0, x1), h in zip(lines, heights):
        if h > 2.6 * med_h and y0 > H * 0.22 and y1 < H * 0.86:
            # con un marchio di un'altra pagina, un elemento piccolo è il suo avatar (stile tweet): si cancella
            if has_mark and h < H * 0.14:
                header_rows.append((y0, y1))
                continue
            return None
    if header_rows:
        # le righe accanto all'avatar (nome e @handle della pagina) non sono testo da tradurre
        hy0 = min(r[0] for r in header_rows)
        hy1 = max(r[1] for r in header_rows)
        keep = [not (l[1] >= hy0 - 4 and l[0] <= hy1 + 4) for l in lines]
        lines = [l for l, k in zip(lines, keep) if k]
        heights = np.array([l[1] - l[0] + 1 for l in lines])
        if not lines:
            return None
        med_h = float(np.median(heights))
    # righe di testo vere: altezza simile alla mediana, non nella fascia in basso dove stanno i marchi
    text_lines = [l for l, h in zip(lines, heights)
                  if 0.45 * med_h <= h <= 1.9 * med_h and l[0] < H * 0.9]
    if not text_lines:
        return None
    t_heights = [l[1] - l[0] + 1 for l in text_lines]
    lefts = np.array([l[2] for l in text_lines])
    rights = np.array([l[3] for l in text_lines])
    centers = (lefts + rights) / 2
    align = "left" if np.std(lefts) <= max(6, W * 0.012) else ("center" if np.std(centers) <= W * 0.03 else "left")
    left = int(np.min(lefts)) if align == "left" else int(np.min(lefts))
    top, bottom = text_lines[0][0], text_lines[-1][1]
    size = int(np.percentile(t_heights, 75) / 0.93)    # riga con maiuscole e discendenti ≈ 0.93 del corpo

    strong = dist > 120
    color = tuple(int(c) for c in np.median(a[strong], axis=0)) if strong.any() else (255, 255, 255)
    # peso: spessore medio dei tratti verticali (lunghezza delle corse orizzontali di inchiostro) rispetto al corpo
    runs = []
    for l in text_lines:
        mid = (l[0] + l[1]) // 2
        for y in range(max(l[0], mid - 3), min(l[1], mid + 3) + 1):
            runs += [e - s0 + 1 for s0, e in _runs(ink[y, l[2]:l[3] + 1], min_gap=0)]
    stroke = (np.median(runs) / max(size, 1)) if runs else 0.1
    weight = "bold" if stroke > 0.125 else ("semibold" if stroke > 0.105 else "regular")
    # RICONOSCIMENTO DEL FONT: ogni riga dell'originale (con il suo testo letto da Claude) si confronta con la
    # stessa riga scritta in ogni font e spessore disponibile, portata alla stessa larghezza: vince quello
    # con la forma più simile (sovrapposizione dei pixel). Da lì si ricava anche la dimensione esatta.
    orig_lines = [ln.strip() for ln in (original_text or "").split("\n") if ln.strip()]
    family, found = "", _identify_font(ink, text_lines, orig_lines)
    if found:
        wname, size = found
        stroke_w = weight                              # spessore misurato sui tratti (affidabile)
        weight, _, family = wname.partition("_")
        family = "_" + family if family else ""
        order = ["regular", "semibold", "bold"]
        if stroke_w in order and abs(order.index(stroke_w) - order.index(weight)) > 1:
            weight = stroke_w                          # forma giusta ma spessore sbagliato: vale lo spessore misurato
    else:
        h75 = float(np.percentile(t_heights, 75))
        for cand in range(max(12, int(h75 * 0.6)), int(h75 * 1.7) + 1):
            bb = _font(weight, cand).getbbox("Hgjy")
            if bb[3] - bb[1] >= h75:
                size = cand
                break
        weight = weight

    # interlinea misurata: distanza tipica tra l'inizio di due righe consecutive
    tops = [l[0] for l in text_lines]
    gaps = np.diff(tops) if len(tops) > 1 else np.array([size * 1.32])
    pitch = float(np.percentile(gaps, 30)) if len(gaps) else size * 1.32
    step0 = float(np.min(gaps)) if len(tops) > 1 else pitch
    # interlinea vera (anche serratissima, righe che quasi si toccano) e stacco vero tra i paragrafi
    line_ratio = float(np.clip(step0 / max(size, 1), 0.95, 1.8))
    big = [g - step0 for g in gaps if g > step0 * 1.22]
    blank_ratio = float(np.clip((np.median(big) / max(size, 1)) if big else 0.8, 0.12, 1.6))
    # titoletto colorato: il primo paragrafo ha un colore diverso dal resto (es. "3. Titolo" in rosso)
    head_color, head_weight = None, ""
    def _line_color(l):
        reg = a[l[0]:l[1] + 1, l[2]:l[3] + 1]
        m = np.abs(reg - bg).max(axis=2) > 120
        return np.median(reg[m], axis=0) if m.sum() > 10 else None
    para_end = len(text_lines)
    for k in range(1, len(text_lines)):
        if text_lines[k][0] - text_lines[k - 1][0] > pitch * 1.4:
            para_end = k
            break
    if 0 < para_end < len(text_lines):
        hc = [c for c in (_line_color(l) for l in text_lines[:para_end]) if c is not None]
        bc = [c for c in (_line_color(l) for l in text_lines[para_end:]) if c is not None]
        if hc and bc:
            h, b_ = np.median(hc, axis=0), np.median(bc, axis=0)
            if np.abs(h - b_).max() > 60:
                head_color = tuple(int(c) for c in h)
                color = tuple(int(c) for c in b_)
                hr = []
                for l in text_lines[:para_end]:
                    mid = (l[0] + l[1]) // 2
                    hr += [e - s0 + 1 for s0, e in _runs(ink[mid, l[2]:l[3] + 1], min_gap=0)]
                st = (np.median(hr) / max(size, 1)) if hr else 0.1
                head_weight = "bold" if st > 0.125 else ("semibold" if st > 0.105 else "regular")
                br = []
                for l in text_lines[para_end:]:
                    mid = (l[0] + l[1]) // 2
                    for yy in range(max(l[0], mid - 3), min(l[1], mid + 3) + 1):
                        br += [e - s0 + 1 for s0, e in _runs(ink[yy, l[2]:l[3] + 1], min_gap=0)]
                if br:                          # peso del testo normale, senza il titoletto
                    sb = np.median(br) / max(size, 1)
                    weight = "bold" if sb > 0.125 else ("semibold" if sb > 0.105 else "regular")
    # paragrafi dell'originale (anche con stacchi piccoli) e il loro colore: es. prima parte grigia, poi bianca
    para_colors = None
    groups, cur = [], [text_lines[0]]
    step = float(np.min(gaps)) if len(tops) > 1 else pitch      # distanza tra righe dello stesso paragrafo
    for k in range(1, len(text_lines)):
        if text_lines[k][0] - text_lines[k - 1][0] > step * 1.22:
            groups.append(cur)
            cur = []
        cur.append(text_lines[k])
    groups.append(cur)
    if len(groups) >= 2:
        cols = []
        for g in groups:
            cs = [c for c in (_line_color(l) for l in g) if c is not None]
            cols.append(np.median(cs, axis=0) if cs else None)
        if all(c is not None for c in cols):
            arr = np.array(cols)
            if (arr.max(axis=0) - arr.min(axis=0)).max() > 60:
                # paragrafi vicini con lo stesso colore diventano un'unica parte: [(colore, righe), ...]
                segs = []
                for c, g in zip(cols, groups):
                    c = tuple(int(v) for v in c)
                    if segs and max(abs(a_ - b_) for a_, b_ in zip(segs[-1][0], c)) < 40:
                        segs[-1] = (segs[-1][0], segs[-1][1] + len(g))
                    else:
                        segs.append((c, len(g)))
                para_colors = segs
                head_color = None                    # i colori per paragrafo comprendono il titoletto
                # colore principale = quello del paragrafo più chiaro/contrastato (il testo "forte")
                color = max((c for c, _ in para_colors), key=lambda c: abs(np.array(c) - bg).max())
    if family:
        weight = weight + family
        if head_weight:
            head_weight = head_weight + family
    block_center = (top + bottom) / 2
    if align == "center":
        width = int(min(W * 0.9, (np.max(rights) - np.min(lefts)) * 1.1))
    else:
        width = int(W - max(left, W * SAFE_X) - max(left, W * SAFE_X))
    # maschera da cancellare: tutto ciò che non è sfondo (testo, watermark, avatar), un po' allargata
    # soglia di cancellazione in base al rumore dello sfondo: su un nero pulito si tolgono anche gli aloni grigi
    noise = float(np.percentile(np.abs(border - bg).max(axis=1), 95))
    erase = dist > max(10.0, noise * 2 + 4)
    erase = _dilate(erase, max(2, size // 10))
    return Layout(bg=tuple(int(c) for c in bg), color=color, weight=weight, align=align, left=left,
                  top=int(top), bottom=int(bottom), width=width, size=max(14, size),
                  centered_v=abs(block_center - H / 2) < H * 0.03, mask=erase,
                  line_ratio=line_ratio, blank_ratio=blank_ratio, right=int(np.max(rights)),
                  head_color=head_color, head_weight=head_weight, para_colors=para_colors)


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    out = m.copy()
    for dy in range(-r, r + 1, max(1, r // 2)):
        for dx in range(-r, r + 1, max(1, r // 2)):
            out |= np.roll(np.roll(m, dy, axis=0), dx, axis=1)
    return out


def _clean(img: Image.Image, lay: Layout) -> Image.Image:
    a = np.asarray(img.convert("RGB")).copy()
    m = lay.mask
    # riempie riga per riga con il colore di sfondo di quella riga (regge anche lievi sfumature verticali)
    for y in range(a.shape[0]):
        row_m = m[y]
        if row_m.any():
            keep = ~row_m
            col = np.median(a[y][keep], axis=0) if keep.sum() > 10 else np.array(lay.bg)
            a[y][row_m] = col.astype(np.uint8)
    # aloni grigi rimasti attorno al testo cancellato (compressione JPEG): nella fascia del testo
    # tutto ciò che è vicino al colore di sfondo diventa sfondo pieno
    H = a.shape[0]
    y0, y1 = 0, H          # tutta la slide: anche l'ombra del watermark cancellato in basso
    band = a[y0:y1].astype(np.int16)
    near = np.abs(band - np.array(lay.bg)).max(axis=2) < 70
    band[near] = np.array(lay.bg)
    a[y0:y1] = band.astype(np.uint8)
    return Image.fromarray(a)


def _greedy(words, width_of, max_w):
    lines, line = [], []
    for w in words:
        if not line or width_of(line + [w]) <= max_w:
            line.append(w)
        else:
            lines.append(line)
            line = [w]
    if line:
        lines.append(line)
    return lines


def _balanced(words, width_of, max_w):
    """Stesso numero di righe del ritorno a capo normale, ma lunghe uguali: niente parola sola nell'ultima riga."""
    lines = _greedy(words, width_of, max_w)
    if len(lines) < 2:
        return lines
    lo, hi = max(width_of([w]) for w in words), max_w
    best = lines
    while hi - lo > 2:
        mid = (lo + hi) / 2
        cand = _greedy(words, width_of, mid)
        if len(cand) <= len(lines):
            best, hi = cand, mid
        else:
            lo = mid
    return best


def _flow(draw, words: list[str], font, max_w: int) -> list[str]:
    return [" ".join(l) for l in _balanced(words, lambda ws: draw.textlength(" ".join(ws), font=font), max_w)]


def _flow_old(draw, words: list[str], font, max_w: int) -> list[str]:
    lines, line = [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if not line or draw.textlength(test, font=font) <= max_w:
            line = test
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Mantiene gli a capo dell'originale; se in un paragrafo una riga non ci sta, ridistribuisce
    tutto il paragrafo in modo uniforme (niente parole rimaste da sole)."""
    out: list[str] = []
    paragraphs = text.split("\n\n")
    for k, para in enumerate(paragraphs):
        rows = [r.strip() for r in para.split("\n") if r.strip()]
        if rows and all(draw.textlength(r, font=font) <= max_w for r in rows):
            out += rows
        elif rows:
            out += _flow(draw, " ".join(rows).split(), font, max_w)
        if k < len(paragraphs) - 1:
            out.append("")
    return out


def _is_flat(img: Image.Image, lay: Layout) -> bool:
    """Sfondo davvero a tinta unita (niente sfumature né texture)?"""
    a = np.asarray(img.convert("RGB").resize((108, 135))).astype(np.int16)
    bgp = np.abs(a - np.array(lay.bg)).max(axis=2) < 40          # pixel di sfondo (il resto è testo)
    if bgp.mean() < 0.6:
        return False
    return float(np.percentile(np.abs(a[bgp] - np.array(lay.bg)).max(axis=1), 95)) <= 5   # grana/carta: non piatto


def background(img: Image.Image, lay: Layout) -> Image.Image:
    """La slide senza testo. Se lo sfondo è a tinta unita si ricrea da zero dello stesso colore identico:
    nessuna sbavatura possibile. Altrimenti si cancella il testo riga per riga."""
    if _is_flat(img, lay):
        return Image.new("RGB", img.size, tuple(int(c) for c in lay.bg))
    return _clean(img, lay)


def render(img: Image.Image, lay: Layout, text: str, placements: list | None = None) -> Image.Image:
    """Scrive il testo. Se `placements` è una lista, ci aggiunge (riga, x, y, corpo, peso, colore) per il file Canva."""
    out = background(img, lay)
    text = no_emoji(text)
    if not text:
        return out
    W, H = out.size
    draw = ImageDraw.Draw(out)
    size = lay.size
    max_h = H * 0.84
    while True:
        font = _font(lay.weight, size)
        lines = _wrap(draw, text, font, lay.width)
        line_h = int(size * lay.line_ratio)
        blank_h = int(size * lay.blank_ratio)
        total = sum(blank_h if not ln else line_h for ln in lines)
        if total <= max_h or size <= 14:
            break
        size -= 2
    if lay.centered_v:
        y = int((H - total) / 2)
    else:
        y = max(int(H * max(0.06, SAFE_Y)), min(lay.top - int(size * 0.2), int(H - total - H * 0.08)))
    left = max(lay.left, int(W * SAFE_X))
    if lay.align != "center" and lay.right and abs((lay.left + lay.right) / 2 - W / 2) < W * 0.02:
        # testo allineato a sinistra ma blocco centrato nella slide: si ricentra il blocco italiano
        block_w = max((draw.textlength(ln, font=font) for ln in lines if ln), default=0)
        left = int((W - block_w) / 2)
    in_head = bool(lay.head_color or lay.head_weight)   # titoletto: colore e/o spessore suoi (es. rosso grassetto)
    # colore di ogni paragrafo italiano: si segue la posizione nel testo (prima parte grigia, poi bianca, ecc.)
    seg_of_line = []
    if lay.para_colors:
        tot_o = sum(n for _, n in lay.para_colors)
        bounds, acc = [], 0
        for _, n in lay.para_colors:
            acc += n
            bounds.append(acc / tot_o)
        real = [ln for ln in lines if ln]
        tot_i, done, k = max(1, len(real)), 0, 0
        para_start = []
        for ln in lines:
            if not ln:
                k = 0
                continue
            if k == 0:
                para_start.append(done)
            k += 1
            done += 1
        para_seg = []
        for st in para_start:
            frac = (st + 0.5) / tot_i
            para_seg.append(next((i for i, b_ in enumerate(bounds) if frac <= b_), len(bounds) - 1))
    para = 0
    for ln in lines:
        if not ln:
            y += blank_h
            in_head = False                     # il titoletto colorato è solo il primo paragrafo
            para += 1
            continue
        f, col, wt = font, lay.color, lay.weight
        if lay.para_colors:
            col = lay.para_colors[para_seg[min(para, len(para_seg) - 1)]][0]
        if in_head:
            col, wt = (lay.head_color or col), (lay.head_weight or lay.weight)
            f = _font(wt, size)
        if lay.align == "center":
            x = (W - draw.textlength(ln, font=f)) / 2
        else:
            x = left
        draw.text((x, y), ln, font=f, fill=col)
        if placements is not None:
            placements.append({"testo": ln, "x": float(x), "y": float(y), "size": size,
                               "weight": wt, "color": col})
        y += line_h
    return out


# ---------------------------------------------------------------- slide con grafica: testo scritto sui blocchi

def _hex(c: str) -> tuple:
    c = (c or "#FFFFFF").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def refine_box(original: Image.Image, cleaned: Image.Image, box: tuple[int, int, int, int]):
    """Stringe il riquadro indicato da Claude attorno ai pixel che Gemini ha davvero cancellato
    (dove originale e immagine pulita differiscono), e ricava il colore reale del testo."""
    W, H = original.size
    x0, y0, x1, y1 = box
    pad_x, pad_y = int(W * 0.04), int(H * 0.03)
    X0, Y0, X1, Y1 = max(0, x0 - pad_x), max(0, y0 - pad_y), min(W, x1 + pad_x), min(H, y1 + pad_y)
    a = np.asarray(original.crop((X0, Y0, X1, Y1)).convert("RGB")).astype(np.int16)
    b = np.asarray(cleaned.crop((X0, Y0, X1, Y1)).convert("RGB")).astype(np.int16)
    diff = np.abs(a - b).max(axis=2) > 90
    if diff.sum() < 30:
        return box, None
    # solo le righe con vere lettere: il "codino" della vignetta (una linea sottile) non fa parte della casella
    rows = diff.sum(axis=1)
    dense = rows >= max(4, int(diff.shape[1] * 0.025))
    core = diff & dense[:, None]
    if core.sum() >= 30:
        diff_box = core
    else:
        diff_box = diff
    ys, xs = np.where(diff_box)
    ry0, ry1 = np.percentile(ys, [1, 99]).astype(int)
    rx0, rx1 = np.percentile(xs, [1, 99]).astype(int)
    color = tuple(int(c) for c in np.median(a[diff], axis=0))
    new = (X0 + rx0, Y0 + ry0, X0 + rx1, Y0 + ry1)
    # accetta la correzione solo se è coerente con il riquadro di Claude (stessa zona, dimensioni simili):
    # se Gemini ha spostato o cambiato altri elementi, la differenza non è solo il testo e si tiene il riquadro di Claude
    area_new = (new[2] - new[0]) * (new[3] - new[1])
    area_old = max(1, (x1 - x0) * (y1 - y0))
    cx_shift = abs((new[0] + new[2]) - (x0 + x1)) / 2
    cy_shift = abs((new[1] + new[3]) - (y0 + y1)) / 2
    if (new[2] - new[0] < (x1 - x0) * 0.35 or new[3] - new[1] < (y1 - y0) * 0.3
            or area_new > area_old * 2.2 or cx_shift > W * 0.12 or cy_shift > H * 0.08):
        return box, (color if diff.mean() < 0.5 else None)
    return new, color


def _tokens(text: str) -> list[list[list[tuple[str, int]]]]:
    """Testo -> paragrafi -> righe -> parole (parola, stile). Stile: 1 = colore evidenziato ([[ ]]),
    2 = grassetto ({{ }}), 3 = entrambi."""
    paras = []
    hl, bd = False, False                  # un'evidenziazione può continuare sulla riga dopo
    for para in text.split("\n\n"):
        rows = []
        for row in para.split("\n"):
            words = []
            for w in row.split():
                if w.startswith("[["):
                    hl = True
                if w.startswith("{{") or w.startswith("[[{{") or w.startswith("{{[["):
                    bd = True
                end_hl, end_bd = "]]" in w, "}}" in w
                clean = w.replace("[[", "").replace("]]", "").replace("{{", "").replace("}}", "")
                if clean:
                    words.append((clean, int(hl) | (2 if bd else 0)))
                if end_hl:
                    hl = False
                if end_bd:
                    bd = False
            if words:
                rows.append(words)
        if rows:
            paras.append(rows)
    return paras


def _bold_weight(weight: str) -> str:
    base, _, fam = weight.partition("_")
    if base in ("regular", "semibold", "bold"):
        return "bold" + ("_" + fam if fam else "")
    return weight


def _wfont(font, bfont, flag):
    return bfont if (flag & 2 and bfont is not None) else font


def _line_w(draw, words, font, bfont=None) -> float:
    space = draw.textlength(" ", font=font)
    return sum(draw.textlength(w, font=_wfont(font, bfont, f)) for w, f in words) + space * max(0, len(words) - 1)


def _layout(draw, paras, font, max_w, bfont=None):
    """Come _wrap ma con le parole evidenziate. Restituisce righe di parole; None = riga vuota tra paragrafi."""
    out = []
    for k, rows in enumerate(paras):
        for r in rows:                       # gli a capo voluti restano; si spezza solo la riga troppo lunga
            if _line_w(draw, r, font, bfont) <= max_w:
                out.append(r)
                continue
            out += _balanced(r, lambda ws: _line_w(draw, ws, font, bfont), max_w)
        if k < len(paras) - 1:
            out.append(None)
    return out


def local_background(img: Image.Image, box: tuple[int, int, int, int], exclude: list | None = None):
    """Se attorno al riquadro del testo lo sfondo è di un colore uniforme (es. fascia nera sotto una foto)
    restituisce quel colore: il testo si può cancellare con precisione senza Gemini.
    `exclude` = altri riquadri di testo vicini, da non considerare sfondo."""
    W, H = img.size
    x0, y0, x1, y1 = box
    p = max(6, int(min(W, H) * 0.015))
    X0, Y0, X1, Y1 = max(0, x0 - p), max(0, y0 - p), min(W, x1 + p), min(H, y1 + p)
    if X1 <= X0 or Y1 <= Y0:
        return None
    # si lavora solo sul ritaglio attorno al riquadro (poca memoria anche con tante slide in parallelo)
    a = np.asarray(img.convert("RGB").crop((X0, Y0, X1, Y1))).astype(np.int16)
    keep = np.ones(a.shape[:2], dtype=bool)
    keep[max(0, y0 - Y0):max(0, y1 - Y0), max(0, x0 - X0):max(0, x1 - X0)] = False   # solo la cornice
    for ex in exclude or []:
        ex0, ey0, ex1, ey1 = ex
        keep[max(0, ey0 - p - Y0):max(0, ey1 + p - Y0), max(0, ex0 - p - X0):max(0, ex1 + p - X0)] = False
    ring = a[keep]
    if len(ring) < 50:
        return None
    # i quattro lati della cornice devono avere lo stesso colore: una sfumatura (foto che sfuma nel nero)
    # non è uno sfondo uniforme, riempirla con un colore solo lascerebbe un rettangolo visibile
    # i lati della cornice devono esserci (almeno 3) e avere lo stesso colore: una sfumatura (foto che sfuma
    # nel nero) non è uno sfondo uniforme, riempirla con un colore solo lascerebbe un rettangolo visibile
    ty, by_ = max(0, y0 - Y0), max(0, y1 - Y0)
    lx, rx = max(0, x0 - X0), max(0, x1 - X0)
    strips = [keep[:ty], keep[by_:], keep[:, :lx], keep[:, rx:]]
    datas = [a[:ty], a[by_:], a[:, :lx], a[:, rx:]]
    sides = [np.median(d[k_], axis=0) for k_, d in zip(strips, datas) if k_.sum() >= 20]
    if len(sides) < 3:
        return None
    sides_a = np.array(sides)
    if (sides_a.max(axis=0) - sides_a.min(axis=0)).max() > 22:
        return None
    bg = np.median(ring, axis=0)
    if (np.abs(ring - bg).max(axis=1) < 20).mean() < 0.93:     # davvero uniforme (non un disegno tratteggiato)
        return None
    return tuple(int(c) for c in bg)


def erase_local(img: Image.Image, box: tuple[int, int, int, int], bg: tuple,
                exclude: list | None = None) -> tuple[Image.Image, tuple, float | None]:
    """Cancella il testo dentro il riquadro (su sfondo uniforme). Restituisce l'immagine, il riquadro preciso
    del testo e la distanza tra le righe dell'originale (per non scrivere più grande dell'originale)."""
    a = np.asarray(img.convert("RGB")).copy()
    H, W, _ = a.shape
    x0, y0, x1, y1 = box
    p = max(6, int(min(W, H) * 0.015))
    X0, Y0, X1, Y1 = max(0, x0 - p), max(0, y0 - p), min(W, x1 + p), min(H, y1 + p)
    sub = a[Y0:Y1, X0:X1].astype(np.int16)
    ink = np.abs(sub - np.array(bg)).max(axis=2) > 22     # anche scritte grigie o sbiadite
    own = ink.copy()
    for ex in exclude or []:                       # i pixel degli altri blocchi non contano per questo riquadro
        ex0, ey0, ex1, ey1 = ex
        own[max(0, ey0 - Y0):max(0, ey1 - Y0), max(0, ex0 - X0):max(0, ex1 - X0)] = False
    # righe quasi tutte "non sfondo" sono una foto, non testo (il testo lascia sempre spazi tra le lettere)
    photo_rows = own.mean(axis=1) > 0.75
    if photo_rows.any():
        own[photo_rows] = False
        # e anche le righe di confine subito sotto/sopra (sfumatura della foto)
        idx = np.where(photo_rows)[0]
        for r_ in idx:
            for d_ in (-2, -1, 1, 2):
                if 0 <= r_ + d_ < own.shape[0] and own[r_ + d_].mean() > 0.4:
                    own[r_ + d_] = False
    # ciò che tocca il bordo esterno della zona (es. il fondo di una foto appena sopra la fascia nera)
    # non è testo: non si cancella, altrimenti resterebbe un rettangolo nero sulla foto
    try:
        import cv2
        n, lab = cv2.connectedComponents(own.astype(np.uint8), connectivity=8)
        if n > 1:
            border = np.zeros_like(own)
            border[0, :] = border[-1, :] = True
            border[:, 0] = border[:, -1] = True
            inside = np.zeros_like(own)
            inside[max(0, y0 - Y0):max(0, y1 - Y0), max(0, x0 - X0):max(0, x1 - X0)] = True
            touch = set(np.unique(lab[border & own]).tolist()) - {0}
            keep_in = set(np.unique(lab[inside & own]).tolist()) - {0}
            drop = [c for c in touch if c not in keep_in or (lab == c)[~inside].sum() > (lab == c)[inside].sum()]
            if drop:
                own[np.isin(lab, drop)] = False
    except ImportError:
        pass
    pitch = None
    if own.any():
        ys, xs = np.where(own)
        tight = (X0 + int(xs.min()), Y0 + int(ys.min()), X0 + int(xs.max()), Y0 + int(ys.max()))
        rows = _runs(own.sum(axis=1) >= 2, min_gap=2)
        if len(rows) >= 2:
            pitch = float(np.median(np.diff([r[0] for r in rows])))
        if rows:
            erase_local.last_line_h = float(np.median([r[1] - r[0] + 1 for r in rows]))
    else:
        tight = box
    m = _dilate(own, 3)
    _fill_like_background(sub, m, ink, bg)
    a[Y0:Y1, X0:X1] = sub.astype(np.uint8)
    return Image.fromarray(a), tight, pitch


def _fill_like_background(sub: np.ndarray, m: np.ndarray, ink: np.ndarray, bg: tuple) -> None:
    """Riempie la zona delle lettere col colore di sfondo. Attorno alle lettere di una foto JPEG resta un alone
    di pixel quasi-sfondo (sotto la soglia dell'inchiostro): riempiendo solo le lettere si vedeva la loro sagoma
    più scura. Si copre anche l'alone (vicino alle lettere, solo pixel già molto simili allo sfondo) e il bordo
    sfuma nello sfondo vero, così non resta nessuna sagoma."""
    import cv2
    flat = np.array(bg, dtype=np.float32)
    near = np.abs(sub - np.array(bg)).max(axis=2) <= 40
    halo = _dilate(m, 6) & near
    alpha = cv2.GaussianBlur((m | halo).astype(np.float32), (0, 0), 2.0)
    alpha[m] = 1.0
    out = sub.astype(np.float32) * (1 - alpha[..., None]) + flat * alpha[..., None]
    sub[:] = np.round(out).astype(sub.dtype)


def expand_to_ink(img: Image.Image, box: tuple[int, int, int, int], bg: tuple,
                  exclude: list | None = None) -> tuple[int, int, int, int]:
    """Le misure di Claude a volte tagliano una riga o la fine di una parola (es. "about." sotto il blocco):
    si allarga il riquadro finché appena fuori dai bordi non resta più nessuna lettera dello stesso testo.
    Si allarga solo verso zone di sfondo uniforme (mai dentro una foto)."""
    W, H = img.size
    bx0, by0, bx1, by1 = box
    # ritaglio della zona in cui il riquadro può allargarsi (coordinate locali: meno memoria)
    cx0, cy0 = max(0, bx0 - int(W * 0.55)), max(0, by0 - int(H * 0.24))
    cx1, cy1 = min(W, bx1 + int(W * 0.55)), min(H, by1 + int(H * 0.24))
    a = np.asarray(img.convert("RGB").crop((cx0, cy0, cx1, cy1))).astype(np.int16)
    diff = np.abs(a - np.array(bg)).max(axis=2)
    del a
    ink = diff > 40
    flat = diff < 20
    for ex in exclude or []:
        ex0, ey0, ex1, ey1 = ex[0] - cx0, ex[1] - cy0, ex[2] - cx0, ex[3] - cy0
        ink[max(0, ey0):max(0, ey1), max(0, ex0):max(0, ex1)] = False
        flat[max(0, ey0):max(0, ey1), max(0, ex0):max(0, ex1)] = True
    H, W = ink.shape
    x0, y0, x1, y1 = bx0 - cx0, by0 - cy0, bx1 - cx0, by1 - cy0
    W_full = img.size[0]
    sx, sy = max(8, int(W_full * 0.03)), max(8, int(img.size[1] * 0.03))
    lim_x, lim_y = int(W_full * 0.5), int(img.size[1] * 0.2)
    ox0, oy0, ox1, oy1 = x0, y0, x1, y1
    for _ in range(30):
        changed = False
        strips = {
            "top": (max(0, y0 - sy), y0, x0, x1), "bottom": (y1, min(H, y1 + sy), x0, x1),
            "left": (y0, y1, max(0, x0 - sx), x0), "right": (y0, y1, x1, min(W, x1 + sx)),
        }
        for side, (ya, yb, xa, xb) in strips.items():
            if yb <= ya or xb <= xa:
                continue
            m = ink[ya:yb, xa:xb]
            if m.sum() < 6 or m.mean() > 0.45 or (m | flat[ya:yb, xa:xb]).mean() < 0.9:
                # niente lettere, oppure è una foto/grafica (non sfondo uniforme): non si allarga
                continue
            ys, xs = np.where(m)
            if side == "top" and oy0 - y0 < lim_y:
                y0, changed = ya + int(ys.min()), True
            elif side == "bottom" and y1 - oy1 < lim_y:
                y1, changed = ya + int(ys.max()) + 1, True
            elif side == "left" and ox0 - x0 < lim_x:
                x0, changed = xa + int(xs.min()), True
            elif side == "right" and x1 - ox1 < lim_x:
                x1, changed = xa + int(xs.max()) + 1, True
        if not changed:
            break
    return (x0 + cx0, y0 + cy0, x1 + cx0, y1 + cy0)


def flatten_ghosts(img: Image.Image, blocks: list[dict]) -> Image.Image:
    """Dopo la pulizia di Gemini a volte restano ombre grigie del testo vecchio. Dove il testo sta su
    sfondo uniforme, la zona del blocco si ridipinge col colore pieno dello sfondo."""
    W, H = img.size
    a = np.asarray(img.convert("RGB")).copy()
    boxes = [block_box(bl, W, H) for bl in blocks]
    for box in boxes:
        x0, y0, x1, y1 = box
        gx, gy = int(W * 0.03), int(H * 0.02)
        exp = (max(0, x0 - gx), max(0, y0 - gy), min(W, x1 + gx), min(H, y1 + gy))
        bg = local_background(Image.fromarray(a), exp)
        if bg is None:
            continue
        sub = a[exp[1]:exp[3], exp[0]:exp[2]].astype(np.int16)
        near = np.abs(sub - np.array(bg)).max(axis=2) < 110      # ombre e residui, non elementi grafici forti
        sub[near] = np.array(bg)
        a[exp[1]:exp[3], exp[0]:exp[2]] = sub.astype(np.uint8)
    return Image.fromarray(a)


def _color_mask(a, box, colors, W, H):
    """Lettere di un colore noto (es. nero del fumetto): pixel di quel colore attorno al riquadro, tenendo
    solo i pezzi che toccano il riquadro (così si prendono anche le lettere che escono un po' dal riquadro
    di Claude, ma non i tratti del disegno lontani)."""
    import cv2
    x0, y0, x1, y1 = box
    ex, ey = int(W * 0.06), int(H * 0.015)      # in verticale poco margine: sotto le battute ci sono le teste
    X0, Y0, X1, Y1 = max(0, x0 - ex), max(0, y0 - ey), min(W, x1 + ex), min(H, y1 + ey)
    sub = cv2.cvtColor(a[Y0:Y1, X0:X1], cv2.COLOR_BGR2RGB).astype(np.int16)
    m = np.zeros(sub.shape[:2], bool)
    for c in colors:
        m |= np.abs(sub - np.array(c, np.int16)).max(axis=2) < 75
    m8 = m.astype(np.uint8) * 255
    # il contorno del fumetto ha lo stesso colore delle lettere: i pezzi lunghi e sottili (contorni, codine)
    # o che toccano il bordo della zona di ricerca non sono lettere e restano dove sono
    n0, lab0, st0, _c0 = cv2.connectedComponentsWithStats(m8, connectivity=8)
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    sh, sw = m8.shape
    for i in range(1, n0):
        cx_, cy_, cw_, ch_, area_ = st0[i]
        tocca = cx_ <= 0 or cy_ <= 0 or cx_ + cw_ >= sw or cy_ + ch_ >= sh
        grande = cw_ > 0.55 * bw or ch_ > 0.9 * bh or cw_ > 0.3 * W or ch_ > 0.2 * H
        sottile = area_ < 0.22 * cw_ * ch_
        if (tocca and (cw_ > 0.15 * bw or ch_ > 0.5 * bh)) or (grande and sottile):
            m8[lab0 == i] = 0
    m = m8 > 0
    # le lettere vicine si uniscono in parole, così una lettera che esce dal riquadro viene presa con la sua parola
    # righe e parole della stessa battuta si uniscono (anche in verticale), così si cancella tutta la battuta
    joined = cv2.dilate(m8, np.ones((7, 11), np.uint8), iterations=2)
    n, lab = cv2.connectedComponents(joined, connectivity=8)
    core = np.zeros_like(m)
    cx0, cy0 = max(0, x0 - X0), max(0, y0 - Y0)
    core[cy0:max(cy0, y1 - Y0), cx0:max(cx0, x1 - X0)] = True
    lab_keep = None
    keep = set(np.unique(lab[core & (joined > 0)]).tolist()) - {0}
    out = np.zeros((H, W), np.uint8)
    if keep:
        sel = np.isin(lab, list(keep)) & m
        sel = cv2.dilate(sel.astype(np.uint8) * 255, np.ones((5, 5), np.uint8), iterations=2)
        out[Y0:Y1, X0:X1] = sel
    return out


def inpaint_boxes(img: Image.Image, boxes: list[tuple[int, int, int, int]], pad: float = 0.025,
                  colors: list | None = None) -> Image.Image:
    """Cancella col codice le scritte dentro i riquadri anche su foto o sfumature (senza Gemini):
    trova i pixel delle lettere confrontando con lo sfondo 'sfocato' e ricostruisce lo sfondo attorno.
    `colors` (per riquadro, facoltativo): colori del testo; se ci sono si cercano le lettere per colore."""
    import cv2
    a = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    H, W = a.shape[:2]
    mask = np.zeros((H, W), np.uint8)
    for bi, (x0, y0, x1, y1) in enumerate(boxes):
        cols = (colors or [None] * len(boxes))[bi] if colors and bi < len(colors) else None
        if cols:
            cm = _color_mask(a, (x0, y0, x1, y1), cols, W, H)
            if cm.any():
                mask = np.maximum(mask, cm)
                continue
        px, py = int(W * pad), int(H * pad)
        X0, Y0, X1, Y1 = max(0, x0 - px), max(0, y0 - py), min(W, x1 + px), min(H, y1 + py)
        if X1 - X0 < 4 or Y1 - Y0 < 4:
            continue
        sub = a[Y0:Y1, X0:X1]
        k = int(min(Y1 - Y0, X1 - X0, 151) // 2 * 2 + 1)
        k = max(15, min(k, 101))
        bg = cv2.medianBlur(sub, k)
        diff = np.abs(sub.astype(np.int16) - bg.astype(np.int16)).max(axis=2)
        # righe su fondo quasi uniforme (fasce scure, sfumature): lo sfondo è la mediana della riga,
        # così si prendono per intero anche le lettere grandi dei titoli
        s16 = sub.astype(np.int16)
        q = (sub // 32).astype(np.int32)
        codes = q[..., 0] * 64 + q[..., 1] * 8 + q[..., 2]
        # colore dominante del bordo del riquadro: di solito è lo sfondo dietro le lettere
        border = np.concatenate([codes[0], codes[-1], codes[:, 0], codes[:, -1]])
        bcode = int(np.bincount(border, minlength=512).argmax())
        for yy in range(sub.shape[0]):
            cnt = np.bincount(codes[yy], minlength=512)
            top = int(cnt.argmax())
            if cnt[bcode] >= 0.12 * sub.shape[1]:
                top = bcode                    # le lettere possono occupare quasi tutta la riga: vale lo sfondo del bordo
            elif cnt[top] < 0.3 * sub.shape[1]:
                continue                       # riga di foto/dettagli: resta il metodo sfocato
            sel = codes[yy] == top
            ref = np.median(s16[yy][sel], axis=0)  # colore dominante della riga (lo sfondo dietro le lettere)
            rd = np.abs(s16[yy] - ref).max(axis=1)
            diff[yy] = np.maximum(diff[yy], rd)
        # soglia bassa e allargamento generoso: si prendono anche ombre e aloni attorno alle lettere
        m = (diff > 26).astype(np.uint8) * 255
        m = cv2.dilate(m, np.ones((5, 5), np.uint8), iterations=3)
        mask[Y0:Y1, X0:X1] = np.maximum(mask[Y0:Y1, X0:X1], m)
    if not mask.any():
        return img.convert("RGB")
    out = cv2.inpaint(a, mask, 6, cv2.INPAINT_TELEA)
    out = _retexture(a, out, mask)
    return Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))


def _retexture(orig: np.ndarray, filled: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """La ricostruzione 'liscia' lascia una macchia sfocata su sfondi con texture (tratteggio, grana).
    Si rimette la texture: alla zona riempita si aggiunge il dettaglio fine (alte frequenze) di una zona vicina
    senza testo (sopra o sotto), così il muro tratteggiato resta tratteggiato."""
    import cv2
    H, W = mask.shape
    n, lab, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    out = filled.astype(np.float32)
    detail = orig.astype(np.float32) - cv2.GaussianBlur(orig, (0, 0), 3).astype(np.float32)
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if area < 200:
            continue
        best, best_e = None, None
        for dy in (-h - 4, h + 4, -2 * h - 8, 2 * h + 8, -h // 2 - 4, h // 2 + 4):
            sy = y + dy
            if sy < 0 or sy + h > H:
                continue
            if (mask[sy:sy + h, x:x + w] > 0).mean() > 0.05:
                continue
            d = detail[sy:sy + h, x:x + w]
            strong = (np.abs(d).max(axis=2) > 45).mean()        # contorni netti (linee, capelli): da evitare
            if strong > 0.04:
                continue
            e = float(np.abs(d).mean())
            if best_e is None or e < best_e:
                best, best_e = sy, e
        if best is None:
            continue
        reg = lab[y:y + h, x:x + w] == k
        soft = cv2.GaussianBlur(reg.astype(np.float32), (0, 0), 2)
        tex = np.clip(detail[best:best + h, x:x + w], -30, 30)
        out[y:y + h, x:x + w] += tex * soft[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def boxes_on_plain(img: Image.Image, lay, blocks: list[dict]) -> list:
    """Slide su colore pieno: per ogni blocco la casella vera del testo originale (righe misurate e assegnate
    al blocco più vicino), con interlinea e altezza delle lettere. Formato come try_local_clean."""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    H, W, _ = a.shape
    ink = np.abs(a - np.array(lay.bg)).max(axis=2) > 70
    ink[int(H * 0.9):] = False                       # fascia bassa: marchi, non testo
    rows = [r for r in _runs(ink.sum(axis=1) >= 2, min_gap=1) if r[1] - r[0] >= 5]
    bbs = [block_box(b, W, H) for b in blocks]
    groups = [[] for _ in blocks]
    for r in rows:
        cy = (r[0] + r[1]) / 2
        d = [0 if b[1] <= cy <= b[3] else min(abs(cy - b[1]), abs(cy - b[3])) for b in bbs]
        k = int(np.argmin(d))
        if d[k] < H * 0.12:
            groups[k].append(r)
    out = []
    for b, g in zip(bbs, groups):
        if not g:
            out.append((b, None, None))
            continue
        y0, y1 = g[0][0], g[-1][1]
        cols = np.where(ink[y0:y1 + 1].any(axis=0))[0]
        box = (int(cols.min()), int(y0), int(cols.max()), int(y1))
        pitch = float(np.median(np.diff([r[0] for r in g]))) if len(g) >= 2 else None
        line_h = float(np.median([r[1] - r[0] + 1 for r in g]))
        out.append((box, pitch, line_h))
    # blocchi allineati a sinistra quasi sullo stesso margine: stesso margine esatto
    lefts = [b[0][0] for b, bl in zip(out, blocks) if (bl.get("allineamento") or "left") == "left"]
    if len(lefts) >= 2 and max(lefts) - min(lefts) < W * 0.04:
        m = min(lefts)
        out = [((m, b[0][1], b[0][2], b[0][3]), b[1], b[2]) if (bl.get("allineamento") or "left") == "left" else b
               for b, bl in zip(out, blocks)]
    return out


def dark_fade(img: Image.Image, original: Image.Image, boxes: list) -> Image.Image:
    """Testo su una fascia scura che sfuma nella foto (copertine): invece di ricostruire la foto dietro le lettere,
    si ridisegna la fascia scura con una sfumatura morbida verso l'alto, un po' più alta del testo nuovo.
    Si applica solo se dietro il testo originale lo sfondo era già scuro."""
    W, H = img.size
    if not boxes:
        return img
    top = min(b[1] for b in boxes)
    bot = max(b[3] for b in boxes)
    g = np.asarray(original.convert("L")).astype(np.int16)
    band = g[max(0, top):min(H, bot)]
    if band.size == 0:
        return img
    dark = band[band < 90]
    if dark.size < band.size * 0.5:
        return img                                   # dietro il testo non c'è una fascia scura
    rgb = np.asarray(original.convert("RGB")).astype(np.int16)[max(0, top):min(H, bot)]
    col = np.median(rgb[band < 90], axis=0)
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    y = np.arange(H, dtype=np.float32)[:, None]
    start, full = top - H * 0.26, top - H * 0.02      # sfumatura lunga: nessun bordo netto sulla foto
    alpha = np.clip((y - start) / max(1.0, full - start), 0, 1)
    if bot < H * 0.8:                                # fascia in mezzo alla slide: sfuma anche sotto
        end0, end1 = bot + H * 0.02, bot + H * 0.14
        alpha = np.minimum(alpha, np.clip((end1 - y) / max(1.0, end1 - end0), 0, 1))
    alpha = alpha ** 1.3
    alpha = np.broadcast_to(alpha, (H, W)).copy()
    # sopra il testo si scurisce SOLO ciò che nell'originale era già scuro: elementi chiari o colorati
    # (badge col numero, foto) restano intatti; dentro la fascia del testo invece tutto diventa fascia piena
    lum = np.asarray(original.convert("L")).astype(np.float32)
    dark_w = np.clip((140 - lum) / 90, 0, 1)
    above = np.arange(H)[:, None] < top - H * 0.01
    alpha = np.where(above, np.minimum(alpha, dark_w), alpha)
    a = a * (1 - alpha[..., None]) + col[None, None, :] * alpha[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def ink_boxes(img: Image.Image, blocks: list[dict]) -> list:
    """Caselle precise del testo originale di ogni blocco, cercando le lettere per colore attorno al riquadro di
    Claude (per quando lo sfondo viene ricostruito da zero e non si può confrontare 'prima e dopo')."""
    import cv2
    a = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    rgb = np.asarray(img.convert("RGB")).astype(np.int16)
    H, W = a.shape[:2]
    claude = [block_box(bl, W, H) for bl in blocks]
    out = []
    for i, bl in enumerate(blocks):
        box = claude[i]
        cols = [c for c in (_hex(bl.get("colore")), _hex(bl.get("colore_evidenziato")) if bl.get("colore_evidenziato") else None) if c]
        m = _color_mask(a, box, cols, W, H) > 0
        # i pixel più vicini al riquadro di un altro blocco appartengono a quello
        for j, ob in enumerate(claude):
            if j == i:
                continue
            ys_, xs_ = np.nonzero(m)
            if not len(xs_):
                break
            d_me = np.maximum(np.maximum(box[0] - xs_, xs_ - box[2]), 0) + np.maximum(np.maximum(box[1] - ys_, ys_ - box[3]), 0)
            d_ot = np.maximum(np.maximum(ob[0] - xs_, xs_ - ob[2]), 0) + np.maximum(np.maximum(ob[1] - ys_, ys_ - ob[3]), 0)
            far = d_ot < d_me
            m[ys_[far], xs_[far]] = False
        ink = np.zeros((H, W), bool)
        for c in cols:
            ink |= np.abs(rgb - np.array(c)).max(axis=2) < 75
        ink &= m
        near = np.zeros((H, W), bool)                 # solo attorno al riquadro di Claude (non i tratti del disegno)
        ex, ey = int(W * 0.03), int(H * 0.012)
        near[max(0, box[1] - ey):min(H, box[3] + ey), max(0, box[0] - ex):min(W, box[2] + ex)] = True
        ink &= near
        # righe di testo vere: abbastanza inchiostro in orizzontale
        rowsum = ink.sum(axis=1)
        ink[rowsum < max(4, int((box[2] - box[0]) * 0.03))] = False
        ys, xs = np.nonzero(ink)
        if len(xs) < 30:
            out.append((box, None, None))
            continue
        tight = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        rows = [r for r in _runs(ink.sum(axis=1)[tight[1]:tight[3] + 1] >= 3, min_gap=2) if r[1] - r[0] >= 6]
        pitch = float(np.median(np.diff([r[0] for r in rows]))) if len(rows) >= 2 else None
        line_h = float(np.median([r[1] - r[0] + 1 for r in rows])) if rows else None
        n_orig = max(1, len([x for x in (bl.get("testo") or "").split("\n") if x.strip()]))
        if len(rows) < min(n_orig, 2):            # righe attaccate: misure non affidabili, conta solo la casella
            pitch = line_h = None
        out.append((tight, pitch, line_h))
    return out


def other_marks(slide: dict, W: int, H: int) -> list:
    return [block_box(r, W, H) for r in (slide.get("altri_marchi") or []) if r.get("w") and r.get("h")]


def wm_box(slide: dict, W: int, H: int):
    r = slide.get("watermark_riquadro") or {}
    if not r or not r.get("w") or not r.get("h"):
        return None
    return block_box(r, W, H)


def block_box(bl: dict, W: int, H: int) -> tuple[int, int, int, int]:
    return (int(bl["x"] / 1000 * W), int(bl["y"] / 1000 * H),
            int((bl["x"] + bl["w"]) / 1000 * W), int((bl["y"] + bl["h"]) / 1000 * H))


def try_local_clean(original: Image.Image, blocks: list[dict]):
    """Pulisce col codice tutti i blocchi se ognuno sta su uno sfondo uniforme. Altrimenti None (serve Gemini)."""
    img, W, H = original, *original.size
    all_boxes = [block_box(bl, W, H) for bl in blocks]
    bgs = []
    for i in range(len(all_boxes)):
        others = [b for j, b in enumerate(all_boxes) if j != i]
        found = None
        # le misure di Claude sono approssimative: si allarga il riquadro a piccoli passi finché attorno c'è solo sfondo
        for grow in (0, 0.01, 0.02, 0.035, 0.05):
            x0, y0, x1, y1 = all_boxes[i]
            gx, gy = int(W * grow), int(H * grow)
            cand = (max(0, x0 - gx), max(0, y0 - gy), min(W, x1 + gx), min(H, y1 + gy))
            bg = local_background(img, cand, others)
            if bg is not None:
                found = (cand, bg)
                break
        if not found:
            return None
        others_now = [b for j, b in enumerate(all_boxes) if j != i]
        all_boxes[i] = expand_to_ink(img, found[0], found[1], others_now)
        bgs.append(found[1])
    boxes = []
    for i, (box, bg) in enumerate(zip(all_boxes, bgs)):
        erase_local.last_line_h = None
        img, tight, pitch = erase_local(img, box, bg, [b for j, b in enumerate(all_boxes) if j != i])
        boxes.append((tight, pitch, erase_local.last_line_h))      # altezza delle lettere dell'originale
    return img, boxes


def _metrics_from_diff(original: Image.Image, cleaned: Image.Image, box) -> tuple[float | None, float | None]:
    """Interlinea e altezza delle lettere del testo originale, dove l'immagine pulita differisce dall'originale."""
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 - x0 < 10 or y1 - y0 < 10:
        return None, None
    a = np.asarray(original.crop((x0, y0, x1, y1)).convert("RGB"), np.int16)
    b = np.asarray(cleaned.crop((x0, y0, x1, y1)).convert("RGB"), np.int16)
    m = np.abs(a - b).max(axis=2) > 60
    rows = [r for r in _runs(m.sum(axis=1) >= max(4, int(m.shape[1] * 0.025)), min_gap=2) if r[1] - r[0] >= 6]
    if not rows:
        return None, None
    line_h = float(np.median([r[1] - r[0] + 1 for r in rows]))
    pitch = float(np.median(np.diff([r[0] for r in rows]))) if len(rows) >= 2 else None
    return pitch, line_h


def render_blocks(cleaned: Image.Image, original: Image.Image, blocks: list[dict],
                  placements: list | None = None, boxes: list | None = None) -> Image.Image:
    """Scrive i blocchi di testo italiano sull'immagine ripulita, nei riquadri del testo originale.
    `boxes` = riquadri già precisi (pulizia col codice); altrimenti si affinano confrontando con l'originale."""
    out = cleaned.copy()
    W, H = out.size
    draw = ImageDraw.Draw(out)
    all_bb = [block_box(b, W, H) for b in blocks]
    for idx, bl in enumerate(blocks):
        text = no_emoji(bl.get("testo") or "")
        if not text:
            continue
        if bl.get("maiuscolo"):
            text = text.upper()
        pitch, line_h = None, None
        if boxes:
            entry, color = boxes[idx], None
            box, pitch = entry[0], entry[1]
            line_h = entry[2] if len(entry) > 2 else None
        else:
            box, color = refine_box(original, cleaned, block_box(bl, W, H))
            pitch, line_h = _metrics_from_diff(original, cleaned, box)
        # con parole di due colori (es. nere e rosse) il colore misurato sarebbe una via di mezzo: vale quello di Claude
        color = _hex(bl.get("colore")) if (boxes or not color or bl.get("colore_evidenziato")) else color
        hl_color = _hex(bl.get("colore_evidenziato")) if bl.get("colore_evidenziato") else color
        bw, bh = max(40, box[2] - box[0]), max(20, box[3] - box[1])
        # spazio in larghezza: l'italiano è più lungo dell'inglese. Invece di rimpicciolire o andare a capo
        # più volte, le righe possono allungarsi fino al margine simmetrico a quello di partenza
        al0 = bl.get("allineamento", "left")
        sx = int(W * SAFE_X)
        # il testo resta sempre dentro la zona sicura di Instagram (margini laterali minimi)
        box = (max(box[0], sx), box[1], min(box[2], W - sx), box[3])
        bw = max(40, box[2] - box[0])
        # limiti dati dagli ALTRI blocchi sulla stessa altezza (es. due battute di fumetto affiancate):
        # le righe possono allungarsi solo fino a poco prima del blocco vicino
        gap = int(W * 0.03)
        lim_l, lim_r = sx, W - sx
        me = all_bb[idx]
        mcx = (me[0] + me[2]) / 2
        for j, ob in enumerate(all_bb):
            ov = min(ob[3], me[3]) - max(ob[1], me[1])
            hov = min(ob[2], me[2]) - max(ob[0], me[0])
            if j == idx or ov < 0.3 * min(ob[3] - ob[1], me[3] - me[1]) or hov > 0.2 * min(ob[2] - ob[0], me[2] - me[0]):
                continue                      # affiancati solo se stessa altezza e uno a fianco dell'altro
            ocx = (ob[0] + ob[2]) / 2
            if ocx > mcx:                     # blocco alla mia destra: non lo supero
                lim_r = min(lim_r, max(ob[0] - gap, me[0] + int(W * 0.15)))
            else:                             # blocco alla mia sinistra
                lim_l = max(lim_l, min(ob[2] + gap, me[2] - int(W * 0.15)))
        # il riquadro misurato non può invadere il blocco vicino (le lettere dei due blocchi a volte si toccano)
        box = (max(box[0], lim_l if lim_l < box[2] - 40 else box[0]), box[1],
               min(box[2], lim_r if lim_r > box[0] + 40 else box[2]), box[3])
        bw = max(40, box[2] - box[0])
        # la casella non può scendere sul blocco successivo (es. titolo grande con sottotitolo piccolo sotto)
        for j, ob in enumerate(all_bb):
            if j == idx or ob[1] <= all_bb[idx][1] or ob[2] < box[0] or ob[0] > box[2]:
                continue
            if ob[1] < box[3] + int(H * 0.005):
                box = (box[0], box[1], box[2], max(box[1] + 20, ob[1] - int(H * 0.008)))
                bh = box[3] - box[1]
        # CASELLA DI TESTO: il testo italiano sta nello stesso riquadro occupato dall'originale.
        # Le righe si ridistribuiscono liberamente, ma non si esce mai dalla casella.
        max_w = int(bw * 1.02)
        car0 = bl.get("carattere") or "normale"
        if car0 != "fumetto":
            # titoli e testi su foto/fasce: le righe possono allungarsi fino al margine simmetrico
            # (l'italiano è più lungo: così resta grande come l'originale), ma mai oltre un blocco vicino
            if al0 == "left":
                wide = min(W - box[0] - max(box[0], sx), lim_r - box[0])
            elif al0 == "right":
                wide = min(box[2] - max(W - box[2], sx), box[2] - lim_l)
            else:
                half = min((box[0] + box[2]) / 2 - lim_l, lim_r - (box[0] + box[2]) / 2)
                wide = min(W - 2 * max(min(box[0], W - box[2]), sx), 2 * half)
            max_w = int(max(max_w, min(wide, W - 2 * sx)))
        # la casella si accorcia se esce dalla zona sicura in alto o in basso (il testo si rimpicciolisce
        # invece di essere spinto giù sopra i personaggi)
        top_eff, bot_eff = max(box[1], int(H * SAFE_Y)), min(box[3], H - int(H * SAFE_Y))
        if bot_eff - top_eff > 30:
            box = (box[0], top_eff, box[2], bot_eff)
            bh = box[3] - box[1]
        car = bl.get("carattere") or "normale"
        weight = car if car in ("titolo", "stretto", "fumetto") else (bl.get("peso") or "regular")
        paras = _tokens(text)
        ratio = 1.15 if car in ("titolo", "stretto", "fumetto") else 1.25   # spazio per gli accenti sulle maiuscole
        size, best = max(14, int(bh)), None
        if pitch:                                   # mai più grande dell'originale
            size = min(size, int(pitch / ratio * 1.02))
        if pitch and line_h:
            # stessa altezza delle lettere e stessa interlinea dell'originale (titoli grandi e serrati)
            sample = "HÀ" if text.replace("[[", "").replace("]]", "").isupper() else "Hg"
            for cand in range(14, 400):
                bb = _font(weight, cand).getbbox(sample.replace("À", ""))
                if bb[3] - bb[1] >= line_h * 0.97:
                    size = cand
                    break
            ratio = float(np.clip(pitch / max(size, 1), 1.02, 1.6))
            size = min(size, int(pitch / 1.02))      # mai più grande di quanto permette l'interlinea originale
        start = size
        flat = [[[w for r in rows for w in r]] for rows in paras]

        def fit(p_):
            sz = start
            while sz >= 14:
                f_, b_ = _font(weight, sz), _font(_bold_weight(weight), sz)
                lh_ = int(sz * ratio)
                cand = _layout(draw, p_, f_, int(max_w), b_)
                tot = sum(int(sz * 0.6) if ln is None else lh_ for ln in cand)
                if tot - lh_ + sz * 0.95 <= bh * 1.04 and \
                        max((_line_w(draw, ln, f_, b_) for ln in cand if ln), default=0) <= max_w:
                    return (f_, b_, cand, lh_, tot)
                sz -= 1 if sz < 40 else 2
            return None
        # prima gli a capo dell'originale (elenchi, frasi brevi una per riga); le parole si ridistribuiscono
        # solo se così il testo verrebbe molto più piccolo
        keep_breaks = fit(paras)
        reflow = fit(flat) if any(len(rows) > 1 for rows in paras) else None
        best = keep_breaks
        if reflow and (not keep_breaks or reflow[0].size > keep_breaks[0].size * 1.2):
            best = reflow
        elif reflow and keep_breaks and pitch and reflow[0].size >= keep_breaks[0].size * 0.95:
            # l'italiano è andato a capo più volte dell'originale (es. 3 righe al posto di 2): se a grandezza
            # uguale ci sta nelle righe dell'originale, si torna a quelle (resta nello spazio, es. sulla fascia scura)
            n_orig = max(1, round((bh + pitch * 0.3) / pitch))
            n_keep = sum(1 for ln in keep_breaks[2] if ln)
            n_flow = sum(1 for ln in reflow[2] if ln)
            if n_keep > n_orig and n_flow <= n_orig:
                best = reflow
        if not best:
            font, bfont = _font(weight, 14), _font(_bold_weight(weight), 14)
            lines = _layout(draw, paras, font, bw, bfont)
            best = (font, bfont, lines, int(14 * ratio), len(lines) * int(14 * ratio))
        font, bfont, lines, lh, total = best
        # dove cadono davvero le lettere rispetto al punto di scrittura (cambia da un font all'altro)
        first = next((ln for ln in lines if ln), None)
        last = next((ln for ln in reversed(lines) if ln), None)
        top_off = font.getbbox(" ".join(w for w, _ in first))[1] if first else 0
        bot_off = font.getbbox(" ".join(w for w, _ in last))[3] if last else font.size
        real_h = (total - lh) + bot_off - top_off
        y = box[1] + (bh - real_h) / 2 - top_off
        margin = int(H * max(0.05, SAFE_Y))
        if y + (total - lh) + bot_off > H - margin:            # mai attaccato al bordo in basso
            y = H - margin - (total - lh) - bot_off
        if y + top_off < margin:
            y = margin - top_off
        space = draw.textlength(" ", font=font)
        al = bl.get("allineamento", "left")
        x_left = box[0]
        shared_left = any(j != idx and (blocks[j].get("allineamento") or "left") == "left"
                          and abs(all_bb[j][0] - all_bb[idx][0]) < W * 0.04 for j in range(len(blocks)))
        if al == "left" and not shared_left and abs((box[0] + box[2]) / 2 - W / 2) < W * 0.045 \
                and (box[2] - box[0]) < W * 0.75:       # un blocco largo quanto la slide è allineato a sinistra, non centrato
            # blocco centrato nella slide: resta centrato anche se le righe italiane hanno lunghezza diversa
            widest = max((_line_w(draw, ln, font, bfont) for ln in lines if ln), default=0)
            x_left = int((W - widest) / 2)
        for ln in lines:
            if ln is None:
                y += int(font.size * 0.6)
                continue
            tw = _line_w(draw, ln, font, bfont)
            cx = (box[0] + box[2]) / 2
            if abs(cx - W / 2) < W * 0.05:
                cx = W / 2                                 # testo centrato nella slide: centro esatto
            x = x_left if al == "left" else (box[2] - tw if al == "right" else cx - tw / 2)
            x = min(max(x, sx), W - sx - tw) if tw <= W - 2 * sx else sx      # mai fuori dalla slide
            runs = []
            for w, fl in ln:
                c = hl_color if fl & 1 else color
                f = _wfont(font, bfont, fl)
                draw.text((x, y), w, font=f, fill=c)
                runs.append((w, c, bool(fl & 2)))
                x += draw.textlength(w, font=f) + space
            if placements is not None:
                x_start = x_left if al == "left" else (box[2] - tw if al == "right" else cx - tw / 2)
                x_start = min(max(x_start, sx), W - sx - tw) if tw <= W - 2 * sx else sx
                placements.append({"testo": " ".join(w for w, _ in ln), "x": float(x_start), "y": float(y),
                                   "size": font.size, "weight": weight, "color": color,
                                   "runs": [[w, list(c), b] for w, c, b in runs]})
            y += lh
    return out
