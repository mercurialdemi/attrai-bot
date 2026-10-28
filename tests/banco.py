"""Banco di prova con slide VERE dei post: prima di ogni modifica si rifanno tutte e si confrontano con la volta
precedente. Nessuna chiamata a servizi esterni (Claude e Gemini sono spenti: blocchi fissi qui sotto).

Uso:  python3 tests/banco.py CARTELLA_USCITA
Crea un foglio di confronto (originale | risultato) e segnala le slide non riuscite o cambiate rispetto
al riferimento salvato in tests/banco/riferimento/ (crearlo con --salva)."""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import yaml
from PIL import Image

from bot import config, images

config.SLIDE_CHECK = False
images._gemini_edit = lambda *a, **k: None          # niente Gemini: deve funzionare tutto col codice
B = Path(__file__).parent / "banco"
NONE = {"x": 0, "y": 0, "w": 0, "h": 0}

T, F, S = "titolo", "fumetto", "normale"
CASI = {
    "nero_lista.jpg": ("tinta_unita", "THAT'S WHY MORE MEN\nARE CHOOSING:\nTHE GYM.\nTHEIR BUSINESS.\nTHEIR MISSION.\nTHEIR PEACE.", [
        dict(testo="PER QUESTO SEMPRE PIÙ UOMINI\nSCELGONO:", x=100, y=260, w=700, h=110, colore="#E5605A", allineamento="left", maiuscolo=True, carattere=T),
        dict(testo="LA PALESTRA.\nIL LORO BUSINESS.\nLA LORO MISSIONE.\nLA LORO PACE.", x=100, y=370, w=480, h=250, colore="#FFFFFF", allineamento="left", maiuscolo=True, carattere=T)]),
    "nero_titolo.jpg": ("tinta_unita", "THE MODERN BOYFRIEND EXPERIENCE:\nMAXIMUM INVESTMENT.\nUNLIMITED RESPONSIBILITY.\nZERO AUTHORITY.\nREPLACEABLE AT ANY MOMENT.", [
        dict(testo="L'ESPERIENZA DEL FIDANZATO MODERNO:", x=70, y=350, w=830, h=45, colore="#E5605A", allineamento="left", maiuscolo=True, carattere=T),
        dict(testo="INVESTIMENTO MASSIMO.\nRESPONSABILITÀ ILLIMITATA.\nZERO AUTORITÀ.\nSOSTITUIBILE IN QUALSIASI MOMENTO.", x=70, y=400, w=700, h=230, colore="#FFFFFF", allineamento="left", maiuscolo=True, carattere=T)]),
    "nero_elenco.jpg": ("tinta_unita", "YOU STAY LOYAL.\nSHE KEEPS:\n3 EXES.\n7 ORBITERS.\n2 SITUATIONSHIPS.\nA GUY BEST FRIEND.", [
        dict(testo="TU RESTI FEDELE.", x=110, y=225, w=500, h=70, colore="#E5605A", allineamento="left", maiuscolo=True, carattere=T),
        dict(testo="LEI SI TIENE:\n3 EX.\n7 SPASIMANTI.\n2 STORIE A METÀ.\nUN MIGLIORE AMICO.", x=110, y=300, w=620, h=470, colore="#FFFFFF", allineamento="left", maiuscolo=True, carattere=T)]),
    "foto_badge.jpg": ("foto", "A MAN WILL ONLY BE\nFRIENDS WITH A\nWOMAN IF HE LIKES HER.", [
        dict(testo="UN UOMO RESTA AMICO\n[[DI UNA DONNA]]\n[[SOLO SE]] LEI GLI PIACE.", x=40, y=715, w=920, h=235, colore="#FFFFFF", colore_evidenziato="#E5605A", allineamento="center", maiuscolo=True, carattere=T)]),
    "cov2_o.jpg": ("foto", "BEING THE BOYFRIEND\nIN 2026 IS A\nHUMILIATION RITUAL.\nyou stay loyal while she keeps...", [
        dict(testo="FARE [[IL FIDANZATO\nNEL 2026]] È\nUN'UMILIAZIONE.", x=55, y=600, w=890, h=330, colore="#FFFFFF", colore_evidenziato="#E5605A", allineamento="center", maiuscolo=True, carattere=T),
        dict(testo="tu resti fedele, lei intanto si tiene...", x=250, y=905, w=500, h=40, colore="#FFFFFF", allineamento="center", maiuscolo=False, carattere=S, peso="regular")]),
    "cov_o.jpg": ("foto", "MOST PLAYBOYS END UP\nHAPPILY MARRIED.", [
        dict(testo="A SPOSARSI FELICI SONO\nQUASI SEMPRE I PLAYBOY.", x=30, y=665, w=820, h=205, colore="#FFFFFF", allineamento="left", maiuscolo=True, carattere=S, peso="bold")]),
    "wed_o.jpg": ("foto", "NEVER MARRY ANY\nWOMAN UNTIL SHE\nPASSES THESE 5\nGOLDEN TESTS:", [
        dict(testo="[[NON SPOSARE MAI]] UNA DONNA\nPRIMA CHE SUPERI [[QUESTE 5\nPROVE DECISIVE:]]", x=70, y=500, w=870, h=330, colore="#111111", colore_evidenziato="#C0302B", allineamento="center", maiuscolo=True, carattere=S, peso="bold")]),
    "com_o.jpg": ("disegno", "THAT'S IT?\nYOU'RE NOT GOING\nTO ASK WHAT HAPPENED?\nIF NOTHING HAPPENED,\nTHERE'S NOTHING TO ASK.", [
        dict(testo="TUTTO QUI?\nNON MI CHIEDI\nCOS'È SUCCESSO?", x=40, y=45, w=560, h=135, colore="#111111", allineamento="left", maiuscolo=True, carattere=F),
        dict(testo="SE NON È SUCCESSO NIENTE,\nNON C'È NIENTE DA CHIEDERE.", x=585, y=120, w=400, h=70, colore="#111111", allineamento="center", maiuscolo=True, carattere=F)]),
    "fumetto_porta.jpg": ("disegno", "x", [
        dict(testo="QUINDI\nCOSA STAI DICENDO?", x=95, y=70, w=330, h=90, colore="#111111", allineamento="center", maiuscolo=True, carattere=F),
        dict(testo="NON GAREGGIO CON UN UOMO DEL TUO PASSATO\nPER UN POSTO NELLA MIA RELAZIONE.", x=465, y=20, w=500, h=150, colore="#111111", allineamento="center", maiuscolo=True, carattere=F)]),
    "fumetto_cucina.jpg": ("disegno", "x", [
        dict(testo="STASERA HO VISTO\nIL MIO EX.", x=145, y=40, w=420, h=95, colore="#111111", allineamento="center", maiuscolo=True, carattere=F),
        dict(testo="OK.", x=770, y=60, w=150, h=55, colore="#111111", allineamento="center", maiuscolo=True, carattere=F)]),
    "fumetto_palestra.jpg": ("disegno", "YOU MOVED ON\nTHAT FAST?\nNO.\nI JUST STOPPED\nBEGGING TO STAY\nWHERE I WAS\nALREADY REPLACED.", [
        dict(testo="HAI GIÀ\nVOLTATO PAGINA?", x=333, y=31, w=333, h=102, colore="#111111", allineamento="center", maiuscolo=True, carattere=F),
        dict(testo="NO.", x=755, y=602, w=130, h=50, colore="#111111", allineamento="center", maiuscolo=True, carattere=F),
        dict(testo="HO SOLO SMESSO\nDI ELEMOSINARE\nUN POSTO DOVE\nMI AVEVI GIÀ\nSOSTITUITO.", x=667, y=719, w=318, h=180, colore="#111111", allineamento="center", maiuscolo=True, carattere=F)]),
    "lista_poppins.jpg": ("tinta_unita", "1. The contract carries real risk.\n\nA man can love deeply and still sign\nterms he never studied. Marriage can\nbe sacred while ignorance remains\nexpensive. Know the laws before\nmaking the promise.", [
        dict(testo="1. Quel contratto ha rischi veri.", x=60, y=195, w=380, h=25, colore="#DF5D60", allineamento="left", carattere=S, peso="bold"),
        dict(testo="Puoi amarla alla follia e firmare comunque clausole mai lette. Il matrimonio può essere sacro, ma l'ignoranza si paga cara. Studia le leggi prima di fare quella promessa.", x=60, y=240, w=420, h=90, colore="#DBDBDB", allineamento="left", carattere=S, peso="regular")]),
    "cover_parabola.jpg": ("foto", "MODERN WOMEN TIMELINE:\nFROM H*E TO WIFE.", [
        dict(testo="LA PARABOLA DELLA\nDONNA MODERNA:\nDA FACILE A MOGLIE.", x=41, y=777, w=908, h=98, colore="#FFFFFF", allineamento="left", maiuscolo=True, carattere=S, peso="bold")]),
    # post del 27/10 (stile tweet, avatar e nome di un'altra pagina in alto): segnalato "posizione non ben centrata"
    "tweet_02.jpg": ("tinta_unita", 'Not duty s*x.\n\nNot "fine, hurry up"\n\nNot once every three weeks after he practically files an application.\n\nHe wants to feel like.\n\nHIS WOMAN ACTUALLY WANTS HIM.', [
        dict(testo='Non s*sso per dovere.', x=134, y=320, w=254, h=36, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo='Non "va bene, ma sbrigati".', x=134, y=422, w=400, h=40, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo='Non una volta ogni tre settimane, dopo\nuna richiesta quasi in carta bollata.', x=134, y=523, w=714, h=90, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo='Vuole sentire che', x=134, y=676, w=410, h=40, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo='LA SUA DONNA LO DESIDERA\nDAVVERO.', x=134, y=781, w=665, h=82, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular")], "Manly Pinnacle @manly_pinnacle"),
    # post del 27/10 (stile tweet, avatar e nome di un'altra pagina in alto): segnalato "posizione non ben centrata"
    "tweet_03.jpg": ("tinta_unita", "Men can survive stress.\n\nLong hours.\nBills.\nPressure.\nA brutal week.\n\nBut coming home to a woman who looks at him like she still can't keep her hands off him?\n\nDifferent man.", [
        dict(testo='Un uomo regge lo stress.', x=134, y=270, w=459, h=36, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo="Giornate infinite.\nBollette.\nPressione.\nUna settimana d'inferno.", x=134, y=371, w=300, h=188, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo="Ma tornare a casa da una donna che\nlo guarda come se non vedesse l'ora\ndi mettergli le mani addosso?", x=134, y=629, w=698, h=133, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo='È un altro uomo.', x=134, y=832, w=290, h=36, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular")], "Manly Pinnacle @manly_pinnacle"),
    # post del 27/10 (stile tweet, avatar e nome di un'altra pagina in alto): segnalato "posizione non ben centrata"
    "tweet_05.jpg": ("tinta_unita", "You'd be shocked how little most men actually ask for.\n\nPeace. Respect. Loyalty.\n\nAnd a woman who doesn't make him feel like wanting his own wife is an inconvenience.", [
        dict(testo='Non hai idea di quanto poco chieda\ndavvero la maggior parte degli uomini.', x=134, y=395, w=742, h=86, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo='Pace. Rispetto. Lealtà.', x=134, y=547, w=380, h=38, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular"),
        dict(testo='E una moglie che non lo faccia\nsentire un disturbo solo\nperché la desidera.', x=134, y=652, w=730, h=137, colore="#FFFFFF", allineamento="left", carattere=S, peso="regular")], "Manly Pinnacle @manly_pinnacle"),
}


def main(out_dir: str, salva: bool = False):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ref = B / "riferimento"
    brand = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "brands" / "attraiseduci.yaml"))
    tiles, report = [], []
    for name, (sfondo, orig_txt, blocks, *marchio) in CASI.items():
        o = Image.open(B / name).convert("RGB")
        b = io.BytesIO(); o.save(b, "JPEG", quality=95)
        sl = {"numero": 2, "testo": "\n\n".join(x["testo"] for x in blocks), "testo_originale": orig_txt,
              "sfondo": sfondo, "watermark_originale": (marchio or [""])[0], "da_modificare": True, "altri_marchi": [],
              "watermark_riquadro": NONE, "blocchi": [dict(x) for x in blocks]}
        try:
            img, ok, pr = images.render_slide(b.getvalue(), sl, brand, "codice")
        except Exception as e:
            report.append(f"ERRORE {name}: {e}")
            continue
        img.save(out / name)
        stato = "ok" if ok else f"NON RIUSCITA ({pr[:80]})"
        if ref.exists() and (ref / name).exists():
            a = np.asarray(Image.open(ref / name).convert("RGB").resize((270, 338))).astype(int)
            c = np.asarray(img.resize((270, 338))).astype(int)
            diff = float(np.abs(a - c).mean())
            stato += f" · differenza dal riferimento {diff:.1f}" + ("  <-- CAMBIATA" if diff > 4 else "")
        report.append(f"{name}: {sl.get('_via')} · {stato}")
        tiles += [o, img]
        if salva:
            ref.mkdir(exist_ok=True)
            img.save(ref / name)
    W = 360
    sheet = Image.new("RGB", (W * 2, int(W * 1.25) * ((len(tiles) + 1) // 2)), "black")
    for i, t in enumerate(tiles):
        sheet.paste(t.resize((W, int(W * 1.25))), ((i % 2) * W, (i // 2) * int(W * 1.25)))
    sheet.save(out / "confronto.jpg", quality=88)
    print("\n".join(report))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/banco", "--salva" in sys.argv)
