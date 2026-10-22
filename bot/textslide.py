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


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    p = FONTS.get(weight) or FONTS["regular"]
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


def analyze(img: Image.Image, original_text: str = "") -> Layout | None:
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
    for y0, y1 in _runs(ink.sum(axis=1) >= 2, min_gap=1):
        cols = np.where(ink[y0:y1 + 1].any(axis=0))[0]
        lines.append((y0, y1, int(cols.min()), int(cols.max())))
    if not lines:
        return None
    heights = np.array([l[1] - l[0] + 1 for l in lines])
    med_h = float(np.median(heights))
    # un blocco molto più alto di una riga di testo, fuori dalle fasce alta e bassa dei marchi, è un'illustrazione:
    # quella slide la lascia a Gemini per non cancellarla
    for (y0, y1, x0, x1), h in zip(lines, heights):
        if h > 2.6 * med_h and y0 > H * 0.22 and y1 < H * 0.86:
            return None
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
    by_text = _size_from_text(text_lines, original_text, weight)
    if by_text:
        size = by_text
        stroke = (np.median(runs) / size) if runs else 0.1
        weight = "bold" if stroke > 0.125 else ("semibold" if stroke > 0.105 else "regular")

    # interlinea misurata: distanza tipica tra l'inizio di due righe consecutive
    tops = [l[0] for l in text_lines]
    gaps = np.diff(tops) if len(tops) > 1 else np.array([size * 1.32])
    pitch = float(np.percentile(gaps, 30)) if len(gaps) else size * 1.32
    line_ratio = float(np.clip(pitch / max(size, 1), 1.1, 1.8))
    big = [g - pitch for g in gaps if g > pitch * 1.4]
    blank_ratio = float(np.clip((np.median(big) / max(size, 1)) if big else 0.8, 0.4, 1.6))
    block_center = (top + bottom) / 2
    if align == "center":
        width = int(min(W * 0.9, (np.max(rights) - np.min(lefts)) * 1.1))
    else:
        width = int(W - left - max(left * 0.6, W * 0.05))
    # maschera da cancellare: tutto ciò che non è sfondo (testo, watermark, avatar), un po' allargata
    erase = dist > 25
    erase = _dilate(erase, max(2, size // 10))
    return Layout(bg=tuple(int(c) for c in bg), color=color, weight=weight, align=align, left=left,
                  top=int(top), bottom=int(bottom), width=width, size=max(14, size),
                  centered_v=abs(block_center - H / 2) < H * 0.08, mask=erase,
                  line_ratio=line_ratio, blank_ratio=blank_ratio)


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
    return Image.fromarray(a)


def _flow(draw, words: list[str], font, max_w: int) -> list[str]:
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
    for ln in lines:
        if not ln:
            y += blank_h
            continue
        if lay.align == "center":
            x = (W - draw.textlength(ln, font=font)) / 2
        else:
            x = lay.left
        draw.text((x, y), ln, font=font, fill=lay.color)
        if placements is not None:
            placements.append({"testo": ln, "x": float(x), "y": float(y), "size": size,
                               "weight": lay.weight, "color": lay.color})
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


def render_blocks(cleaned: Image.Image, original: Image.Image, blocks: list[dict],
                  placements: list | None = None) -> Image.Image:
    """Scrive i blocchi di testo italiano sull'immagine ripulita, nei riquadri del testo originale."""
    out = cleaned.copy()
    W, H = out.size
    draw = ImageDraw.Draw(out)
    for bl in blocks:
        text = (bl.get("testo") or "").strip()
        if not text:
            continue
        if bl.get("maiuscolo"):
            text = text.upper()
        box = (int(bl["x"] / 1000 * W), int(bl["y"] / 1000 * H),
               int((bl["x"] + bl["w"]) / 1000 * W), int((bl["y"] + bl["h"]) / 1000 * H))
        box, color = refine_box(original, cleaned, box)
        color = color or _hex(bl.get("colore"))
        bw, bh = max(40, box[2] - box[0]), max(20, box[3] - box[1])
        weight = bl.get("peso") or "regular"
        # il corpo più grande che fa stare il testo nel riquadro (con un po' di tolleranza in altezza)
        size, best = max(14, int(bh)), None
        while size >= 14:
            font = _font(weight, size)
            lines = _wrap(draw, text, font, int(bw * 1.04))
            lh = int(size * 1.25)
            total = sum(int(size * 0.7) if not ln else lh for ln in lines)
            widest = max((draw.textlength(ln, font=font) for ln in lines if ln), default=0)
            if total <= bh * 1.12 and widest <= bw * 1.06:
                best = (font, lines, lh, total)
                break
            size -= 1 if size < 40 else 2
        if not best:
            font = _font(weight, 14)
            lines = _wrap(draw, text, font, bw)
            best = (font, lines, int(14 * 1.25), len(lines) * int(14 * 1.25))
        font, lines, lh, total = best
        y = box[1] + (bh - total) / 2
        for ln in lines:
            if not ln:
                y += int(size * 0.7)
                continue
            tw = draw.textlength(ln, font=font)
            al = bl.get("allineamento", "left")
            x = box[0] if al == "left" else (box[2] - tw if al == "right" else box[0] + (bw - tw) / 2)
            draw.text((x, y), ln, font=font, fill=color)
            if placements is not None:
                placements.append({"testo": ln, "x": float(x), "y": float(y), "size": font.size,
                                   "weight": weight, "color": color})
            y += lh
    return out
