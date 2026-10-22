"""Gemini + Pillow: testo italiano sulla slide originale, rimozione watermark, nostro logo."""
from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config, textslide, writer

log = logging.getLogger(__name__)

_gclient = None
RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9"]
OUT_WIDTH = 1080


def gclient():
    global _gclient
    if _gclient is None:
        from google import genai
        _gclient = genai.Client(api_key=config.GEMINI_API_KEY)
    return _gclient


# ---------------------------------------------------------------- utilità

def _font(size: int) -> ImageFont.FreeTypeFont:
    if config.FONT_PATH.exists():
        return ImageFont.truetype(str(config.FONT_PATH), size)
    return ImageFont.load_default(size=size)


def _hex(c: str, alpha: int = 255) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def nearest_ratio(w: int, h: int) -> str:
    r = w / h
    return min(RATIOS, key=lambda s: abs(int(s.split(":")[0]) / int(s.split(":")[1]) - r))


def _fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Ridimensiona e ritaglia al centro per riempire esattamente `size`."""
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - tw) // 2, (img.height - th) // 2
    return img.crop((left, top, left + tw, top + th))


def output_size(original: Image.Image) -> tuple[int, int]:
    return OUT_WIDTH, round(OUT_WIDTH * original.height / original.width)


# ---------------------------------------------------------------- Gemini

def _base_prompt(slide: dict, mode: str, feedback: str = "") -> str:
    wm = slide.get("watermark_originale") or "any handle, page name, username or logo of the original page"
    remove = (f"Completely remove the watermark of the original page ({wm}) and rebuild the background "
              "underneath so that no trace of it remains.")
    keep = ("Keep the illustration, characters, background, colors and composition exactly as they are. "
            "Do not add any other text, logo, handle or watermark. Keep the bottom-right corner clean.")
    testo = (slide.get("testo") or "").strip()

    if mode == "overlay":
        return ("Edit this Instagram slide. Remove ALL the text on it, rebuilding the background where the text was. "
                f"{remove} {keep}")
    if not testo:
        if (slide.get("testo_originale") or "").strip():
            return f"Edit this Instagram slide. Remove all the text on it, rebuilding the background. {remove} {keep}"
        return f"Edit this Instagram slide. {remove} {keep}"
    return (
        "Edit this Instagram slide.\n"
        "1. Replace all the existing text with the Italian text below. Use exactly the same font style, weight, size, "
        "color, alignment, line breaks and position as the original text, so it looks like the original designer wrote it. "
        "Spell every word EXACTLY as given, including accents and punctuation:\n"
        f'"""\n{testo}\n"""\n'
        f"2. {remove}\n"
        f"3. {keep}"
    )


def _prompt(slide: dict, mode: str, feedback: str = "") -> str:
    p = _base_prompt(slide, mode)
    if feedback:
        p += f"\n\nA previous attempt had these problems, fix them: {feedback}"
    return p


def _gemini_edit(original: Image.Image, prompt: str) -> Image.Image | None:
    from google.genai import types
    resp = gclient().models.generate_content(
        model=config.GEMINI_IMAGE_MODEL,
        contents=[prompt, original],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=nearest_ratio(original.width, original.height)),
        ),
    )
    parts = getattr(resp, "parts", None)
    if not parts and getattr(resp, "candidates", None):
        content = resp.candidates[0].content
        parts = content.parts if content else None
    for part in parts or []:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            return Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
    return None


# ---------------------------------------------------------------- testo e logo con Pillow

def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words, line = paragraph.split(), ""
        for w in words:
            test = f"{line} {w}".strip()
            if draw.textlength(test, font=font) <= max_w or not line:
                line = test
            else:
                lines.append(line)
                line = w
        lines.append(line)
    return lines


def draw_text_overlay(img: Image.Image, text: str, brand: dict) -> Image.Image:
    """Scrive il testo italiano centrato, con dimensione automatica (modalità overlay)."""
    if not text.strip():
        return img
    style = brand.get("testo_overlay", {})
    img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)
    max_w, max_h = int(img.width * 0.84), int(img.height * 0.62)
    size = int(img.width * 0.075)
    while size > 22:
        font = _font(size)
        lines = _wrap(draw, text, font, max_w)
        line_h = int(size * 1.25)
        if line_h * len(lines) <= max_h:
            break
        size -= 3
    block_h = line_h * len(lines)
    pos = style.get("posizione", "centro")
    y = {"alto": int(img.height * 0.12), "basso": img.height - block_h - int(img.height * 0.16)}.get(
        pos, (img.height - block_h) // 2)
    for ln in lines:
        w = draw.textlength(ln, font=font)
        draw.text(((img.width - w) / 2, y), ln, font=font, fill=_hex(style.get("colore", "#FFFFFF")),
                  stroke_width=max(2, size // 18), stroke_fill=_hex(style.get("contorno", "#000000")))
        y += line_h
    return img.convert("RGB")


def _circle_logo(diameter: int) -> Image.Image | None:
    if not config.LOGO_PATH.exists():
        return None
    logo = Image.open(config.LOGO_PATH).convert("RGBA")
    side = min(logo.size)
    logo = logo.crop(((logo.width - side) // 2, (logo.height - side) // 2,
                      (logo.width + side) // 2, (logo.height + side) // 2))
    logo = logo.resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter * 4, diameter * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter * 4 - 1, diameter * 4 - 1), fill=255)
    mask = mask.resize((diameter, diameter), Image.LANCZOS)
    alpha = Image.composite(logo.getchannel("A"), Image.new("L", logo.size, 0), mask)
    logo.putalpha(alpha)
    return logo


def add_brand_mark(img: Image.Image, brand: dict) -> Image.Image:
    """Logo circolare + nome della pagina, al posto del watermark originale."""
    wm = brand.get("watermark", {})
    W, H = img.size
    d = max(36, int(W * float(wm.get("larghezza_logo", 0.075))))
    margin = int(W * 0.04)
    text = wm.get("testo", brand.get("nome", ""))
    font = _font(int(d * 0.42))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    text_w = int(draw.textlength(text, font=font))
    gap = int(d * 0.25)
    logo = _circle_logo(d)
    total_w = (d + gap if logo else 0) + text_w

    pos = wm.get("posizione", "basso-destra")
    x = {"basso-sinistra": margin, "alto-sinistra": margin,
         "basso-centro": (W - total_w) // 2}.get(pos, W - margin - total_w)
    y = margin if pos.startswith("alto") else H - margin - d

    if logo:
        layer.alpha_composite(logo, (x, y))
        tx = x + d + gap
    else:
        tx = x
    bbox = draw.textbbox((0, 0), text, font=font)
    ty = y + (d - (bbox[3] - bbox[1])) // 2 - bbox[1]
    # ombra morbida per leggibilità su qualsiasi sfondo
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((tx + 2, ty + 2), text, font=font, fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(3))
    layer = Image.alpha_composite(shadow, layer)
    colore = str(wm.get("colore_testo", "auto"))
    if colore.lower() == "auto":   # testo bianco su sfondi scuri, nero su sfondi chiari
        zona = img.convert("L").crop((max(0, tx - 10), y, min(W, tx + text_w + 10), min(H, y + d)))
        lum = sum(zona.getdata()) / max(1, zona.width * zona.height)
        colore = "#111111" if lum > 150 else "#FFFFFF"
        if lum > 150:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))   # niente ombra scura su sfondo chiaro
            if logo:
                layer.alpha_composite(logo, (x, y))
    ImageDraw.Draw(layer).text((tx, ty), text, font=font, fill=_hex(colore))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


# ---------------------------------------------------------------- slide complete

def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _remove_watermark(clean: Image.Image, orig_fit: Image.Image, slide: dict):
    """Watermark della pagina di origine: lo cancella col codice se sta su sfondo uniforme nella fascia bassa
    o alta (dove di solito si trova), altrimenti lo lascia a Gemini con un'unica modifica mirata."""
    W, H = clean.size
    pos = (slide.get("watermark_originale") or "").lower()
    bands = []
    if any(k in pos for k in ("basso", "bottom", "sotto")) or not any(k in pos for k in ("alto", "top", "sopra")):
        bands.append((8, int(H * 0.86), W - 8, H - 8))
    if any(k in pos for k in ("alto", "top", "sopra")):
        bands.append((8, 8, W - 8, int(H * 0.12)))
    done = False
    for band in bands:
        bg = textslide.local_background(clean, band)
        if bg is not None:
            clean, _, _ = textslide.erase_local(clean, band, bg)
            done = True
    if done:
        return clean
    try:
        edited = _gemini_edit(clean, _prompt({**slide, "testo": ""}, "gemini"))
        if edited is not None:
            return _fit(edited, clean.size)
    except Exception as e:
        log.warning("Rimozione watermark con Gemini fallita: %s", e)
    return clean


