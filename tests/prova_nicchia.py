"""Report: una pagina sempre virale (media altissima) entra se il post è molto sopra la nicchia."""
import os, sys
from datetime import datetime, timedelta, timezone
T = sys.argv[1] if len(sys.argv) > 1 else "/tmp/prova_nicchia"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = T + "/data"
from bot import instagram, report

ora = datetime.now(timezone.utc)
def post(i, owner, likes, ore):
    return instagram.SourcePost(url=f"{owner}{i}", owner=owner, caption="c", kind="Sidecar", image_urls=["x"],
                                likes=likes, comments=0, timestamp=(ora - timedelta(hours=ore)).isoformat())
posts = []
for o in ("a", "b", "c", "d"):                          # pagine normali: ~100 like
    posts += [post(i, o, 100, 30 + i) for i in range(6)]
posts += [post(i, "craze", 3000, 30 + i) for i in range(6)]   # sempre virale: ~3000 like, sempre uguale
ris = report.rank_candidates(posts, 72, 50)
craze = [p for p in ris if p.owner == "craze"]
assert craze and craze[0].score >= 2, [(p.owner, round(p.score, 2), round(p.score_nicchia, 1)) for p in ris[:6]]
print("pagina sempre virale:", round(craze[0].score, 1), "x (nicchia", round(craze[0].score_nicchia, 1), "x)")
