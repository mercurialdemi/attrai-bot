"""Post 189 (27/11, Gabriele: "il logo è sovrapposto"): copertina rifatta da OpenAI, che ha alzato il titolo
italiano (più lungo) fin dove stava il watermark "painxfuel". Il nostro marchio non deve finire sopra le lettere."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = sys.argv[1]
import numpy as np
from PIL import Image, ImageDraw
from bot import images, pagine, writer, config
config.SLIDE_CHECK = False
pagine.imposta("attraiseduci"); brand = writer.load_brand()
data = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "copertina_logo_openai.jpg"), "rb").read()
TOP = 0.655                                   # dove OpenAI ha cominciato a scrivere (il watermark stava a ~0.65)


def finto_openai(original, prompt):
    im = original.convert("RGB").copy()
    W, H = im.size
    d = ImageDraw.Draw(im)
    d.rectangle((0, int(H * 0.625), W, H), fill=(8, 8, 8))
    f = images._font(int(H * 0.058))
    y = int(H * TOP)
    for ln in ["GLI UOMINI CHE SOFFRONO DI PIÙ", "NEL MATRIMONIO? QUELLI", "CHE NON HANNO MAI AVUTO",
               "UNA FASE DA PLAYBOY."]:
        w = d.textlength(ln, font=f)
        d.text(((W - w) / 2, y), ln, font=f, fill=(255, 255, 255))
        y += int(H * 0.068)
    finto_openai.ultima = im
    return im


images._openai_scrive = lambda s: True
images._gemini_edit = finto_openai
slide = {"numero": 1, "da_modificare": True, "sfondo": "foto", "_metti_logo": True,
         "testo_originale": "painxfuel\nTHE MEN WHO SUFFER THE MOST IN MARRIAGE... ARE THE ONES WHO NEVER HAD A PLAYBOY PHASE.",
         "testo": "GLI UOMINI CHE SOFFRONO DI PIÙ\nNEL MATRIMONIO? QUELLI\nCHE NON HANNO MAI AVUTO\nUNA FASE DA PLAYBOY.",
         "watermark_originale": "al centro sopra il titolo: painxfuel tra due filetti",
         "watermark_riquadro": {"x": 380, "y": 632, "w": 245, "h": 38},
         "blocchi": [{"testo": "GLI UOMINI CHE SOFFRONO DI PIÙ\nNEL MATRIMONIO? QUELLI\nCHE NON HANNO MAI AVUTO\nUNA FASE DA PLAYBOY.",
                      "x": 45, "y": 715, "w": 735, "h": 195, "colore": "#FFFFFF", "colore_evidenziato": "#E0262B",
                      "allineamento": "center", "maiuscolo": True, "carattere": "titolo"}]}
img, ok, pr = images.render_slide(data, slide, brand, "gemini")    # il codice è stato bocciato: la rifà OpenAI
assert ok, pr
img.save(os.path.join(sys.argv[1], "logo_openai.jpg"))
base = np.asarray(finto_openai.ultima.resize(img.size).convert("L")).astype(int)
fin = np.asarray(img.convert("L")).astype(int)
H, W = fin.shape
diff = np.abs(fin - base) > 40
righe = np.where(diff.sum(axis=1) > 3)[0]
assert righe.size, "il marchio non è stato messo"
testo_da = int(H * TOP)
assert righe.max() < testo_da, f"marchio sopra le lettere (marchio fino a y={righe.max()}, testo da y={testo_da})"
assert righe.max() > H * 0.55, f"marchio finito troppo lontano dal posto del watermark (y={righe.max()})"
print("logo sopra il testo di OpenAI ok:", slide["_via"], int(righe.min()), int(righe.max()), testo_da)
