"""Mockup 3D dei manuali (libro in piedi con dorso, ombra e riflesso) dalle copertine in assets/manuali.
Gabriele 15/11: nelle storie di vendita il libro va mostrato in bella vista."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import config

COPERTINE = Path(__file__).resolve().parent.parent / "assets" / "manuali"
# id del manuale -> copertine (il bundle mostra due libri)
MAPPA = {"potere-sociale-solo": ["potere-sociale"], "bundle-attrazione-potere": ["attraiseduci", "potere-sociale"],
         "potere-sociale": ["attraiseduci", "potere-sociale"],
         # manuale + bonus (copertine di Gabriele, 15/11)
         "arte-sedurre-chat": ["arte-sedurre-chat", "bonus-100-messaggi"],
         "dipendenza-emotiva": ["dipendenza-emotiva", "bonus-30-giorni-dipendenza"],
         "lasciare-andare": ["lasciare-andare", "bonus-strumenti-lasciare-andare"],
         "magnetismo-femminile": ["magnetismo-femminile", "bonus-desiderata-scelta"]}


def copertine(manuale_id: str) -> list[Path]:
    nomi = MAPPA.get(manuale_id, [manuale_id])
    return [COPERTINE / f"{n}.png" for n in nomi if (COPERTINE / f"{n}.png").exists()]


def _coeffs(dst, src):
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        B += [u, v]
    return np.linalg.solve(np.array(A, float), np.array(B, float)).tolist()


def libro(cover_path: Path) -> Image.Image:
    """Un libro 3D su sfondo trasparente."""
    cov = Image.open(cover_path).convert("RGB")
    cw, ch = cov.size
    W, H = 1500, 1900
    can = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sp, L, R = 110, 380, 1330
    altezza = 1540 * min(1.0, (ch / cw) / 1.5)          # copertine più quadrate -> libro più basso
    top_l, bot_l = 950 - altezza / 2, 950 + altezza / 2
    top_r, bot_r = top_l + 50, bot_l - 50
    quad = [(L, top_l), (R, top_r), (R, bot_r), (L, bot_l)]
    sh = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sh).polygon([(L - sp + 40, bot_l - 10), (R + 60, bot_r + 10), (R + 160, bot_r + 90),
                                (L - sp + 140, bot_l + 110)], fill=170)
    can.paste((0, 0, 0, 255), mask=sh.filter(ImageFilter.GaussianBlur(45)))
    c = cov.transform((W, H), Image.PERSPECTIVE, _coeffs(quad, [(0, 0), (cw, 0), (cw, ch), (0, ch)]), Image.BICUBIC)
    g = np.linspace(1.12, 0.86, W)[None, :, None]
    c = Image.fromarray(np.clip(np.asarray(c).astype(float) * g, 0, 255).astype("uint8"))
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).polygon(quad, fill=255)
    can.paste(c, mask=m)
    spq = [(L - sp, top_l + 28), (L, top_l), (L, bot_l), (L - sp, bot_l - 28)]
    strip = cov.crop((0, 0, max(8, cw // 25), ch)).resize((sp, ch))
    strip = Image.fromarray((np.asarray(strip).astype(float) * 0.55).astype("uint8"))
    s2 = strip.transform((W, H), Image.PERSPECTIVE, _coeffs(spq, [(0, 0), (sp, 0), (sp, ch), (0, ch)]), Image.BICUBIC)
    m2 = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m2).polygon(spq, fill=255)
    can.paste(s2, mask=m2)
    d = ImageDraw.Draw(can)
    d.line([(L, top_l), (L, bot_l)], fill=(255, 255, 255, 70), width=3)
    d.line([(L + 22, top_l + 2), (L + 22, bot_l - 2)], fill=(0, 0, 0, 90), width=4)
    gl = Image.new("L", (W, H), 0)
    ImageDraw.Draw(gl).polygon([(L + 60, top_l), (L + 380, top_l + 15), (L + 120, bot_l), (L + 20, bot_l)], fill=38)
    gl = Image.composite(gl.filter(ImageFilter.GaussianBlur(30)), Image.new("L", (W, H), 0), m)
    can.paste((255, 255, 255, 255), mask=gl)
    return can.crop(can.getbbox())


def mockup(manuale_id: str) -> Image.Image | None:
    """Mockup del manuale (due libri affiancati per il bundle), con cache su disco."""
    covs = copertine(manuale_id)
    if not covs:
        return None
    cache = Path(config.DATA_DIR) / "mockup" / f"{manuale_id}.png"
    try:
        if cache.exists() and cache.stat().st_mtime >= max(p.stat().st_mtime for p in covs):
            return Image.open(cache).convert("RGBA")
    except OSError:
        pass
    libri = [libro(p) for p in covs]
    if len(libri) == 1:
        out = libri[0]
    else:
        h = max(b.height for b in libri)
        libri = [b.resize((int(b.width * h / b.height), h), Image.LANCZOS) for b in libri]
        out = Image.new("RGBA", (int(sum(b.width for b in libri) * 0.9), int(h * 1.05)), (0, 0, 0, 0))
        out.paste(libri[0], (0, 0), libri[0])
        out.paste(libri[1], (out.width - libri[1].width, int(h * 0.05)), libri[1])
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        out.save(cache)
    except OSError:
        pass
    return out


def su_sfondo(base: Image.Image, manuale_id: str, altezza: int = 700, top: int = 240,
              colore: tuple = (200, 40, 50)) -> tuple[Image.Image, int] | None:
    """Mette il mockup in alto sulla storia, con un alone colorato dietro. Restituisce (immagine, y fine mockup)."""
    m = mockup(manuale_id)
    if m is None:
        return None
    out = base.copy().convert("RGB")
    r = min(altezza / m.height, 900 / m.width)
    m = m.resize((int(m.width * r), int(m.height * r)), Image.LANCZOS)
    glow = Image.new("L", out.size, 0)
    ImageDraw.Draw(glow).ellipse((out.width // 2 - 400, top - 40, out.width // 2 + 400, top + m.height + 40), fill=160)
    out.paste(colore, mask=glow.filter(ImageFilter.GaussianBlur(120)))
    out.paste(m, ((out.width - m.width) // 2 + 15, top), m)
    return out, top + m.height
