"""Ogni comando del bot deve comparire nel menu e nella guida /comandi."""
import re
import sys
from pathlib import Path

src = (Path(__file__).resolve().parents[1] / "bot" / "main.py").read_text()
registrati = set()
for m in re.finditer(r'CommandHandler\((\[[^\]]*\]|"[^"]+")', src):
    registrati |= set(re.findall(r'"([^"]+)"', m.group(1)))
registrati -= {"start", "aiuto", "help", "storia", "sfondo", "temi", "temi_storie", "aggiungi", "aggiungicollaboratore", "rimuovi", "toglicollaboratore"}   # alias
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.main import COMMANDS, HELP  # noqa: E402
nel_menu = {c for _g, cmds in COMMANDS for c, _s, _l in cmds}
mancanti = registrati - nel_menu
assert not mancanti, f"Comandi senza voce nel menu /comandi: {mancanti}"
assert all(f"/{c}" in HELP for c in nel_menu)
print(f"comandi ok: {len(nel_menu)} nel menu")
# l'aiuto deve arrivare anche se supera il limite di Telegram (4096 caratteri): si manda a pezzi
import asyncio  # noqa: E402
from bot import main as _m  # noqa: E402
class _Msg:
    def __init__(self): self.inviati = []
    async def reply_text(self, t): assert len(t) <= 4096; self.inviati.append(t)
_mm = _Msg()
asyncio.run(_m._manda_lungo(_mm, HELP))
assert "".join(_mm.inviati).replace("\n", "") == HELP.replace("\n", ""), "pezzi persi"
print("aiuto lungo ok:", len(HELP), "caratteri in", len(_mm.inviati), "messaggi")
