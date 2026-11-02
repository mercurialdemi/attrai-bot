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


def finto(system, content, tool, **k):
    visti[tool["name"]] = system
    if tool["name"] == "temi_storie":
        return {"temi": [{"tipo": "didattica", "tema": "t1", "perche": "p"}, {"tipo": "vendita", "tema": "t2", "perche": "p"}]}
    if tool["name"] == "serie_storie":
        assert "SLIDE 1 (GANCIO)" in system and "PUBBLICO" in system and "REGOLA AUTORI" in system
        if "BOX DOMANDE" in content[0]["text"]:
            return {"tipo": "domande", "sfondo": "primo_piano", "sticker_testo": "Chiedimi tutto sulla gelosia",
                    "slides": [{"blocchi": ["Fammi la domanda che non faresti a **nessuno** sulla gelosia."]}]}
        return {"tipo": "didattica", "sfondo": "sicura", "sticker_testo": "", "slides": ESEMPIO + ESEMPIO}
    return {"domanda": "Come faccio a smettere di pensarla?", "chiusura": "vendita", "sfondo": "distante", "slides": ESEMPIO}


writer._tool_call = finto
for slug in pagine.elenco():
    pagine.imposta(slug)
    b = writer.load_brand()
    storie.proponi_temi(b)
    assert storie.proposta(1)["tema"] == "t1"
    serie, paths, cv = storie.crea(b, slug, "attenzioni maschili", None, [], Path(T) / slug)
    assert len(paths) == 3 and all(p.exists() for p in paths) and cv and Path(cv).exists(), slug   # sempre 3 slide
    if slug == "attraiseduci":
        assert serie["sfondo_file"].startswith("sicura_"), serie["sfondo_file"]                    # foto della categoria
    box, bp, _ = storie.crea(b, slug, "gelosia", "domande", [], Path(T) / (slug + "_box"))
    assert len(bp) == 1 and "Chiedimi tutto" in storie.istruzioni(box)
    out, rp, rcv = storie.rispondi(b, slug, [], Path(T) / (slug + "_r"), None, "come smetto di pensarla")
    assert len(rp) == 3 and rp[0].exists()
    if not b.get("manuali"):
        assert out["chiusura"] == "didattica"                                                     # niente vendita
print("prima storia:", Path(T) / "attraiseduci" / "storia_01.jpg")
assert storie.capisci_richiesta("marketing gelosia") == ("vendita", "gelosia")
assert storie.capisci_richiesta("box gelosia") == ("domande", "gelosia")
print("storie ok")
