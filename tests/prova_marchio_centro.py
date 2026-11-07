"""Copertina con avatar + filetti + @handle al centro sopra il titolo (7/11, @themasculinecreed): dopo la pulizia col
codice non deve restare nessuna ombra del vecchio handle, anche se il riquadro stimato del marchio è più stretto."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = sys.argv[1]
import numpy as np
from bot import images, pagine, writer, config
config.SLIDE_CHECK = False
images._openai_scrive = lambda s: False
pagine.imposta("attraiseduci"); brand = writer.load_brand()
data = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "copertina_marchio_centro.jpg"), "rb").read()
slide = {"numero": 1, "da_modificare": True, "sfondo": "foto", "_metti_logo": False,
         "testo_originale": "40 SIMP BEHAVIORS\nTHAT MAKE YOU LESS\nATTRACTIVE TO WOMEN",
         "testo": "[[40 COMPORTAMENTI DA SIMP]]\nCHE TI RENDONO\nMENO ATTRAENTE",
         "watermark_originale": "al centro: avatar rotondo con due linee e @themasculinecreed sotto",
         "watermark_riquadro": {"x": 430, "y": 556, "w": 137, "h": 111},
         "blocchi": [{"testo": "[[40 COMPORTAMENTI DA SIMP]]\nCHE TI RENDONO\nMENO ATTRAENTE", "x": 55, "y": 690,
                      "w": 890, "h": 290, "colore": "#FFFFFF", "colore_evidenziato": "#4FB3E8",
                      "allineamento": "center", "peso": "bold", "maiuscolo": True, "carattere": "titolo"}]}
img, ok, _ = images.render_slide(data, slide, brand, "codice")
a = np.asarray(img.convert("L")).astype(int)
H, W = a.shape
fascia = a[int(H * 0.635):int(H * 0.672), int(W * 0.25):int(W * 0.75)]    # dove stava "@themasculinecreed"
assert fascia.max() < 110, f"resta l'ombra del vecchio handle (luminosità massima {fascia.max()})"
print("marchio al centro ok:", slide["_via"], fascia.max())
