"""Slide CTA illustrata per i post disegnati/fotografici con CTA a un manuale.

1. Gemini disegna la scena nello stile del post (uomo che mostra i manuali), con le copertine come
   rettangoli verdi pieni e senza nessuna scritta.
2. Il codice trova i rettangoli verdi e ci applica le copertine vere in prospettiva (nitide e identiche).
3. Il codice scrive i testi (titolo in stile fumetto con parole rosse, testo, chiusura) dentro riquadri bianchi.
Se qualcosa non riesce si torna alla CTA normale.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config

log = logging.getLogger(__name__)

COVERS = config.ROOT / "assets" / "manuali"
MARKER = config.ROOT / "assets" / "fumetto_marker.ttf"
BODY = config.ROOT / "assets" / "titolo_oswald_bold.ttf"

# quali copertine mostrare per ogni CTA
BOOKS = {
    "attraiseduci": ["attraiseduci"],
    "potere-sociale": ["attraiseduci", "potere-sociale"],     # il bundle: si vedono tutti e due
    "friendzone": ["friendzone"],
}

PROMPT = (
    "Create a NEW vertical 4:5 illustration in EXACTLY the same art style, colors and rendering as the reference "
    "image (same kind of drawing, same lighting, same mood). Scene: a confident, well-dressed man, the kind of "
    "character that appears in the reference style, standing or sitting and proudly holding up {n} towards the "
    "viewer. They are real thick HARDCOVER BOOKS: draw the book body in 3D with visible thickness, the spine "
    "and the page edges, slightly tilted, held firmly with fingers on the sides or behind the book. "
    "Each book's FRONT COVER face must be a perfectly flat, solid, pure bright green (#00FF00) area, "
    "fully visible, not covered by fingers or hands, with no texture and no drawing on it. "
    "The background is a stylish city/office setting in the same style. "
    "IMPORTANT: absolutely NO text, letters, numbers, logos or speech bubbles anywhere in the image. "
    "Keep the top 30% of the image and the bottom 16% of the image calm and simple (sky, wall or floor), "
    "because text will be added there later."
)


def _green_quads(img: Image.Image, want: int) -> list[np.ndarray]:
    import cv2
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = ((g > 150) & (g - r > 70) & (g - b > 70)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    H, W = mask.shape
    quads = []
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:want]:
        if cv2.contourArea(c) < W * H * 0.01:
            continue
        rect = cv2.minAreaRect(c)
        hull = cv2.convexHull(c)
        approx = cv2.approxPolyDP(hull, 0.03 * cv2.arcLength(hull, True), True)
        pts = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(rect)
        pts = np.array(pts, dtype=np.float32)
        s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
        ordered = np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
                           dtype=np.float32)          # alto-sx, alto-dx, basso-dx, basso-sx
        quads.append(ordered)
    quads.sort(key=lambda q: q[:, 0].mean())          # da sinistra a destra
    return quads, mask


def _book_face(cover: Image.Image) -> Image.Image:
    """La copertina come quella di un libro stampato: bordo scuro sottile, piega vicino al dorso,
    leggera lucentezza e ombra, così non sembra un rettangolo piatto incollato."""
    cov = cover.convert("RGB")
    W, H = cov.size
    a = np.asarray(cov).astype(np.float32)
    x = np.linspace(0, 1, W)[None, :, None]
    y = np.linspace(0, 1, H)[:, None, None]
    shade = 1.0 - 0.28 * np.clip(1 - x / 0.06, 0, 1)                 # zona del dorso più scura
    shade *= 1.0 - 0.10 * np.clip((x - 0.85) / 0.15, 0, 1)            # bordo esterno leggermente in ombra
    hinge = np.exp(-((x - 0.045) ** 2) / 0.00012) * 0.18               # piega della copertina rigida
    gloss = 0.10 * np.exp(-(((x - 0.30) - (y - 0.15) * 0.6) ** 2) / 0.02)   # riflesso diagonale
    a = a * (shade - hinge) + 255 * gloss
    a = np.clip(a, 0, 255).astype(np.uint8)
    img = Image.fromarray(a)
    d = ImageDraw.Draw(img)
    e = max(2, W // 120)
    d.rectangle((0, 0, W - 1, H - 1), outline=(25, 22, 20), width=e)   # bordo della copertina
    return img


def _paste_covers(img: Image.Image, quads: list[np.ndarray], covers: list[Path], mask: np.ndarray) -> Image.Image:
    import cv2
    out = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    H, W = out.shape[:2]
    for q, cp in zip(quads, covers):
        cov = cv2.cvtColor(np.asarray(_book_face(Image.open(cp))), cv2.COLOR_RGB2BGR)
        ch, cw = cov.shape[:2]
        src = np.array([[0, 0], [cw - 1, 0], [cw - 1, ch - 1], [0, ch - 1]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(src, q)
        warped = cv2.warpPerspective(cov, M, (W, H), flags=cv2.INTER_LANCZOS4)
        m = np.zeros((H, W), np.uint8)
        cv2.fillConvexPoly(m, q.astype(np.int32), 255)
        out[m > 0] = warped[m > 0]
    # bordini verdi rimasti attorno alle copertine: si ricostruiscono
    a = out.astype(np.int16)
    left = ((a[..., 1] > 140) & (a[..., 1] - a[..., 2] > 60) & (a[..., 1] - a[..., 0] > 60)).astype(np.uint8) * 255
    if left.any():
        left = cv2.dilate(left, np.ones((3, 3), np.uint8), iterations=2)
        out = cv2.inpaint(out, left, 3, cv2.INPAINT_TELEA)
    return Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))


def _fit_font(draw, path: Path, text_lines: list[str], max_w: int, start: int, min_size: int = 18):
    size = start
    while size > min_size:
        f = ImageFont.truetype(str(path), size)
        if all(draw.textlength(t, font=f) <= max_w for t in text_lines):
            return f
        size -= 2
    return ImageFont.truetype(str(path), min_size)


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    out = []
    for para in text.split("\n"):
        words, line = para.split(), ""
        for w in words:
            t = (line + " " + w).strip()
            if not line or draw.textlength(t.replace("[[", "").replace("]]", ""), font=font) <= max_w:
                line = t
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
    return out


def _draw_rich(draw, x, y, line: str, font, color, red):
    """Riga con le parole tra [[ ]] in rosso."""
    hl = False
    for word in line.split(" "):
        start, end = word.startswith("[["), word.endswith("]]")
        clean = word.replace("[[", "").replace("]]", "")
        if start:
            hl = True
        draw.text((x, y), clean, font=font, fill=red if hl else color)
        x += draw.textlength(clean + " ", font=font)
        if end:
            hl = False


def _panel(img: Image.Image, box, radius: int):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = box
    d.rounded_rectangle((x0 + 6, y0 + 8, x1 + 6, y1 + 8), radius, fill=(0, 0, 0, 90))      # ombra
    d.rounded_rectangle(box, radius, fill=(255, 255, 255, 242), outline=(20, 20, 20, 255), width=5)
    return Image.alpha_composite(img.convert("RGBA"), layer)


def _texts(img: Image.Image, text: str) -> Image.Image:
    """Titolo in alto, frase e chiusura in basso, in riquadri bianchi stile fumetto."""
    W, H = img.size
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        return img
    head = paras[0].upper()
    body = "\n".join(paras[1:-1]) if len(paras) > 2 else ""
    close = paras[-1].upper() if len(paras) > 1 else ""
    red = (205, 30, 30)
    margin = int(W * 0.065)
    inner = W - 2 * margin - int(W * 0.06)
    d = ImageDraw.Draw(img)
    # titolo (le parole tra [[ ]] in rosso; se non ce ne sono, l'ultima parola forte in rosso)
    if "[[" not in head:
        words = head.split()
        if len(words) > 2:
            head = " ".join(words[:-2] + ["[[" + " ".join(words[-2:]) + "]]"])
    fh = ImageFont.truetype(str(MARKER), int(W * 0.075))
    lines_h = _wrap(d, head, fh, inner)
    while len(lines_h) > 3 and fh.size > 30:
        fh = ImageFont.truetype(str(MARKER), fh.size - 4)
        lines_h = _wrap(d, head, fh, inner)
    lh = int(fh.size * 1.12)
    top_box = (margin, int(H * 0.035), W - margin, int(H * 0.035) + lh * len(lines_h) + int(fh.size * 0.7))
    img = _panel(img, top_box, 34)
    d = ImageDraw.Draw(img)
    y = top_box[1] + int(fh.size * 0.3)
    for ln in lines_h:
        w = d.textlength(ln.replace("[[", "").replace("]]", ""), font=fh)
        _draw_rich(d, (W - w) / 2, y, ln, fh, (15, 15, 15), red)
        y += lh
    # testo + chiusura in basso
    fb = ImageFont.truetype(str(BODY), int(W * 0.04))
    fc = ImageFont.truetype(str(MARKER), int(W * 0.058))
    lines_b = _wrap(d, body, fb, inner) if body else []
    lines_c = _wrap(d, close, fc, inner) if close else []
    hb = int(fb.size * 1.25) * len(lines_b) + (int(fc.size * 1.15) * len(lines_c)) + int(W * 0.05)
    if lines_b or lines_c:
        bot_box = (margin, H - int(H * 0.035) - hb, W - margin, H - int(H * 0.035))
        img = _panel(img, bot_box, 34)
        d = ImageDraw.Draw(img)
        y = bot_box[1] + int(W * 0.025)
        for ln in lines_b:
            w = d.textlength(ln.replace("[[", "").replace("]]", ""), font=fb)
            _draw_rich(d, (W - w) / 2, y, ln, fb, (25, 25, 25), red)
            y += int(fb.size * 1.25)
        for ln in lines_c:
            w = d.textlength(ln.replace("[[", "").replace("]]", ""), font=fc)
            _draw_rich(d, (W - w) / 2, y, ln, fc, red, red)
            y += int(fc.size * 1.15)
    return img.convert("RGB")


def make(post: dict, originals: list[bytes], jdir: Path) -> tuple[Path, dict] | None:
    cta = post.get("cta") or {}
    if cta.get("tipo") != "manuale":
        return None
    books = [COVERS / f"{b}.png" for b in BOOKS.get(cta.get("manuale") or "", [])]
    if not books or not all(b.exists() for b in books):
        return None
    text = (post.get("_testo_cta_finale") or "").strip()
    if not text or not config.GEMINI_API_KEY:
        return None
    from . import images
    ref = Image.open(io.BytesIO(originals[0])).convert("RGB")
    n = "one book" if len(books) == 1 else f"{len(books)} books, one in each hand"
    for attempt in range(2):
        try:
            gen = images._gemini_edit(ref, PROMPT.format(n=n))
        except Exception as e:
            log.warning("CTA illustrata: Gemini non ha risposto (%s)", e)
            gen = None
        if gen is None:
            continue
        size = images.output_size(ref)
        gen = images._fit(gen, size)
        quads, mask = _green_quads(gen, len(books))
        if len(quads) != len(books):
            log.info("CTA illustrata: trovati %s libri su %s, riprovo", len(quads), len(books))
            continue
        img = _paste_covers(gen, quads, books, mask)
        bg = img.copy()
        img = _texts(img, text)
        out = jdir / "cta_illustrata.jpg"
        img.save(out, "JPEG", quality=95, subsampling=0)
        bgp = jdir / "sfondo_cta_illustrata.jpg"
        bg.save(bgp, "JPEG", quality=95)
        info = {"numero": 0, "testo": text.replace("[[", "").replace("]]", ""), "testo_originale": "",
                "watermark_originale": "", "e_cta": True, "da_modificare": False, "_via": "cta-illustrata",
                "_canva": {"tipo": "immagine"}}
        return out, info
    return None
