"""Sfondi delle storie aggiunti dal bot: salvati sul disco dati per pagina, categoria scelta da Claude se manca."""
import os, sys, io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = sys.argv[1]
from PIL import Image
from bot import storie, writer
buf = io.BytesIO(); Image.new("RGB", (800, 2400), (90, 60, 50)).save(buf, "JPEG"); foto = buf.getvalue()
writer._tool_call = lambda *a, **k: {"categoria": "distante", "scritte": True}
prima = len(storie.sfondi_disponibili("ilpensatore"))
assert storie.aggiungi_sfondo("ilpensatore", foto) == ("distante", True)            # categoria scelta da Claude
assert storie.aggiungi_sfondo("ilpensatore", foto, "complice") == ("complice", False)  # categoria data a mano
tutte = storie.sfondi_disponibili("ilpensatore")
assert len(tutte) == prima + 2 and all(Image.open(p).height <= 1920 for p in tutte[-2:])
assert "distante 1" in storie.conta_sfondi("ilpensatore") and "complice 1" in storie.conta_sfondi("ilpensatore")
assert len(storie.sfondi_disponibili("attraiseduci")) >= 38                           # quelli del codice restano
print("sfondi ok:", storie.conta_sfondi("ilpensatore"))
