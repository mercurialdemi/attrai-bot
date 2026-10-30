"""Prova delle storie: serie da un tema e risposta a un box domanda, per ogni pagina (Claude finto)."""
import os, sys
T = sys.argv[1]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = T + "/data"
from pathlib import Path
from bot import pagine, storie, writer


def finto(system, content, tool, **k):
    if tool["name"] == "temi_storie":
        return {"temi": [{"tipo": "didattica", "tema": "t1", "perche": "p"}, {"tipo": "vendita", "tema": "t2", "perche": "p"}]}
    if tool["name"] == "serie_storie":
        assert "STRUTTURE DISPONIBILI" in system and "PUBBLICO" in system
        return {"tipo": "didattica", "struttura": "segnali", "storie": [
            {"titolo": "Tre segnali", "testo": "Il primo è che ti scrive solo di notte. Il sesso non c'entra.", "sticker": "nessuno", "sticker_testo": ""},
            {"titolo": "", "testo": "Ti è successo?", "sticker": "sondaggio", "sticker_testo": "Sì / No"}]}
    return {"domanda": "Come faccio a smettere di pensarla?", "storie": [{"titolo": "Smetti di cercarla", "testo": "Ogni controllo dei suoi social riaccende tutto."}]}


writer._tool_call = finto
for slug in pagine.elenco():
    pagine.imposta(slug)
    b = writer.load_brand()
    temi = storie.proponi_temi(b)
    assert storie.proposta(1)["tema"] == "t1"
    serie, paths, cv = storie.crea(b, slug, "segnali di disinteresse", None, [], Path(T) / slug)
    assert len(paths) == 2 and all(p.exists() for p in paths) and cv and Path(cv).exists(), slug
    assert "ses*o" in serie["storie"][0]["testo"], serie["storie"][0]["testo"]       # censura della pagina
    assert "SONDAGGIO" in storie.istruzioni(serie)
    out, rp, rcv = storie.rispondi(b, slug, [], Path(T) / (slug + "_r"), None, "come smetto di pensarla")
    assert rp and rp[0].exists()
assert storie.capisci_richiesta("marketing gelosia") == ("vendita", "gelosia")
assert storie.capisci_richiesta("gelosia") == (None, "gelosia")
print("storie ok")
