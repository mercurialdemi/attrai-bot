"""Copertina stile tweet (avatar + nome sopra, testo bianco sotto): il testo resta bianco anche se la stima dice
oro, non sale dove stava l'intestazione, e il nostro marchio va SOPRA il testo come l'originale."""
import os, sys, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = sys.argv[1]
from PIL import Image, ImageDraw
import numpy as np
from bot import images, textslide, pagine, writer, config
config.SLIDE_CHECK = False
pagine.imposta("attraiseduci")
brand = writer.load_brand()
W, H = 1080, 1350
bg = (17, 24, 39)
im = Image.new("RGB", (W, H), bg); d = ImageDraw.Draw(im)
d.ellipse((90, 380, 230, 520), fill=(150, 60, 40))                     # avatar
d.text((250, 400), "Dominatemanhood_", font=textslide._font("bold", 48), fill=(255, 255, 255))
d.text((250, 465), "@Dominatemanhood_", font=textslide._font("regular", 30), fill=(200, 200, 200))
d.text((130, 740), "Before You Catch Feelings,", font=textslide._font("regular", 52), fill=(255, 255, 255))
d.text((130, 810), "Ask Her This First", font=textslide._font("regular", 52), fill=(255, 255, 255))
buf = io.BytesIO(); im.save(buf, "JPEG", quality=95)
slide = {"numero": 1, "da_modificare": True, "sfondo": "tinta_unita", "_metti_logo": True,
         "testo_originale": "Before You Catch Feelings, Ask Her This First",
         "testo": "Prima di perdere la testa per lei, falle queste domande",
         "watermark_originale": "in alto: avatar e nome Dominatemanhood_",
         "watermark_riquadro": {"x": 80, "y": 275, "w": 420, "h": 120},
         "blocchi": [{"testo": "[[Prima di perdere la testa per lei, falle queste domande]]", "x": 115, "y": 270,
                      "w": 560, "h": 420, "colore": "#D4AF37", "colore_evidenziato": "#D4AF37",
                      "allineamento": "left", "peso": "bold", "maiuscolo": False, "carattere": "normale"}]}
img, ok, prob = images.render_slide(buf.getvalue(), slide, brand, "codice")
img.save(sys.argv[1] + "/tweet.png")
a = np.asarray(img.convert("RGB")).astype(int)
ink = np.abs(a - np.array(bg)).max(axis=2) > 90
# niente oro: i pixel di testo sono bianchi/grigi (canali quasi uguali)
txt = a[650:][ink[650:]]                                        # solo la zona del testo (il logo ha il rosso)
oro = ((txt[:, 0] - txt[:, 2]) > 60).mean()
assert oro < 0.02, f"testo dorato: {oro:.2%}"
rows = np.where(ink[:, int(W*0.35):int(W*0.9)].sum(axis=1) > 3)[0]
testo = rows[rows > 650]
assert 700 <= testo.min() <= 790, f"il testo non è più dov'era: parte da {testo.min()}"   # originale: 740
marchio = np.where(ink[:, 300:600].sum(axis=1) > 3)[0]
assert marchio.min() < 600, "il nostro marchio non è sopra il testo"
print("via:", slide["_via"], "righe con inchiostro da", rows.min(), "a", rows.max(), "→", sys.argv[1] + "/tweet.png")

# copertina vera (6/11, @dominatemanhood_): l'avatar ha un pezzo d'oro sotto il nome, che diventava il colore del titolo
dati = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tweet_avatar_oro.jpg"), "rb").read()
slide = {"numero": 1, "da_modificare": True, "sfondo": "tinta_unita", "_metti_logo": True,
         "testo_originale": "Before You Catch Feelings,\nAsk Her This First",
         "testo": "Prima di perdere la testa per lei, falle queste domande",
         "watermark_originale": "in alto: avatar e nome Dominatemanhood_",
         "watermark_riquadro": {"x": 95, "y": 100, "w": 680, "h": 140},
         "blocchi": [{"testo": "Prima di perdere la testa per lei, falle queste domande", "x": 130, "y": 380, "w": 650,
                      "h": 120, "colore": "#FFFFFF", "colore_evidenziato": "", "allineamento": "left",
                      "peso": "regular", "maiuscolo": False, "carattere": "normale"}]}
img, ok, _ = images.render_slide(dati, slide, brand, "codice")
a = np.asarray(img.convert("RGB")).astype(int)
bg2 = np.array([14, 24, 36])
ink = np.abs(a - bg2).max(axis=2) > 90
txt = a[450:][ink[450:]]
assert ((txt[:, 0] - txt[:, 2]) > 60).mean() < 0.02, "titolo colorato come l'avatar"
r = np.where(ink[450:, 300:900].sum(axis=1) > 3)[0] + 450
assert 520 <= r.min() <= 600, f"il titolo non è dov'era (originale 544): {r.min()}"
print("copertina vera ok:", slide["_via"], "titolo da", r.min())