def needs_logo(slide: dict, brand: dict) -> bool:
    wm = brand.get("watermark", {})
    if not wm.get("solo_se_presente", True):
        return True
    return bool((slide.get("watermark_originale") or "").strip())


def render_slide(original_bytes: bytes, slide: dict, brand: dict, mode: str) -> tuple[Image.Image, bool, str]:
    """Restituisce (slide, ok, problemi). ok=False se dopo tutti i tentativi la slide non è corretta:
    in quel caso NON va consegnata come pronta."""
    original = Image.open(io.BytesIO(original_bytes)).convert("RGB")
    size = output_size(original)
    if not slide.get("da_modificare", True) and not (slide.get("watermark_originale") or "").strip():
        # già in italiano e senza marchi di altre pagine: si consegna l'originale, nessun ritocco
        return original, True, ""
    testo = (slide.get("testo") or "").strip()
    special = any(b.get("carattere") in ("titolo", "stretto") or (b.get("colore_evidenziato") or "").strip()
                  for b in (slide.get("blocchi") or []))
    if config.SLIDE_TEXT_MODE != "gemini-sempre" and not special:
        lay = textslide.analyze(original, slide.get("testo_originale", ""))
        if lay:
            placements: list = []
            img = textslide.render(original, lay, testo, placements)
            slide["_canva"] = {"tipo": "testo", "righe": placements}
            slide["_sfondo"] = textslide.background(original, lay)
            img = add_brand_mark(img, brand) if needs_logo(slide, brand) else img
            if slide.get("_sfondo") is not None and needs_logo(slide, brand):
                slide["_sfondo"] = add_brand_mark(slide["_sfondo"], brand)
            return img, True, ""
    original_jpeg = _jpeg(original)
    orig_fit = _fit(original, size)
    blocks0 = [b for b in (slide.get("blocchi") or []) if (b.get("testo") or "").strip()]
    if mode == "codice" and blocks0:
        local = textslide.try_local_clean(orig_fit, blocks0)
        if local:
            # il testo sta su fasce/zone di colore uniforme: cancellazione precisa col codice, niente Gemini
            clean, boxes = local
            if (slide.get("watermark_originale") or "").strip():
                clean = _remove_watermark(clean, orig_fit, slide) or clean
            placements: list = []
            img = textslide.render_blocks(clean, orig_fit, blocks0, placements, boxes=boxes)
            slide["_canva"] = {"tipo": "testo", "righe": placements}
            slide["_sfondo"] = add_brand_mark(clean, brand) if needs_logo(slide, brand) else clean
            img = add_brand_mark(img, brand) if needs_logo(slide, brand) else img
            return img, True, ""
    feedback, problems = "", ""
    best: Image.Image | None = None
    for attempt in range(max(1, config.SLIDE_ATTEMPTS)):
        try:
            has_blocks = any((b.get("testo") or "").strip() for b in (slide.get("blocchi") or []))
            gmode = "overlay" if (mode == "codice" and has_blocks) else ("gemini" if mode == "codice" else mode)
            edited = _gemini_edit(original, _prompt(slide, gmode, feedback))
        except Exception as e:  # rete, quota, filtri di sicurezza
            log.warning("Gemini tentativo %s fallito: %s", attempt + 1, e)
            problems = "Gemini non ha risposto"
            continue
        if edited is None:
            problems = "Gemini non ha prodotto l'immagine"
            continue
        img = _fit(edited, size)
        blocks = [b for b in (slide.get("blocchi") or []) if (b.get("testo") or "").strip()]
        if mode == "codice" and blocks:
            # Gemini ha solo ripulito: il testo di Claude lo scrive il codice negli stessi riquadri
            clean = img
            placements: list = []
            img = textslide.render_blocks(clean, orig_fit, blocks, placements)
            slide["_canva"] = {"tipo": "testo", "righe": placements}
            slide["_sfondo"] = add_brand_mark(clean, brand) if needs_logo(slide, brand) else clean
        elif mode == "overlay":
            img = draw_text_overlay(img, testo, brand)
        if config.SLIDE_CHECK:
            ok, problems = writer.check_slide(_jpeg(img), testo, slide.get("watermark_originale", ""), original_jpeg)
            if not ok:
                log.info("Slide %s scartata al controllo (tentativo %s): %s", slide.get("numero"), attempt + 1, problems)
                feedback, best = problems, img
                continue
        img = add_brand_mark(img, brand) if needs_logo(slide, brand) else img
        return img, True, ""
    # Nessun tentativo valido: restituiamo l'ultima prova (o l'originale) solo per riferimento, segnata come fallita.
    img = best or _fit(original, size)
    return img, False, problems or "non verificata"


