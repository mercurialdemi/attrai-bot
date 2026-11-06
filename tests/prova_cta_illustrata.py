"""CTA illustrata (uomo con il manuale): i riquadri di testo non devono coprire il soggetto al centro.
Segnalazione di Gabriele sul post 76 (5/11): "nel cta non coprire tutto il soggetto, sennò rimane un muro di testo".
Testo vero della CTA del post 76, sfondo finto grigio: si misura quanta parte centrale della slide resta libera."""
import os, sys
T = sys.argv[1]; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.environ["DATA_DIR"] = T + "/data"
import numpy as np
from PIL import Image
from bot import cta_illustrata, textslide

TESTO = ("Se ti sei riconosciuto anche in uno solo di questi 6 punti…\nse anche tu hai riempito il suo silenzio con i tuoi messaggi,\n"
         "e vuoi davvero capire perché all'inizio ti cercava e poi è sparita…\ndevi smettere di guardare ciò che dice\n"
         "e iniziare a leggere ciò che succede sotto.\n\n"
         "Ho scritto un manuale che ti spiega i pattern reali\nche regolano attrazione, investimento\ne perdita di valore nelle relazioni,\n"
         "senza filtri e senza illusioni.\n\nSi chiama \"Diventa l'uomo a cui nessuna donna sa resistere\"\ne lo trovi nel link in bio.\n\n"
         "Leggilo se sei stanco di ripetere sempre gli stessi errori\nsenza capire dove stai sbagliando davvero.")
W, H = 1080, 1350
ok = True
for nome, font in (("foto", textslide.FONTS["bold"]), ("fumetto", cta_illustrata.COMIC)):
    bg = Image.new("RGB", (W, H), (110, 110, 110))
    img = cta_illustrata._texts(bg, TESTO, font)
    img.save(f"{T}/cta_{nome}.jpg")
    col = np.asarray(img.convert("L"))[:, W // 2]
    coperto = col > 200                           # riquadri bianchi
    righe_libere = np.where(~coperto)[0]
    # banda libera più lunga (il soggetto sta lì)
    best = cur = 0
    for v in coperto:
        cur = 0 if v else cur + 1
        best = max(best, cur)
    libera = best / H
    print(f"{nome}: parte centrale libera {libera:.0%}")
    if libera < 0.40:
        print(f"ERRORE {nome}: i riquadri coprono il soggetto (libera solo {libera:.0%})"); ok = False
    # nessun testo perso: tutte le parole devono essere state scritte (il riquadro non è vuoto)
    if coperto.sum() < H * 0.15:
        print(f"ERRORE {nome}: testo mancante"); ok = False
print("OK cta illustrata" if ok else "FALLITA cta illustrata")
sys.exit(0 if ok else 1)
