"""File modificabile per Canva (PowerPoint): Canva lo importa mantenendo i testi modificabili.

- Slide di solo testo: sfondo pulito come immagine + ogni riga come casella di testo (font Inter, stessa
  posizione, dimensione e colore). In Canva si clicca sul testo e si modifica.
- Slide con grafica (rifatte da Gemini) o rimaste uguali all'originale: l'immagine finale.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Emu, Pt

PX = 9525          # 1 pixel a 96 dpi in EMU
FONT_NAMES = {"regular": "Inter", "semibold": "Inter SemiBold", "bold": "Inter", "titolo": "Anton", "stretto": "Oswald"}


def build(post: dict, paths: list[Path], out_path: Path, failed: list[int] | None = None) -> Path:
    from PIL import Image

    failed = set(failed or [])
    first = Image.open(paths[0])
    W, H = first.size
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(W * PX), Emu(H * PX)
    blank = prs.slide_layouts[6]

    for i, p in enumerate(paths, 1):
        if i in failed:
            continue
        slide_info = post["slides"][i - 1] if i - 1 < len(post.get("slides", [])) else {}
        canva = slide_info.get("_canva") or {}
        s = prs.slides.add_slide(blank)
        if canva.get("tipo") == "testo" and canva.get("sfondo") and Path(canva["sfondo"]).exists():
            s.shapes.add_picture(canva["sfondo"], 0, 0, Emu(W * PX), Emu(H * PX))
            for r in canva.get("righe", []):
                size = r["size"]
                box = s.shapes.add_textbox(Emu(int(r["x"] * PX)), Emu(int(r["y"] * PX)),
                                           Emu(int((W - r["x"]) * PX)), Emu(int(size * 1.3 * PX)))
                tf = box.text_frame
                tf.word_wrap = False
                tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
                tf.auto_size = MSO_AUTO_SIZE.NONE
                tf.paragraphs[0].alignment = PP_ALIGN.LEFT
                runs = r.get("runs") or [[r["testo"], r["color"]]]
                for k, item in enumerate(runs):
                    word, col = item[0], item[1]
                    strong = bool(item[2]) if len(item) > 2 else False
                    run = tf.paragraphs[0].add_run()
                    run.text = word + (" " if k < len(runs) - 1 else "")
                    f = run.font
                    f.name = FONT_NAMES.get(r["weight"], "Inter")
                    f.size = Pt(size * 0.75)          # pixel -> punti
                    f.bold = r["weight"] == "bold" or strong
                    f.color.rgb = RGBColor(*list(col)[:3])
        else:
            s.shapes.add_picture(str(p), 0, 0, Emu(W * PX), Emu(H * PX))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out_path)
    return out_path
