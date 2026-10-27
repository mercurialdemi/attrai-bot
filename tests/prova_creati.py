"""Post creati da zero: virgolette nei titoli stretti, maiuscole a inizio riga nel serif, CTA spezzata da una virgola.
(Casi dalle segnalazioni del 26/10 sui post 33 e 34.)"""
import os, sys
T = sys.argv[1]; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.environ["DATA_DIR"] = T + "/data"
os.makedirs(T, exist_ok=True)
from bot import writer, crea

brand = writer.load_brand()
ok = True

# 1. « » nel carattere stretto sembrano << >>: diventano “ ”
assert crea._virgolette("LEI: «STASERA C'È IL [[MIO EX.]]»") == "LEI: “STASERA C'È IL [[MIO EX.]]”"

# 2. serif: un a capo a metà frase non mette la maiuscola
s = crea._sentence("SE QUESTA STORIA TI SUONA FAMILIARE,\nSE ANCHE TU HAI MOLLATO PEZZI DELLA TUA VITA\nPER STARE CON LEI,\nNON SEI FINITO QUI PER CASO.")
assert s == "Se questa storia ti suona familiare,\nse anche tu hai mollato pezzi della tua vita\nper stare con lei,\nnon sei finito qui per caso.", s
assert crea._sentence("NON SE N'È [[ANDATA.]] HA SOLO SMESSO") == "Non se n'è [[andata.]] Ha solo smesso"

# 3. le slide si disegnano (copertina con virgolette, CTA con virgola in fondo al titolo)
cov = "LEI: «STASERA C'È ANCHE IL [[MIO EX.]]» LUI: «OK. [[DIVERTITI.]]»\n\nDa lì in poi, a non chiudere occhio è stata lei."
for i, (txt, cover, cta, look) in enumerate([
        (cov, True, False, {"palette": "nero", "stile": "impatto", "copertina": "grande"}),
        ("Se ti sei rivisto in questa scena, dalla parte di chi aspettava sveglio,\n\nsegui @attraiseduci.", False, True,
         {"palette": "nero", "stile": "impatto"}),
        ("Se questa storia ti suona familiare,\nse anche tu hai mollato pezzi della tua vita\nper stare con lei,\n"
         "non sei finito qui per caso.\n\nHai trovato questa pagina per un motivo.", False, True,
         {"palette": "nero", "stile": "autorevole"})], 1):
    pl = []
    img = crea.render_slide(txt, cover=cover, brand=brand, logo=cover, placements=pl, cta=cta, look=look)
    img.save(f"{T}/creata_{i}.jpg")
    testi = " ".join(r.get("testo", "") for r in pl if isinstance(r, dict))
    if "«" in testi and look["stile"] == "impatto" and cover:
        ok = False; print("ERRORE: « ancora nel titolo", testi)
print("prova_creati:", "OK" if ok else "ERRORE")
sys.exit(0 if ok else 1)
