"""Prova delle pagine separate: gruppo -> pagina, dati separati, report solo con i post della pagina."""
import asyncio, os, sys
T = sys.argv[1]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = T + "/data"

from bot import config, instagram, main, pagine, report, storage, writer

# 1. pagine configurate, la principale per prima, con i percorsi di sempre
el = pagine.elenco()
assert el[0] == "attraiseduci" and {"resetrelazionale", "ilpensatore", "amoreinfedele"} <= set(el), el
assert str(config.DATA_DIR) == T + "/data", config.DATA_DIR
assert config.LOGO_PATH.name == "logo.png"

# 2. collegare un gruppo: la pagina segue la chat
pagine.collega(-100123, "resetrelazionale")
assert pagine.di_chat(-100123) == "resetrelazionale" and pagine.di_chat(555) == "attraiseduci"
assert pagine.gruppo_di("resetrelazionale") == -100123


class U:  # update finto
    def __init__(s, cid): s.effective_chat = type("C", (), {"id": cid, "type": "supergroup"})()


async def flusso(cid):
    await main._set_page(U(cid), None)
    nome = writer.load_brand()["nome"]
    # anche nei thread (to_thread) resta la stessa pagina
    nome_thread = await asyncio.to_thread(lambda: writer.load_brand()["nome"])
    jid = storage.save_job(cid, 1, "u", "o", {"descrizione": nome}, [])
    return nome, nome_thread, str(config.DATA_DIR), jid


async def due_insieme():
    return await asyncio.gather(asyncio.create_task(flusso(-100123)), asyncio.create_task(flusso(555)))

r1, r2 = asyncio.run(due_insieme())
assert r1[0] == r1[1] == "ResetRelazionale", r1
assert r2[0] == r2[1] == "AttraiSeduci", r2
assert r1[2].endswith("/pagine/resetrelazionale") and r2[2] == T + "/data", (r1, r2)
print("gruppo Reset ->", r1[0], "| privata ->", r2[0])

# 3. destinatari: Reset va al suo gruppo, le pagine senza gruppo non ricevono niente
assert main._destinatari("resetrelazionale") == [-100123]
assert main._destinatari("ilpensatore") == []

# 4. report: nel gruppo Reset restano solo i post che vanno a Reset
pagine.imposta("resetrelazionale")
b = writer.load_brand()
cands = [instagram.SourcePost(url=f"u{i}", owner="x", caption="c", kind="Sidecar", score=s)
         for i, s in enumerate([5, 3, 2])]
writer._tool_call = lambda *a, **k: {"post": [
    {"numero": 1, "pagina": "AttraiSeduci", "natura": "didattico", "motivo": "test femminili"},
    {"numero": 2, "pagina": "ResetRelazionale", "natura": "virale", "motivo": "rottura"},
    {"numero": 3, "pagina": "nessuna", "natura": "virale", "motivo": "auto"}]}
keep, ok = report._fit_filter(cands, b)
assert [p.url for p in keep] == ["u1"], [p.url for p in keep]
righe = report._link_lines(keep, 1, 1.5)
assert "VIRALE" in righe[0] and "segui la pagina" in righe[0], righe[0]   # Reset ora ha il manuale friendzone
print("report Reset:", righe[0].splitlines()[0])

# 5. riconoscere la pagina da come la scrivi
assert pagine.trova("reset") == "resetrelazionale" and pagine.trova("Il Pensatore") == "ilpensatore"
assert pagine.trova("amore infedele") == "amoreinfedele" and pagine.trova("xyz") is None
print("pagine ok")
