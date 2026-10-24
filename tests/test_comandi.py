"""Ogni comando del bot deve comparire nel menu e nella guida /comandi."""
import re
import sys
from pathlib import Path

src = (Path(__file__).resolve().parents[1] / "bot" / "main.py").read_text()
registrati = set()
for m in re.finditer(r'CommandHandler\((\[[^\]]*\]|"[^"]+")', src):
    registrati |= set(re.findall(r'"([^"]+)"', m.group(1)))
registrati -= {"start", "aiuto", "help"}
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.main import COMMANDS, HELP  # noqa: E402
nel_menu = {c for _g, cmds in COMMANDS for c, _s, _l in cmds}
mancanti = registrati - nel_menu
assert not mancanti, f"Comandi senza voce nel menu /comandi: {mancanti}"
assert all(f"/{c}" in HELP for c in nel_menu)
print(f"comandi ok: {len(nel_menu)} nel menu")