_SEM: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    """Limite unico per tutto il bot: anche con più post in parallelo non si satura la memoria del server."""
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(config.MAX_PARALLEL_IMAGES)
    return _SEM


async def render_all(post: dict, originals: list[bytes], brand: dict, job_dir: Path,
                     only: set[int] | None = None,
                     existing: list[Path] | None = None) -> tuple[list[Path], list[int]]:
    """Genera le slide (solo quelle in `only`, se indicato). Restituisce percorsi e slide non riuscite."""
    job_dir.mkdir(parents=True, exist_ok=True)
    sem = _sem()
    slides = post["slides"]
    paths: list[Path] = [job_dir / f"slide_{i + 1:02d}.jpg" for i in range(len(slides))]
    failed: list[int] = []
    version = len(list(job_dir.glob("*.jpg")))

    async def one(i: int):
        if only is not None and (i + 1) not in only and existing and i < len(existing) and existing[i].exists():
            paths[i] = existing[i]
            return
        src = originals[min(i, len(originals) - 1)]
        async with sem:
            img, ok, problems = await asyncio.to_thread(render_slide, src, slides[i], brand, config.SLIDE_TEXT_MODE)
        if not ok:
            failed.append(i + 1)
            slides[i]["_problema"] = problems
        p = job_dir / f"slide_{i + 1:02d}_v{version}.jpg"
        sfondo = slides[i].pop("_sfondo", None)
        if sfondo is not None:
            bp = job_dir / f"sfondo_{i + 1:02d}_v{version}.jpg"
            await asyncio.to_thread(sfondo.save, bp, "JPEG", quality=95)
            slides[i]["_canva"]["sfondo"] = str(bp)
        if ok and not slides[i].get("da_modificare", True) and not (slides[i].get("watermark_originale") or "").strip():
            p = job_dir / f"slide_{i + 1:02d}_v{version}.jpg"
            p.write_bytes(src)                     # copia esatta dell'originale, nessuna perdita di qualità
        else:
            await asyncio.to_thread(img.save, p, "JPEG", quality=95, subsampling=0)
        paths[i] = p

    await asyncio.gather(*(one(i) for i in range(len(slides))))
    return paths, sorted(failed)
