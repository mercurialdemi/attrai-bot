"""CTA illustrata: il manuale deve stare IN MANO al personaggio, non incollato sopra la mano.
Segnalazione di Gabriele sul post 190 (26/11): "il teschio lo tiene con le mani, il manuale non deve essere
incollato sopra". Scena finta: rettangolo verde (la copertina da sostituire) con un pollice e due dita
(color osso) appoggiati sopra i bordi. Dopo aver messo la copertina vera, le dita devono restare davanti."""
import os, sys
T = sys.argv[1]; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.environ["DATA_DIR"] = T + "/data"
import numpy as np
from PIL import Image, ImageDraw
from bot import cta_illustrata

W, H = 1080, 1350
OSSO = (232, 222, 200)
img = Image.new("RGB", (W, H), (120, 150, 190))
d = ImageDraw.Draw(img)
d.rectangle((600, 500, 900, 920), fill=(0, 255, 0))                 # copertina verde
dita = [(560, 640, 640, 690), (860, 560, 940, 605), (860, 780, 940, 825)]   # pollice a sinistra, dita a destra
for b in dita:
    d.rounded_rectangle(b, 18, fill=OSSO)
img.save(f"{T}/scena.png")
quads, mask = cta_illustrata._green_quads(img, 1)
ok = len(quads) == 1
if ok:
    out = cta_illustrata._paste_covers(img, quads, [cta_illustrata.COVERS / "attraiseduci.png"], mask)
    out.save(f"{T}/risultato.jpg")
    a = np.asarray(out).astype(int)
    for x0, y0, x1, y1 in dita:
        # la parte del dito sopra la copertina (dentro il rettangolo verde)
        xs0, xs1 = max(x0, 600) + 6, min(x1, 900) - 6
        zona = a[y0 + 12:y1 - 12, xs0:xs1]
        dist = np.abs(zona - np.array(OSSO)).sum(axis=2)
        vis = (dist < 40).mean()
        print(f"dito {x0},{y0}: visibile {vis:.0%}")
        if vis < 0.8:
            print("ERRORE: il manuale copre le dita (sembra incollato sopra la mano)"); ok = False
    # la copertina c'è davvero (niente verde, niente sfondo vuoto al centro)
    centro = a[650:780, 680:840]
    if (np.abs(centro - np.array((0, 255, 0))).sum(axis=2) < 80).mean() > 0.05:
        print("ERRORE: è rimasto verde sulla copertina"); ok = False
else:
    print("ERRORE: copertina non trovata")
print("OK manuale in mano" if ok else "FALLITA manuale in mano")
sys.exit(0 if ok else 1)
