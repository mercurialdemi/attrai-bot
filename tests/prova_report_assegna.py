"""Un post assegnato a una pagina in pausa, o a una pagina che non segue quel collaboratore, resta nel report."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = sys.argv[1]
from bot import pagine, report, writer, instagram
pagine.imposta("attraiseduci")
b = writer.load_brand()
mk = lambda o, u: instagram.SourcePost(url=u, owner=o, caption="x", kind="Image", image_urls=[], likes=10, comments=1)
posts = [mk("masculinecraze", "u1"), mk("badboyscode", "u2"), mk("masculinemind", "u3")]
for p in posts:
    p.score = 2.0
def finto(system, content, tool, **k):
    assert "Ascesa Mentale" not in system                       # pagina in pausa: non è tra le alternative
    return {"post": [
        {"numero": 1, "dolore": "", "adatto": True, "pagina_migliore": "Ascesa Mentale", "natura": "virale", "valore_didattico": 0, "motivo": ""},
        {"numero": 2, "dolore": "", "adatto": True, "pagina_migliore": "ResetRelazionale", "natura": "virale", "valore_didattico": 0, "motivo": ""},
        {"numero": 3, "dolore": "", "adatto": True, "pagina_migliore": "ResetRelazionale", "natura": "virale", "valore_didattico": 0, "motivo": ""}]}
writer._tool_call = finto
keep, ok = report._fit_filter(posts, b)
got = sorted(p.owner for p in keep)
# badboyscode è seguito anche da Reset: va a Reset. masculinemind no: resta ad AttraiSeduci. Ascesa è in pausa.
assert got == ["masculinecraze", "masculinemind"], got
st = {"min_score": 1.5, "link": 7}
print("assegnazione ok:", got)
