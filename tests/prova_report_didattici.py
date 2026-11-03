"""Report: ogni pagina una volta sola, didattici solo se insegnano davvero, ordinati per quanto insegnano."""
import os, sys
T = sys.argv[1] if len(sys.argv) > 1 else "/tmp/prova_rd"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = T + "/data"
from bot import instagram, pagine, report, writer


def post(i, owner, score, natura="virale", valore=0):
    p = instagram.SourcePost(url=f"u{i}", owner=owner, caption="c", kind="Sidecar", score=score)
    p.natura, p.valore = natura, valore
    return p


# la stessa pagina che esplode sempre non riempie il report
c = [post(1, "big", 9), post(2, "big", 8), post(3, "big", 7), post(4, "a", 2), post(5, "b", 1.5),
     post(6, "c", 1.2, "didattico", 5), post(7, "d", 3, "didattico", 4), post(8, "big", 5, "didattico", 4)]
scelti = report._bilancia(c, 6)
owners = [p.owner for p in scelti]
assert owners.count("big") <= 2 and len(set(owners)) >= 4, owners
did = [p for p in scelti if p.natura == "didattico"]
assert did[0].url == "u6", [p.url for p in did]          # prima quello che insegna di più (5), anche se rende meno
print("report:", [(p.owner, p.natura, getattr(p, "valore", 0)) for p in scelti])

# il filtro chiama didattico solo un post che insegna davvero (valore >= 4)
pagine.imposta("attraiseduci")
writer._tool_call = lambda *a, **k: {"post": [
    {"numero": 1, "dolore": "d", "adatto": True, "pagina_migliore": "AttraiSeduci", "natura": "didattico", "valore_didattico": 2, "motivo": "m"},
    {"numero": 2, "dolore": "d", "adatto": True, "pagina_migliore": "AttraiSeduci", "natura": "didattico", "valore_didattico": 5, "motivo": "m"}]}
cands = [instagram.SourcePost(url=f"x{i}", owner="o", caption="c", kind="Sidecar", score=1) for i in (1, 2)]
keep, ok = report._fit_filter(cands, writer.load_brand())
assert [p.natura for p in keep] == ["virale", "didattico"], [p.natura for p in keep]
print("filtro didattici ok")
