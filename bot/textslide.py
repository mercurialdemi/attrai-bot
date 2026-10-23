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
    # testo con lettere strette e ravvicinate (stile Helvetica Neue / SF compatto)
    "regular_tight": config.ROOT / "assets" / "stretto_testo_regular.ttf",
    "semibold_tight": config.ROOT / "assets" / "stretto_testo_semibold.ttf",
    "bold_tight": config.ROOT / "assets" / "stretto_testo_bold.ttf",
}


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
    # corpo dall'altezza vera delle lettere: la dimensione in cui "Hg" è alto come le righe misurate
    h75 = float(np.percentile(t_heights, 75))
    size_h = size
    for cand in range(max(12, int(h75 * 0.7)), int(h75 * 1.6) + 1):
        bb = _font(weight, cand).getbbox("Hgjy")
        if bb[3] - bb[1] >= h75:
            size_h = cand
            break
    # corpo dalla larghezza (confronto con il testo letto da Claude): se a parità di altezza l'originale è
    # molto più stretto di Inter, il font è "compatto" (stile Helvetica Neue) e si usa Inter Tight
    tight = False
    by_text = _size_from_text(text_lines, original_text, weight)
    if not by_text:
        flat_text = " ".join((original_text or "").split())
        if len(flat_text) > 12:
            ink_w = sum(l[3] - l[2] + 1 for l in text_lines)
            probe = _font(weight, 100)
            w100 = probe.getlength(flat_text) - probe.getlength(" ") * max(0, len(text_lines) - 1)
            by_text = int(100 * ink_w / w100) if w100 > 0 else None
    if by_text and by_text < 0.95 * size_h:
        tight, size = True, size_h
    else:
        size = by_text or size_h
    stroke = (np.median(runs) / size) if runs else 0.1
    weight = "bold" if stroke > 0.125 else ("semibold" if stroke > 0.105 else "regular")

    # interlinea misurata: distanza tipica tra l'inizio di due righe consecutive
    tops = [l[0] for l in text_lines]
    gaps = np.diff(tops) if len(tops) > 1 else np.array([size * 1.32])
    pitch = float(np.percentile(gaps, 30)) if len(gaps) else size * 1.32
    line_ratio = float(np.clip(pitch / max(size, 1), 1.1, 1.8))
    big = [g - pitch for g in gaps if g > pitch * 1.4]
    blank_ratio = float(np.clip((np.median(big) / max(size, 1)) if big else 0.8, 0.4, 1.6))
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
    if tight:
        weight = weight if weight.endswith("_tight") else weight + "_tight"
        if head_weight and not head_weight.endswith("_tight"):
            head_weight += "_tight"
    block_center = (top + bottom) / 2
    if align == "center":
        width = int(min(W * 0.9, (np.max(rights) - np.min(lefts)) * 1.1))
    else:
        width = int(W - left - max(left * 0.6, W * 0.05))
    # maschera da cancellare: tutto ciò che non è sfondo (testo, watermark, avatar), un po' allargata
    # soglia di cancellazione in base al rumore dello sfondo: su un nero pulito si tolgono anche gli aloni grigi
    noise = float(np.percentile(np.abs(border - bg).max(axis=1), 95))
    erase = dist > max(10.0, noise * 2 + 4)
    erase = _dilate(erase, max(2, size // 10))
    return Layout(bg=tuple(int(c) for c in bg), color=color, weight=weight, align=align, left=left,
                  top=int(top), bottom=int(bottom), width=width, size=max(14, size),
                  centered_v=abs(block_center - H / 2) < H * 0.08, mask=erase,
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


def background(img: Image.Image, lay: Layout) -> Image.Image:
    """La slide senza testo (per il file Canva)."""
    return _clean(img, lay)


def render(img: Image.Image, lay: Layout, text: str, placements: list | None = None) -> Image.Image:
    """Scrive il testo. Se `placements` è una lista, ci aggiunge (riga, x, y, corpo, peso, colore) per il file Canva."""
    out = _clean(img, lay)
    text = text.strip()
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
        y = max(int(H * 0.06), min(lay.top - int(size * 0.2), int(H - total - H * 0.08)))
    left = lay.left
    if lay.align != "center" and lay.right and abs((lay.left + lay.right) / 2 - W / 2) < W * 0.045:
        # testo allineato a sinistra ma blocco centrato nella slide: si ricentra il blocco italiano
        block_w = max((draw.textlength(ln, font=font) for ln in lines if ln), default=0)
        left = int((W - block_w) / 2)
    in_head = bool(lay.head_color)
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
            col, wt = lay.head_color, (lay.head_weight or lay.weight)
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
    ys, xs = np.where(diff)
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
    for para in text.split("\n\n"):
        rows = []
        for row in para.split("\n"):
            words, hl, bd = [], False, False
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
    if weight.endswith("_tight"):
        return "bold_tight"
    return "bold" if weight in ("regular", "semibold") else weight


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
    if (np.abs(ring - bg).max(axis=1) < 30).mean() < 0.88:
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
    pitch = None
    if own.any():
        ys, xs = np.where(own)
        tight = (X0 + int(xs.min()), Y0 + int(ys.min()), X0 + int(xs.max()), Y0 + int(ys.max()))
        rows = _runs(own.sum(axis=1) >= 2, min_gap=2)
        if len(rows) >= 2:
            pitch = float(np.median(np.diff([r[0] for r in rows])))
    else:
        tight = box
    m = _dilate(own, 3)
    sub[m] = np.array(bg)
    a[Y0:Y1, X0:X1] = sub.astype(np.uint8)
    return Image.fromarray(a), tight, pitch


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


def inpaint_boxes(img: Image.Image, boxes: list[tuple[int, int, int, int]], pad: float = 0.012) -> Image.Image:
    """Cancella col codice le scritte dentro i riquadri anche su foto o sfumature (senza Gemini):
    trova i pixel delle lettere confrontando con lo sfondo 'sfocato' e ricostruisce lo sfondo attorno."""
    import cv2
    a = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    H, W = a.shape[:2]
    mask = np.zeros((H, W), np.uint8)
    for x0, y0, x1, y1 in boxes:
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
        m = (diff > 38).astype(np.uint8) * 255
        m = cv2.dilate(m, np.ones((5, 5), np.uint8), iterations=2)
        mask[Y0:Y1, X0:X1] = np.maximum(mask[Y0:Y1, X0:X1], m)
    if not mask.any():
        return img.convert("RGB")
    out = cv2.inpaint(a, mask, 6, cv2.INPAINT_TELEA)
    return Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))


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
        img, tight, pitch = erase_local(img, box, bg, [b for j, b in enumerate(all_boxes) if j != i])
        boxes.append((tight, pitch))
    return img, boxes


def render_blocks(cleaned: Image.Image, original: Image.Image, blocks: list[dict],
                  placements: list | None = None, boxes: list | None = None) -> Image.Image:
    """Scrive i blocchi di testo italiano sull'immagine ripulita, nei riquadri del testo originale.
    `boxes` = riquadri già precisi (pulizia col codice); altrimenti si affinano confrontando con l'originale."""
    out = cleaned.copy()
    W, H = out.size
    draw = ImageDraw.Draw(out)
    for idx, bl in enumerate(blocks):
        text = (bl.get("testo") or "").strip()
        if not text:
            continue
        if bl.get("maiuscolo"):
            text = text.upper()
        pitch = None
        if boxes:
            (box, pitch), color = boxes[idx], None
        else:
            box, color = refine_box(original, cleaned, block_box(bl, W, H))
        color = _hex(bl.get("colore")) if (boxes or not color) else color
        hl_color = _hex(bl.get("colore_evidenziato")) if bl.get("colore_evidenziato") else color
        bw, bh = max(40, box[2] - box[0]), max(20, box[3] - box[1])
        car = bl.get("carattere") or "normale"
        weight = car if car in ("titolo", "stretto") else (bl.get("peso") or "regular")
        paras = _tokens(text)
        ratio = 1.15 if car in ("titolo", "stretto") else 1.25   # spazio per gli accenti sulle maiuscole
        size, best = max(14, int(bh)), None
        if pitch:                                   # mai più grande dell'originale
            size = min(size, int(pitch / ratio * 1.02))
        while size >= 14:
            font = _font(weight, size)
            bfont = _font(_bold_weight(weight), size)
            lines = _layout(draw, paras, font, int(bw * 1.03), bfont)
            lh = int(size * ratio)
            total = sum(int(size * 0.6) if ln is None else lh for ln in lines)
            ink_h = total - lh + size * 0.95          # altezza reale delle lettere (l'ultima riga senza interlinea)
            widest = max((_line_w(draw, ln, font, bfont) for ln in lines if ln), default=0)
            if ink_h <= bh * 1.12 and widest <= bw * 1.05:
                best = (font, bfont, lines, lh, total)
                break
            size -= 1 if size < 40 else 2
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
        margin = int(H * 0.05)
        if y + (total - lh) + bot_off > H - margin:            # mai attaccato al bordo in basso
            y = H - margin - (total - lh) - bot_off
        if y + top_off < margin:
            y = margin - top_off
        space = draw.textlength(" ", font=font)
        al = bl.get("allineamento", "left")
        x_left = box[0]
        if al == "left" and abs((box[0] + box[2]) / 2 - W / 2) < W * 0.045:
            # blocco centrato nella slide: resta centrato anche se le righe italiane hanno lunghezza diversa
            widest = max((_line_w(draw, ln, font, bfont) for ln in lines if ln), default=0)
            x_left = int((W - widest) / 2)
        for ln in lines:
            if ln is None:
                y += int(font.size * 0.6)
                continue
            tw = _line_w(draw, ln, font, bfont)
            x = x_left if al == "left" else (box[2] - tw if al == "right" else box[0] + (bw - tw) / 2)
            runs = []
            for w, fl in ln:
                c = hl_color if fl & 1 else color
                f = _wfont(font, bfont, fl)
                draw.text((x, y), w, font=f, fill=c)
                runs.append((w, c, bool(fl & 2)))
                x += draw.textlength(w, font=f) + space
            if placements is not None:
                x_start = x_left if al == "left" else (box[2] - tw if al == "right" else box[0] + (bw - tw) / 2)
                placements.append({"testo": " ".join(w for w, _ in ln), "x": float(x_start), "y": float(y),
                                   "size": font.size, "weight": weight, "color": color,
                                   "runs": [[w, list(c), b] for w, c, b in runs]})
            y += lh
    return out
