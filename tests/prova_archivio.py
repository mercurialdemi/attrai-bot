"""Archivio dei post letti: i giorni dopo il report vede anche i post letti prima (gratis), con i like aggiornati."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = sys.argv[1]
from datetime import datetime, timedelta, timezone
from bot import instagram
ora = datetime.now(timezone.utc)
mk = lambda u, o, l, gg: instagram.SourcePost(url=u, owner=o, caption="c", kind="Image", likes=l,
                                              timestamp=(ora - timedelta(days=gg)).isoformat())
giorno1 = instagram._con_archivio([mk("a", "p1", 10, 3), mk("b", "p2", 5, 3), mk("vecchio", "p1", 1, 20)], ["p1", "p2"])
assert len(giorno1) == 3
giorno2 = instagram._con_archivio([mk("c", "p1", 7, 0), mk("a", "p1", 50, 3)], ["p1"])
urls = {p.url: p.likes for p in giorno2}
assert urls == {"c": 7, "a": 50}, urls          # p2 non richiesta, "vecchio" scaduto, "a" coi like aggiornati
assert {p.url for p in instagram._con_archivio([], ["p1", "p2"])} == {"a", "b", "c"}
print("archivio ok")
