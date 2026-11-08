"""Prova delle storie (formato del prompt di Gabriele): serie da un tema, box domande e risposta, per ogni pagina."""
import os, sys
T = sys.argv[1]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = T + "/data"
from pathlib import Path
from bot import pagine, storie, writer

ESEMPIO = [
    {"blocchi": ["**Non ne dipendono.** Le usano come **termometro** per capire quanto valgono sul mercato. "
                 "E se sei dentro quel termometro, hai un **problema**."]},
    {"blocchi": ["Il meccanismo è semplice: lei non cerca attenzioni per sentirsi **amata**. Le cerca per sapere "
                 "quanto è ancora **desiderabile**. E questo cambia tutto il modo in cui dovresti vederla.",
                 "Ogni messaggio che riceve, ogni like, ogni sguardo è un **voto** che aggiorna il suo valore percepito. "
                 "Tu, che le mandi il buongiorno pensando di farle piacere, non le stai dando affetto. Le stai dando un **dato**."]},
    {"blocchi": ["Più attenzioni dai, più confermi di essere un **voto facile da ottenere**.",
                 "L'uomo che la fa smettere di guardare il telefono non è quello che le scrive di più. È quello che il suo "
                 "sistema **non riesce a inquadrare**.",
                 "Finché non vedi la tua **categoria**, resterai un **numero** nel suo foglio di calcolo. Ses*o a parte."]},
]
visti = {}
richieste = []


def finto(system, content, tool, **k):
    visti[tool["name"]] = system
    if tool["name"] == "temi_storie":
        return {"temi": [{"tipo": "didattica", "tema": "t1", "perche": "p"}, {"tipo": "vendita", "tema": "t2", "perche": "p"}]}
    if tool["name"] == "serie_storie":
        assert "SLIDE 1 (GANCIO)" in system and "PUBBLICO" in system and "REGOLA AUTORI" in system
        if "BOX DOMANDE" in content[0]["text"]:
            return {"tipo": "domande", "sfondo": "primo_piano", "sticker_testo": "Chiedimi tutto sulla gelosia",
                    "slides": [{"blocchi": ["Fammi la domanda che non faresti a **nessuno** sulla gelosia."]}]}
        richieste.append(content[0]["text"])
        return {"tipo": "didattica", "sfondo": "sicura", "sticker_testo": "", "slides": ESEMPIO + ESEMPIO}
    return {"domanda": "Come faccio a smettere di pensarla?", "chiusura": "vendita", "sfondo": "distante", "slides": ESEMPIO}


writer._tool_call = finto
for slug in pagine.elenco():
    pagine.imposta(slug)
    b = writer.load_brand()
    storie.proponi_temi(b)
    assert storie.proposta(1)["tema"] == "t1"
    serie, paths, cv = storie.crea(b, slug, "attenzioni maschili", None, [], Path(T) / slug)
    assert len(paths) == 4 and all(p.exists() for p in paths) and cv and Path(cv).exists(), slug   # al massimo 4 slide
    assert "STRUTTURA DELLA SERIE" in richieste[-1] and serie["struttura"], slug
    if slug == "attraiseduci":
        assert serie["sfondo_file"].startswith("sicura_"), serie["sfondo_file"]                    # foto della categoria
    box, bp, _ = storie.crea(b, slug, "gelosia", "domande", [], Path(T) / (slug + "_box"))
    assert len(bp) == 1 and "Chiedimi tutto" in storie.istruzioni(box)
    out, rp, rcv = storie.rispondi(b, slug, [], Path(T) / (slug + "_r"), None, "come smetto di pensarla")
    assert len(rp) == 3 and rp[0].exists()
    if not b.get("manuali"):
        assert out["chiusura"] == "didattica"                                                     # niente vendita
# rotazione: 6 serie didattiche/vendita di fila senza ripetere la struttura finché ce ne sono di nuove
pagine.imposta("attraiseduci")
b = writer.load_brand()
usate = []
for k in range(4):
    serie, _, _ = storie.crea(b, "attraiseduci", "gelosia", "vendita", [], Path(T) / f"rot{k}")
    usate.append((serie["struttura"], serie["cta"]))
assert len({u[0] for u in usate}) == 4, usate                       # 4 strutture di vendita, tutte diverse
assert [u[1] for u in usate][:2] in (["citato", "non_citato"], ["non_citato", "citato"]), usate   # CTA alternata
serie, _, _ = storie.crea(b, "attraiseduci", "scqa perché si allontana", None, [], Path(T) / "scqa")
assert serie["struttura"] == "scqa" and serie["tema"] == "perché si allontana", (serie["struttura"], serie["tema"])
assert "SCQA" in richieste[-1]
print("strutture:", usate)
print("prima storia:", Path(T) / "attraiseduci" / "storia_01.jpg")
assert storie.capisci_richiesta("marketing gelosia") == ("vendita", "gelosia")
assert storie.capisci_richiesta("box gelosia") == ("domande", "gelosia")
print("storie ok")

# a capo voluti (\n) rispettati, parola rossa a cavallo di due righe compresa; "\\n" scritto alla lettera ripulito
from PIL import Image, ImageDraw
d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
f = storie._font(storie.FONT_BOLD, 50) if hasattr(storie, "_font") else None
if f is not None:
    r = storie._righe(d, "Risponde quando **vuole**,\ncambia tono,\na volte è **coinvolta\na volte** distante.", f, 900)
    assert len(r) == 4, r
    assert r[3][1][0][1] is True and r[3][2][0][1] is False, r[3]      # "volte" rosso, "distante." no
assert "\n" in storie._pulisci_blocco("Prima frase.\\nSeconda frase.", writer.load_brand())
print("a capo ok")
