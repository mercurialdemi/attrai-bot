"""Correzioni: Claude restituisce solo le slide cambiate e il post resta completo. Contatore della spesa."""
import os, sys, types
T = sys.argv[1] if len(sys.argv) > 1 else "/tmp/prova_costi"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = T + "/data"
from bot import costi, writer

post = {"concetto": "c", "senso": "s", "formato": "carosello", "natura": "virale", "descrizione": "Desc vecchia",
        "tipo_hook": "x", "autori_citati": [], "cta": {"tipo": "segui", "manuale": "", "frase": "f", "motivo": "m"},
        "_fallite": [2], "slides": [
            {"numero": 1, "testo": "UNO", "blocchi": [{"testo": "UNO", "y": 10}], "_canva": {"grande": "x" * 500}},
            {"numero": 2, "testo": "DUE", "blocchi": [{"testo": "DUE", "y": 20}]}]}
visto = {}


def finto(system, content, tool, **k):
    visto["tool"], visto["testo"] = tool["name"], content[0]["text"]
    return {"slide_cambiate": [{"numero": 2, "testo": "DUE NUOVA", "blocchi": [{"testo": "DUE NUOVA", "y": 20}]}],
            "descrizione": "", "cta": {"tipo": "segui", "manuale": "", "frase": "f", "motivo": "m"}, "slide_da_rifare": []}


writer._tool_call = finto
new = writer.revise_post(post, "cambia la slide 2", writer.load_brand(), [], {})
assert visto["tool"] == "modifica_post" and "grande" not in visto["testo"]          # niente campi interni a Claude
assert [s["testo"] for s in new["slides"]] == ["UNO", "DUE NUOVA"], new["slides"]
assert new["descrizione"] == "Desc vecchia" and new["natura"] == "virale" and new["slide_da_rifare"] == []
print("correzioni ok: solo la slide 2 cambiata, il resto identico")

u = types.SimpleNamespace(input_tokens=30000, output_tokens=5000, cache_read_input_tokens=0, cache_creation_input_tokens=0)
costi.registra("slide_tradotte", "claude-opus-5-5", u)
costi.registra("esito_controllo", "claude-haiku-5-5", u)
r = costi.riepilogo()
assert "traduzione slide: 0.20" in r and "controllo slide: 0.01" in r, r
print(r)

# tetto: all'80% avvisa una volta, al 100% ferma le chiamate
avvisi = []
costi.avvisa = avvisi.append
costi.imposta_tetto(0.25)
costi.registra("slide_rilette", "claude-opus-5-5", types.SimpleNamespace(input_tokens=0, output_tokens=0,
               cache_read_input_tokens=0, cache_creation_input_tokens=0))
assert any("⚠️" in a for a in avvisi), avvisi
costi.imposta_tetto(0.20)
writer._tool_call = writer.__dict__.get("_tool_call")   # finto: il controllo si prova direttamente
try:
    costi.controlla("slide_tradotte")
    raise SystemExit("il tetto non ha fermato la chiamata")
except costi.TettoSpesa as e:
    assert "/tetto" in str(e)
assert sum("⛔" in a for a in avvisi) == 1
try:
    costi.controlla("slide_tradotte")
except costi.TettoSpesa:
    pass
assert sum("⛔" in a for a in avvisi) == 1          # l'avviso arriva una volta sola al giorno
costi.imposta_tetto(50)
costi.controlla("slide_tradotte")                    # alzato il tetto si riparte
print("tetto ok:", avvisi)
