"""Gemini + Pillow: testo italiano sulla slide originale, rimozione watermark, nostro logo."""
from __future__ import annotations

import asyncio
import io
import re
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
        from google.genai import types
        # mai bloccati all'infinito: se Gemini non risponde entro 2 minuti si passa oltre
        _gclient = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=120_000))
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
    """Stessa risoluzione dell'originale: nessun ridimensionamento, nessuna perdita di qualità."""
    return original.size


def _wm_bands(slide: dict, W: int, H: int) -> list[tuple[int, int, int, int]]:
    pos = (slide.get("watermark_originale") or "").lower()
    if not pos.strip():
        return []
    bands = []
    if any(k in pos for k in ("basso", "bottom", "sotto")) or not any(k in pos for k in ("alto", "top", "sopra", "centro", "metà", "mezzo")):
        bands.append((0, int(H * 0.84), W, H))
    if any(k in pos for k in ("alto", "top", "sopra")):
        bands.append((0, 0, W, int(H * 0.14)))
    if any(k in pos for k in ("centro", "metà", "mezzo")):
        bands.append((0, int(H * 0.35), W, int(H * 0.65)))
    return bands


def _composite(original: Image.Image, edited: Image.Image, boxes: list[tuple[int, int, int, int]],
               pad: float = 0.02) -> Image.Image:
    """Dell'immagine rifatta da Gemini si prendono SOLO le zone del testo (e del watermark):
    tutto il resto resta il pixel originale, identico e alla stessa qualità."""
    from PIL import ImageFilter
    W, H = original.size
    edited = edited if edited.size == original.size else _fit(edited, original.size)
    import numpy as np
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    px, py = int(W * pad), int(H * pad)
    for x0, y0, x1, y1 in boxes:
        d.rectangle((max(0, x0 - px), max(0, y0 - py), min(W, x1 + px), min(H, y1 + py)), fill=255)
    # i riquadri di Claude possono essere un po' stretti: si prende da Gemini anche ciò che ha cambiato
    # (lettere cancellate) nei dintorni dei riquadri, così non restano pezzi di parole
    near = Image.new("L", (W, H), 0)
    dn = ImageDraw.Draw(near)
    qx, qy = int(W * 0.12), int(H * 0.08)
    for x0, y0, x1, y1 in boxes:
        dn.rectangle((max(0, x0 - qx), max(0, y0 - qy), min(W, x1 + qx), min(H, y1 + qy)), fill=255)
    diff = np.abs(np.asarray(edited, np.int16) - np.asarray(original, np.int16)).max(axis=2) > 60
    # solo dove nell'originale c'erano lettere (pixel molto diversi dallo sfondo sfocato), non dove Gemini
    # ha "reinventato" la foto: così la foto resta quella vera, nitida
    g = np.asarray(original.convert("L"), np.int16)
    blur = np.asarray(original.convert("L").filter(ImageFilter.BoxBlur(12)), np.int16)
    letters = np.abs(g - blur) > 40
    diff = diff & np.asarray(Image.fromarray((letters * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 0
    changed = Image.fromarray((diff * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))
    changed = Image.fromarray(np.minimum(np.asarray(changed), np.asarray(near)))
    mask = Image.fromarray(np.maximum(np.asarray(mask), np.asarray(changed)))
    mask = mask.filter(ImageFilter.GaussianBlur(max(2, W // 200)))
    return Image.composite(edited, original, mask)


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


def _openai_text_prompt(slide: dict) -> str:
    """Prompt per OpenAI quando scrive lui il testo italiano (come nella Carousel Factory del collaboratore:
    testo esatto tra due marcatori, mai aggiungere o togliere parole), ma partendo dalla slide originale."""
    wm = slide.get("watermark_originale") or "any handle, page name, username or logo of the original page"
    testo = (slide.get("testo") or "").strip().replace("[[", "").replace("]]", "").replace("{{", "").replace("}}", "")
    evid = [b.get("colore_evidenziato") for b in (slide.get("blocchi") or []) if b.get("colore_evidenziato")]
    hl = ""
    if "[[" in (slide.get("testo") or ""):
        parti = re.findall(r"\[\[(.+?)\]\]", slide.get("testo") or "", flags=re.S)
        hl = (" Words highlighted in a different color in the original stay highlighted in the same color"
              + (f" ({evid[0]})" if evid else "") + ": " + "; ".join(" ".join(x.split()) for x in parti) + ".")
    return (
        "Edit this Instagram carousel slide. Produce ONE single finished slide, same format, no grid, no frame, "
        "no Instagram interface.\n"
        "1. Replace ALL the existing text with the Italian text below. Typeset it EXACTLY as written: never add, "
        "translate, omit or rewrite a word; keep accents, punctuation and the line breaks. Use the same font style, "
        "weight, size, color, alignment and position as the original text, inside the same text areas, with the same "
        "margins: it must look like the original designer wrote it in Italian. Nothing may touch or cross the edges. "
        "Accented capitals (È, À, Ù, Ì, Ò) need room: their accents must never touch or overlap the line above; "
        "if needed, add a little more space between lines or make the text slightly smaller. "
        "Speech bubbles: every line, including the first and the last word, stays well inside its bubble with clear "
        "empty space between the letters and the bubble outline on every side (like the original); if the Italian "
        "text is longer, make it slightly smaller instead of letting a word touch or come close to the outline."
        + hl + "\nTEXT_BEGIN\n" + testo + "\nTEXT_END\n"
        f"2. Completely remove the watermark of the original page ({wm}) and rebuild the background underneath.\n"
        "3. Keep everything else IDENTICAL to the original: illustration, characters and their poses, speech bubbles "
        "(shape and position), photo, background texture and grain, colors, composition. Do not add any other text, "
        "logo, handle or watermark. Keep the bottom-right corner clean."
    )


def _prompt(slide: dict, mode: str, feedback: str = "") -> str:
    p = _openai_text_prompt(slide) if mode == "openai-testo" else _base_prompt(slide, mode)
    if feedback:
        p += f"\n\nA previous attempt had these problems, fix them: {feedback}"
    return p


_oclient = None


_ULTIMO_MOTORE = {"nome": ""}


def _motore() -> str:
    if _ULTIMO_MOTORE["nome"]:
        return _ULTIMO_MOTORE["nome"]
    return "OpenAI" if (config.MOTORE_IMMAGINI == "openai" and config.OPENAI_API_KEY) else "Gemini"


def _openai_edit(original: Image.Image, prompt: str) -> Image.Image | None:
    """Stessa cosa di Gemini con il modello immagini di OpenAI: riceve la slide e il prompt, restituisce la slide nuova."""
    global _oclient
    import base64
    from openai import OpenAI
    if _oclient is None:
        _oclient = OpenAI(api_key=config.OPENAI_API_KEY, timeout=180, max_retries=1)
    scale = 1088 / original.width                               # stessa proporzione, lati multipli di 16
    w, h = 1088, max(16, round(original.height * scale / 16) * 16)
    h = min(max(h, w // 3 // 16 * 16 + 16), w * 3)
    buf = io.BytesIO()
    original.convert("RGB").save(buf, "PNG")
    args = dict(model=config.OPENAI_IMAGE_MODEL, image=("slide.png", buf.getvalue(), "image/png"),
                prompt=prompt, quality=config.OPENAI_IMAGE_QUALITY)
    if config.OPENAI_IMAGE_MODEL.startswith("gpt-image-2"):
        args["size"] = f"{w}x{h}"
    else:                                                       # modelli vecchi: tre formati fissi, fedeltà alta
        args["size"] = "1024x1536" if h > w * 1.1 else ("1536x1024" if w > h * 1.1 else "1024x1024")
        args["input_fidelity"] = "high"
    resp = _oclient.images.edit(**args)
    data = resp.data[0].b64_json if resp.data else None
    if not data:
        return None
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")


def _openai_scrive(slide: dict) -> bool:
    """OpenAI scrive direttamente il testo italiano (stile Carousel Factory) quando è il motore scelto."""
    return (config.MOTORE_IMMAGINI == "openai" and bool(config.OPENAI_API_KEY)
            and bool((slide.get("testo") or "").strip()))


def _gemini_edit(original: Image.Image, prompt: str) -> Image.Image | None:
    """Motore immagini scelto (il nome è rimasto per compatibilità). Con "gemini-poi-openai" si prova Gemini e,
    solo se non risponde, non produce l'immagine o il credito è finito, si passa a OpenAI."""
    m = config.MOTORE_IMMAGINI
    if m == "openai" and config.OPENAI_API_KEY:
        _ULTIMO_MOTORE["nome"] = "OpenAI"
        return _openai_edit(original, prompt)
    if m == "gemini-poi-openai" and config.OPENAI_API_KEY:
        try:
            out = _gemini_only(original, prompt)
            if out is not None:
                _ULTIMO_MOTORE["nome"] = "Gemini"
                return out
            log.warning("Gemini non ha prodotto l'immagine: passo a OpenAI")
        except Exception as e:
            log.warning("Gemini non disponibile (%s): passo a OpenAI", str(e)[:150])
        _ULTIMO_MOTORE["nome"] = "OpenAI"
        return _openai_edit(original, prompt)
    _ULTIMO_MOTORE["nome"] = "Gemini"
    return _gemini_only(original, prompt)


def _gemini_only(original: Image.Image, prompt: str) -> Image.Image | None:
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


def _free_spot(img: Image.Image, w: int, h: int, margin: int, preferred: str,
               avoid: list[tuple[int, int, int, int]]) -> tuple[int, int]:
    """Sceglie l'angolo dove il logo non copre scritte: niente sovrapposizione con le zone di testo
    e zona il più possibile 'calma' (senza lettere o dettagli)."""
    import numpy as np
    W, H = img.size
    spots = {
        "basso-destra": (W - margin - w, H - margin - h), "basso-sinistra": (margin, H - margin - h),
        "alto-destra": (W - margin - w, margin), "alto-sinistra": (margin, margin),
        "basso-centro": ((W - w) // 2, H - margin - h), "alto-centro": ((W - w) // 2, margin),
    }
    order = [preferred] + [k for k in spots if k != preferred]
    g = np.asarray(img.convert("L")).astype(np.int16)
    best, best_score = spots.get(preferred, spots["basso-destra"]), None
    for k in order:
        x, y = spots.get(k, spots["basso-destra"])
        r = (x - 12, y - 12, x + w + 12, y + h + 12)
        if any(not (r[2] < b[0] or r[0] > b[2] or r[3] < b[1] or r[1] > b[3]) for b in avoid):
            continue
        z = g[max(0, r[1]):min(H, r[3]), max(0, r[0]):min(W, r[2])]
        if z.size == 0:
            continue
        busy = float(np.abs(np.diff(z, axis=1)).mean() + np.abs(np.diff(z, axis=0)).mean())
        if busy < 6:                       # zona calma: va bene, nell'ordine di preferenza
            return x, y
        if best_score is None or busy < best_score:
            best, best_score = (x, y), busy
    return best


def misura_watermark(original: Image.Image, slide: dict) -> None:
    """Misura quanto è alto il watermark della pagina di origine (logo/scritta), così il nostro marchio
    prende la stessa grandezza: se il loro era piccolo, il nostro è piccolo. Salva l'altezza come frazione
    della larghezza della slide in slide['_wm_alto']."""
    import numpy as np
    W, H = original.size
    wb = textslide.wm_box(slide, W, H)
    if not wb:
        return
    bh = wb[3] - wb[1]
    alto = bh
    try:
        px, py = int(W * 0.01), int(H * 0.01)
        x0, y0, x1, y1 = max(0, wb[0] - px), max(0, wb[1] - py), min(W, wb[2] + px), min(H, wb[3] + py)
        g = np.asarray(original.convert("L").crop((x0, y0, x1, y1))).astype(np.int16)
        bordo = np.concatenate([g[0], g[-1], g[:, 0], g[:, -1]])
        bg = float(np.median(bordo))
        if float(np.abs(bordo - bg).mean()) < 18:          # sfondo abbastanza uniforme: si misurano le lettere vere
            ink = np.abs(g - bg) > 45
            rows = np.where(ink.sum(axis=1) >= max(2, ink.shape[1] // 200))[0]
            if rows.size:
                h_ink = int(rows[-1] - rows[0] + 1)
                if bh * 0.3 <= h_ink <= bh * 1.6:
                    alto = h_ink
    except Exception as e:
        log.debug("Misura watermark non riuscita: %s", e)
    slide["_wm_alto"] = round(alto / W, 4)


def add_brand_mark(img: Image.Image, brand: dict, avoid: list | None = None,
                   at: tuple | None = None, alto: float | None = None) -> Image.Image:
    """Logo circolare + nome della pagina: al posto del watermark della pagina di origine (`at`, il suo riquadro)
    se lì non copre scritte, altrimenti nell'angolo più libero."""
    wm = brand.get("watermark", {})
    W, H = img.size
    d = max(36, int(W * float(wm.get("larghezza_logo", 0.075))))
    if at and alto and wm.get("stessa_grandezza", True):
        # stessa grandezza del watermark che sostituiamo (entro limiti leggibili)
        d = int(min(max(W * alto, W * 0.032, 28), W * 0.11))
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
    x, y = _free_spot(img, total_w, d, margin, pos, avoid or [])
    if at:
        cx, cy = (at[0] + at[2]) / 2, (at[1] + at[3]) / 2
        ax = int(min(max(margin // 2, cx - total_w / 2), W - total_w - margin // 2))
        ay = int(min(max(margin // 2, cy - d / 2), H - d - margin // 2))
        def free(yy):
            r = (ax - 8, yy - 8, ax + total_w + 8, yy + d + 8)
            return not any(not (r[2] < b[0] or r[0] > b[2] or r[3] < b[1] or r[1] > b[3]) for b in (avoid or []))
        # al posto del watermark; se il testo italiano (più lungo) arriva fin lì, appena sotto il testo
        for yy in range(ay, H - d - 6, 4):
            if free(yy):
                x, y = ax, yy
                break

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
    wb = textslide.wm_box(slide, W, H)
    marks = ([wb] if wb else []) + textslide.other_marks(slide, W, H)
    if marks:
        try:
            return textslide.inpaint_boxes(clean, marks)
        except Exception as e:
            log.warning("Watermark non ricostruito col codice: %s", e)
    try:
        edited = _gemini_edit(clean, _prompt({**slide, "testo": ""}, "gemini"))
        if edited is not None:
            return _composite(clean, edited, _wm_bands(slide, W, H) or [(0, int(H * 0.84), W, H)], pad=0.0)
    except Exception as e:
        log.warning("Rimozione watermark con Gemini fallita: %s", e)
    return clean


def needs_logo(slide: dict, brand: dict) -> bool:
    """Il nostro logo va solo sulla prima slide (deciso per tutto il post in render_all)."""
    return bool(slide.get("_metti_logo"))


def _avoid_boxes(slide: dict, W: int, H: int) -> list[tuple[int, int, int, int]]:
    """Zone con testo, dove il logo non deve andare."""
    from . import textslide as ts
    boxes = [ts.block_box(b, W, H) for b in (slide.get("blocchi") or []) if (b.get("testo") or "").strip()]
    for r in (slide.get("_canva") or {}).get("righe", []):
        size = r.get("size", 30)
        boxes.append((int(r["x"]), int(r["y"]), int(r["x"] + size * 0.62 * len(r.get("testo", ""))),
                      int(r["y"] + size * 1.12)))
    return boxes


REBUILD_PROMPT = (
    "Recreate this exact image as a clean version WITHOUT ANY TEXT. Keep everything else identical: same "
    "characters, faces, poses, clothes, objects, background, composition, framing, colors, drawing style and line "
    "quality. Remove every letter, word, caption, number, watermark, handle and logo. Keep empty speech bubbles "
    "and their tails if there are any, but with no text inside. Where text was written directly on the background "
    "(wall, sky), continue the background naturally. Output a single image with the same aspect ratio."
)


def _rebuild_drawing(original, orig_fit, size, slide, blocks0, brand, testo, original_jpeg):
    """Slide disegnata: Gemini la ricostruisce da zero senza scritte, il codice scrive l'italiano nelle caselle
    del testo originale. Restituisce (img, True, "") oppure None se non riesce (si passa al metodo normale)."""
    boxes = textslide.ink_boxes(orig_fit, blocks0)
    for attempt in range(2):
        try:
            gen = _gemini_edit(original, REBUILD_PROMPT)
        except Exception as e:
            log.warning("Ricostruzione disegno: Gemini non ha risposto (%s)", e)
            slide["_motivo_codice"] = f"ricostruzione: Gemini non ha risposto ({str(e)[:100]})"
            return None
        if gen is None:
            continue
        clean = _fit(gen, size)
        placements: list = []
        img = textslide.render_blocks(clean, orig_fit, blocks0, placements, boxes=boxes)
        ok, problems = (True, "")
        if config.SLIDE_CHECK:
            ok, problems = writer.check_slide(_jpeg(img), testo, slide.get("watermark_originale", ""), original_jpeg)
        if ok:
            slide["_canva"] = {"tipo": "testo", "righe": placements}
            slide["_via"] = "disegno-ricostruito"
            slide["_sfondo"] = clean
            if needs_logo(slide, brand):
                slide["_sfondo"] = add_brand_mark(clean, brand, _avoid_boxes(slide, *clean.size), textslide.wm_box(slide, *clean.size), slide.get('_wm_alto'))
                img = add_brand_mark(img, brand, _avoid_boxes(slide, *img.size), textslide.wm_box(slide, *img.size), slide.get('_wm_alto'))
            return img, True, ""
        slide["_motivo_codice"] = f"ricostruzione: {problems}"
        log.info("Slide %s ricostruita ma scartata: %s", slide.get("numero"), problems)
    return None


def render_slide(original_bytes: bytes, slide: dict, brand: dict, mode: str) -> tuple[Image.Image, bool, str]:
    """Restituisce (slide, ok, problemi). ok=False se dopo tutti i tentativi la slide non è corretta:
    in quel caso NON va consegnata come pronta."""
    original = Image.open(io.BytesIO(original_bytes)).convert("RGB")
    size = output_size(original)
    misura_watermark(original, slide)
    if not slide.get("da_modificare", True) and not (slide.get("watermark_originale") or "").strip():
        # già in italiano e senza marchi di altre pagine: si consegna l'originale, nessun ritocco
        slide["_via"] = "originale"
        if needs_logo(slide, brand):
            return add_brand_mark(original, brand, _avoid_boxes(slide, *original.size), textslide.wm_box(slide, *original.size), slide.get('_wm_alto')), True, ""
        return original, True, ""
    testo = (slide.get("testo") or "").strip()
    # serve il percorso a blocchi solo per titoli con font stretti o parole evidenziate davvero ([[ ]])
    special = any(b.get("carattere") in ("titolo", "stretto", "fumetto") or "[[" in (b.get("testo") or "") or "{{" in (b.get("testo") or "")
                  for b in (slide.get("blocchi") or []))
    if config.SLIDE_TEXT_MODE != "gemini-sempre" and not special:
        lay = textslide.analyze(original, slide.get("testo_originale", ""),
                                has_mark=bool((slide.get("watermark_originale") or "").strip()))
        if lay and slide.get("_peso_post"):
            lay.weight = slide["_peso_post"]          # stesso font e spessore in tutte le slide del post
        if lay:
            placements: list = []
            img = textslide.render(original, lay, testo, placements)
            slide["_canva"] = {"tipo": "testo", "righe": placements}
            slide["_via"] = "testo-su-sfondo-uniforme"
            slide["_sfondo"] = textslide.background(original, lay)
            img = add_brand_mark(img, brand, _avoid_boxes(slide, *img.size), textslide.wm_box(slide, *img.size), slide.get('_wm_alto')) if needs_logo(slide, brand) else img
            if slide.get("_sfondo") is not None and needs_logo(slide, brand):
                slide["_sfondo"] = add_brand_mark(slide["_sfondo"], brand, _avoid_boxes(slide, *slide["_sfondo"].size), textslide.wm_box(slide, *slide["_sfondo"].size), slide.get('_wm_alto'))
            return img, True, ""
    original_jpeg = _jpeg(original)
    orig_fit = _fit(original, size)
    blocks0 = [b for b in (slide.get("blocchi") or []) if (b.get("testo") or "").strip()]
    fam = (slide.get("_peso_post") or "").partition("_")[2]
    if fam:
        # il post usa un font riconosciuto (compatto, Montserrat, Poppins): stesso font per copertina e titoli
        for b in blocks0:
            if (b.get("carattere") or "normale") == "normale" and "_" not in (b.get("peso") or ""):
                b["peso"] = (b.get("peso") or "bold") + "_" + fam
    if mode == "codice" and blocks0 and slide.get("sfondo") == "disegno" and config.RICOSTRUISCI_DISEGNI == "gemini":
        rebuilt = _rebuild_drawing(original, orig_fit, size, slide, blocks0, brand, testo, original_jpeg)
        if rebuilt is not None:
            return rebuilt
    # FUMETTI: con OpenAI attivo li rifà direttamente OpenAI (testo che riempie la nuvoletta, contorni intatti);
    # col codice le nuvolette vengono ancora imperfette. Senza OpenAI resta il codice.
    fumetto_ai = (mode == "codice" and _openai_scrive(slide)
                  and (slide.get("sfondo") == "disegno" or any(b.get("carattere") == "fumetto" for b in blocks0)))
    if fumetto_ai:
        slide["_motivo_codice"] = ""
    if mode == "codice" and blocks0 and not fumetto_ai:
        uni = textslide.analyze(orig_fit, slide.get("testo_originale", ""),
                                has_mark=bool((slide.get("watermark_originale") or "").strip()))
        if uni:
            # slide tutta su colore pieno: si cancella TUTTO ciò che non è sfondo (nessun residuo, anche
            # se un blocco è stato misurato male) e si riscrivono i blocchi nelle loro caselle
            clean = textslide.background(orig_fit, uni)
            placements: list = []
            img = textslide.render_blocks(clean, orig_fit, blocks0, placements,
                                          boxes=textslide.boxes_on_plain(orig_fit, uni, blocks0))
            ok, problems = (True, "")
            if config.SLIDE_CHECK:
                ok, problems = writer.check_slide(_jpeg(img), testo, slide.get("watermark_originale", ""),
                                                  original_jpeg)
            if ok:
                slide["_canva"] = {"tipo": "testo", "righe": placements}
                slide["_via"] = "blocchi-sfondo-pieno"
                slide["_sfondo"] = (add_brand_mark(clean, brand, _avoid_boxes(slide, *clean.size), textslide.wm_box(slide, *clean.size), slide.get('_wm_alto'))
                                    if needs_logo(slide, brand) else clean)
                img = add_brand_mark(img, brand, _avoid_boxes(slide, *img.size), textslide.wm_box(slide, *img.size), slide.get('_wm_alto')) if needs_logo(slide, brand) else img
                return img, True, ""
            slide["_motivo_codice"] = problems
        local = textslide.try_local_clean(orig_fit, blocks0)
        if local:
            # il testo sta su fasce/zone di colore uniforme: cancellazione precisa col codice, niente Gemini
            clean, boxes = local
            if (slide.get("watermark_originale") or "").strip():
                clean = _remove_watermark(clean, orig_fit, slide) or clean
            placements: list = []
            img = textslide.render_blocks(clean, orig_fit, blocks0, placements, boxes=boxes)
            slide["_canva"] = {"tipo": "testo", "righe": placements}
            slide["_via"] = "blocchi-pulizia-codice"
            slide["_sfondo"] = add_brand_mark(clean, brand, _avoid_boxes(slide, *clean.size), textslide.wm_box(slide, *clean.size), slide.get('_wm_alto')) if needs_logo(slide, brand) else clean
            ok, problems = (True, "")
            if config.SLIDE_CHECK:
                ok, problems = writer.check_slide(_jpeg(img), testo, slide.get("watermark_originale", ""),
                                                  original_jpeg)
            if ok:
                img = add_brand_mark(img, brand, _avoid_boxes(slide, *img.size), textslide.wm_box(slide, *img.size), slide.get('_wm_alto')) if needs_logo(slide, brand) else img
                return img, True, ""
            log.info("Slide %s pulita col codice ma scartata al controllo: %s", slide.get("numero"), problems)
            slide["_motivo_codice"] = problems
            slide.pop("_canva", None)
            slide.pop("_sfondo", None)
        # testo su foto o sfumature: le lettere si cancellano col codice ricostruendo lo sfondo attorno
        # (l'immagine resta quella originale, niente Gemini: niente rifiuti e nessuna perdita di qualità)
        try:
            W_, H_ = orig_fit.size
            areas = [textslide.block_box(b, W_, H_) for b in blocks0]
            cols = [[c for c in (textslide._hex(b.get("colore")),
                                 textslide._hex(b.get("colore_evidenziato")) if b.get("colore_evidenziato") else None) if c]
                    for b in blocks0]
            wb = textslide.wm_box(slide, W_, H_)
            if wb:
                areas.append(wb)
            areas += textslide.other_marks(slide, W_, H_)
            clean = textslide.inpaint_boxes(orig_fit, areas, colors=cols)
            # caselle misurate PRIMA della sfumatura (dopo, tutta la fascia risulterebbe "cambiata")
            pre = []
            for b_ in blocks0:
                bx, _c = textslide.refine_box(orig_fit, clean, textslide.block_box(b_, W_, H_))
                pt, lh_ = textslide._metrics_from_diff(orig_fit, clean, bx)
                pre.append((bx, pt, lh_))
            clean = textslide.dark_fade(clean, orig_fit, [p_[0] for p_ in pre])
            placements = []
            img = textslide.render_blocks(clean, orig_fit, blocks0, placements, boxes=pre)
            ok, problems = (True, "")
            if config.SLIDE_CHECK:
                ok, problems = writer.check_slide(_jpeg(img), testo, slide.get("watermark_originale", ""),
                                                  original_jpeg)
            if ok:
                slide["_canva"] = {"tipo": "testo", "righe": placements}
                slide["_via"] = "blocchi-ricostruzione-codice"
                slide["_sfondo"] = (add_brand_mark(clean, brand, _avoid_boxes(slide, *clean.size), textslide.wm_box(slide, *clean.size), slide.get('_wm_alto'))
                                    if needs_logo(slide, brand) else clean)
                img = add_brand_mark(img, brand, _avoid_boxes(slide, *img.size), textslide.wm_box(slide, *img.size), slide.get('_wm_alto')) if needs_logo(slide, brand) else img
                return img, True, ""
            log.info("Slide %s ricostruita col codice ma scartata al controllo: %s", slide.get("numero"), problems)
            slide.setdefault("_motivo_codice", problems)
        except Exception as e:
            log.warning("Ricostruzione col codice non riuscita: %s", e)
    feedback, problems = "", ""
    best: Image.Image | None = None
    slide["_via"] = (f"{_motore().lower()}-pulizia+blocchi" if (mode == "codice" and blocks0)
                    else f"{_motore().lower()}-{mode}")
    for attempt in range(max(1, config.SLIDE_ATTEMPTS)):
        slide["_tentativi"] = attempt + 1
        try:
            has_blocks = any((b.get("testo") or "").strip() for b in (slide.get("blocchi") or []))
            # con OpenAI il testo italiano lo scrive direttamente il modello (è molto bravo con il testo);
            # con Gemini, che sbaglia le lettere, Gemini pulisce e il testo lo scrive il codice
            gmode = ("openai-testo" if (_openai_scrive(slide) and mode in ("codice", "gemini"))
                     else "overlay" if (mode == "codice" and has_blocks) else ("gemini" if mode == "codice" else mode))
            edited = _gemini_edit(original, _prompt(slide, gmode, feedback))
        except Exception as e:  # rete, quota, filtri di sicurezza
            log.warning("Gemini tentativo %s fallito: %s", attempt + 1, e)
            problems = f"{_motore()} non ha risposto ({str(e)[:140]})"
            continue
        if edited is None:
            problems = f"{_motore()} non ha prodotto l'immagine"
            continue
        img = _fit(edited, size)
        blocks = [b for b in (slide.get("blocchi") or []) if (b.get("testo") or "").strip()]
        if gmode == "openai-testo":
            slide["_via"] = "openai-testo"
        elif mode == "codice" and blocks:
            W_, H_ = size
            img = _composite(orig_fit, img, [textslide.block_box(b, W_, H_) for b in blocks]
                             + ([textslide.wm_box(slide, W_, H_)] if textslide.wm_box(slide, W_, H_) else [])
                             + textslide.other_marks(slide, W_, H_), pad=0.012)
            # Gemini ha solo ripulito: il testo di Claude lo scrive il codice negli stessi riquadri
            clean = textslide.flatten_ghosts(img, blocks)
            placements: list = []
            img = textslide.render_blocks(clean, orig_fit, blocks, placements)
            slide["_canva"] = {"tipo": "testo", "righe": placements}
            slide["_sfondo"] = add_brand_mark(clean, brand, _avoid_boxes(slide, *clean.size), textslide.wm_box(slide, *clean.size), slide.get('_wm_alto')) if needs_logo(slide, brand) else clean
        elif mode == "overlay":
            img = draw_text_overlay(img, testo, brand)
        if config.SLIDE_CHECK:
            ok, problems = writer.check_slide(_jpeg(img), testo, slide.get("watermark_originale", ""), original_jpeg)
            if not ok:
                log.info("Slide %s scartata al controllo (tentativo %s): %s", slide.get("numero"), attempt + 1, problems)
                feedback, best = problems, img
                continue
        img = add_brand_mark(img, brand, _avoid_boxes(slide, *img.size), textslide.wm_box(slide, *img.size), slide.get('_wm_alto')) if needs_logo(slide, brand) else img
        return img, True, ""
    # Nessun tentativo valido: restituiamo l'ultima prova (o l'originale) solo per riferimento, segnata come fallita.
    img = best or _fit(original, size)
    if slide.get("_motivo_codice"):
        problems = f"{slide['_motivo_codice']} | poi: {problems}" if problems else slide["_motivo_codice"]
    return img, False, problems or "non verificata"


_SEM: asyncio.Semaphore | None = None
_SEM_LOOP = None


def _post_weight(slides: list[dict], originals: list[bytes]) -> str | None:
    from collections import Counter
    votes = Counter()
    for sl, data in zip(slides, originals):
        if not sl.get("da_modificare", True) or any(b.get("carattere") in ("titolo", "stretto")
                                                    for b in (sl.get("blocchi") or [])):
            continue
        try:
            lay = textslide.analyze(Image.open(io.BytesIO(data)).convert("RGB"), sl.get("testo_originale", ""),
                                    has_mark=bool((sl.get("watermark_originale") or "").strip()))
        except Exception:
            lay = None
        if lay:
            votes[lay.weight] += 1
    _free_memory()
    return votes.most_common(1)[0][0] if sum(votes.values()) >= 2 else None


def _free_memory() -> None:
    """Restituisce al sistema la memoria delle immagini già lavorate (il server ha solo 512 MB)."""
    import ctypes
    import gc
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _sem() -> asyncio.Semaphore:
    """Limite unico per tutto il bot: anche con più post in parallelo non si satura la memoria del server."""
    global _SEM, _SEM_LOOP
    loop = asyncio.get_running_loop()
    if _SEM is None or _SEM_LOOP is not loop:
        _SEM, _SEM_LOOP = asyncio.Semaphore(config.MAX_PARALLEL_IMAGES), loop
    return _SEM


async def render_all(post: dict, originals: list[bytes], brand: dict, job_dir: Path,
                     only: set[int] | None = None,
                     existing: list[Path] | None = None, progress=None) -> tuple[list[Path], list[int]]:
    """Genera le slide (solo quelle in `only`, se indicato). Restituisce percorsi e slide non riuscite."""
    job_dir.mkdir(parents=True, exist_ok=True)
    sem = _sem()
    slides = post["slides"]
    paths: list[Path] = [job_dir / f"slide_{i + 1:02d}.jpg" for i in range(len(slides))]
    failed: list[int] = []
    version = len(list(job_dir.glob("*.jpg")))
    # stesso font in tutto il post: si misura il testo di ogni slide di solo testo e si usa quello più frequente
    peso = await asyncio.to_thread(_post_weight, slides, originals)
    if peso:
        post["_peso_testo"] = peso
        for sl in slides:
            sl["_peso_post"] = peso
    # il nostro logo: solo sulla prima slide, e solo se il post è di un'altra pagina (aveva il suo watermark)
    wm_post = any((sl.get("watermark_originale") or "").strip() for sl in slides
                  if not sl.get("_cta_riscritta") and "CTA AttraiSeduci" not in (sl.get("testo") or ""))
    if not brand.get("watermark", {}).get("solo_se_presente", True):
        wm_post = True

    done = [0]
    todo = len(slides) if only is None else len(only)

    async def one(i: int):
        if only is not None and (i + 1) not in only and existing and i < len(existing) and existing[i].exists():
            paths[i] = existing[i]
            return
        src = originals[min(i, len(originals) - 1)]
        slides[i]["_metti_logo"] = (i == 0 and wm_post)
        async with sem:
            img, ok, problems = await asyncio.to_thread(render_slide, src, slides[i], brand, config.SLIDE_TEXT_MODE)
            _free_memory()
        done[0] += 1
        if progress:
            try:
                await progress(done[0], todo)
            except Exception:
                pass
        if not ok:
            failed.append(i + 1)
            slides[i]["_problema"] = problems
        p = job_dir / f"slide_{i + 1:02d}_v{version}.jpg"
        sfondo = slides[i].pop("_sfondo", None)
        if sfondo is not None:
            bp = job_dir / f"sfondo_{i + 1:02d}_v{version}.jpg"
            await asyncio.to_thread(sfondo.save, bp, "JPEG", quality=95)
            slides[i]["_canva"]["sfondo"] = str(bp)
        if (ok and not slides[i].get("da_modificare", True) and not (slides[i].get("watermark_originale") or "").strip()
                and not slides[i].get("_metti_logo")):
            p = job_dir / f"slide_{i + 1:02d}_v{version}.jpg"
            p.write_bytes(src)                     # copia esatta dell'originale, nessuna perdita di qualità
        else:
            await asyncio.to_thread(img.save, p, "JPEG", quality=95, subsampling=0)
        paths[i] = p

    await asyncio.gather(*(one(i) for i in range(len(slides))))
    return paths, sorted(failed)
