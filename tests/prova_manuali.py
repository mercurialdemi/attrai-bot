"""Catalogo manuali: in arrivo = conosciuti ma non in vendita; /manuali attiva li mette tra i manuali della pagina."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = sys.argv[1]
from bot import pagine, writer
pagine.imposta("ilpensatore")
cat = writer.catalogo_manuali()
assert set(cat) == {"dipendenza-emotiva", "lasciare-andare"}, cat
assert not writer.load_brand().get("manuali")                       # in arrivo: non in vendita
writer.attiva_manuale("lasciare-andare", True)
ids = [m["id"] for m in writer.load_brand()["manuali"]]
assert ids == ["lasciare-andare"], ids
pagine.imposta("resetrelazionale")
assert "lasciare-andare" not in [m["id"] for m in writer.load_brand()["manuali"]]   # resta della sua pagina
assert "perche-lei-ti-ha-lasciato" in writer.catalogo_manuali()
pagine.imposta("ilpensatore")
writer.attiva_manuale("lasciare-andare", False)
assert not writer.load_brand().get("manuali")
print("manuali ok")
