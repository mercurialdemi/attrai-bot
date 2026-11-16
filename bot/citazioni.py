"""Storie-citazione di AttraiSeduci (Gabriele 16/11): una scena illustrata con il NOSTRO personaggio (lo scheletro
del logo) al posto dell'autore, e la citazione in un cartiglio. Solo autori classici e citazioni vere, con l'opera:
quelle inventate o attribuite male sono le prime che vengono contestate nei commenti."""
from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, storie

LOGO = config.ROOT / "assets" / "logo.png"

# testo (con **parole rosse**), autore, opera, scena da illustrare (il personaggio prende il posto dell'autore)
CITAZIONI = [
    ("Desideriamo sempre ciò che è **vietato**, e vogliamo ciò che ci viene **negato**.", "Ovidio", "Amori, III, 4",
     "un poeta nell'antica Roma che legge i suoi versi a una festa serale tra colonne e lucerne, donne eleganti che lo ascoltano rapite"),
    ("È molto più sicuro essere **temuto** che amato, quando si debba rinunciare a una delle due cose.", "Niccolò Machiavelli", "Il Principe, XVII",
     "lo studio di un consigliere nella Firenze del Rinascimento, scrivania con manoscritti e candele, un principe in piedi che lo ascolta"),
    ("Non è che abbiamo **poco tempo**: è che ne **perdiamo molto**.", "Seneca", "La brevità della vita, I",
     "un filosofo romano seduto nel peristilio di una villa al tramonto, una clessidra sul tavolo, un allievo che prende appunti"),
    ("Chi ha un **perché** per vivere sopporta quasi ogni **come**.", "Friedrich Nietzsche", "Crepuscolo degli idoli",
     "un filosofo su un sentiero di montagna in Engadina, cappotto lungo, vento, vallata e lago sotto di lui"),
    ("La **felicità** della tua vita dipende dalla qualità dei tuoi **pensieri**.", "Marco Aurelio", "Pensieri, V, 16",
     "un imperatore romano in una tenda militare di notte, mappe sul tavolo, una lucerna, che scrive su una tavoletta"),
    ("Chi vince se stesso è **più forte** di chi vince mille uomini in battaglia.", "Dhammapada", "versetto 103",
     "un guerriero che depone la spada sui gradini di un antico tempio orientale all'alba, nebbia e montagne"),
]


def _scena(c, img_logo: Image.Image) -> Image.Image | None:
    from .images import _openai_edit
    prompt = ("Crea un'illustrazione verticale in stile fumetto/graphic novel, linee pulite, colori caldi e ombre "
              f"morbide, con questa scena: {c[3]}. Il protagonista della scena è il personaggio dell'immagine di "
              "riferimento: uno scheletro elegante in completo scuro, camicia bianca, cravatta e fiore rossi, cappello "
              "di paglia, con il suo atteggiamento sicuro e un po' beffardo; prende il posto dell'autore nella scena, "
              "vestito come lui ma riconoscibile. Il personaggio è al centro, ben visibile. La parte alta dell'immagine "
              "(circa il 25%) deve essere un fondo semplice, un muro o un cielo, senza figure. Nessuna scritta, nessuna "
              "lettera, nessun logo nell'immagine.")
    return _openai_edit(img_logo.convert("RGB").resize((1024, 1536)), prompt)


def _cartiglio(img: Image.Image, c, rosso) -> Image.Image:
    img = img.convert("RGB").resize((1080, 1920))
    d = ImageDraw.Draw(img)
    f, fa = storie._font(storie.FONT_BOLD, 54), storie._font(storie.FONT_BOLD, 40)
    righe = storie._righe(d, c[0], f, 840)
    h = len(righe) * 68 + 150
    x0, y0, x1 = 90, 200, 990
    d.rectangle((x0 + 8, y0 + 8, x1 + 8, y0 + h + 8), fill=(40, 30, 20))
    d.rectangle((x0, y0, x1, y0 + h), fill=(244, 234, 210), outline=(60, 45, 30), width=5)
    y = y0 + 34
    for r in righe:
        lw = sum(d.textlength(t, font=f) for par in r for t, _ in par) + d.textlength(" ", font=f) * (len(r) - 1)
        x = (1080 - lw) / 2
        for par in r:
            for t, ros in par:
                d.text((x, y), t, font=f, fill=rosso if ros else (35, 28, 22))
                x += d.textlength(t, font=f)
            x += d.textlength(" ", font=f)
        y += 68
    firma = f"— {c[1]}, {c[2]}"
    w = d.textlength(firma, font=fa)
    d.text(((1080 - w) / 2, y + 18), firma, font=fa, fill=(90, 60, 40))
    fs = storie._font(storie.FONT_BOLD, 34)
    w = d.textlength("@attraiseduci", font=fs)
    d.text(((1080 - w) / 2 + 2, 1832), "@attraiseduci", font=fs, fill=(0, 0, 0))
    d.text(((1080 - w) / 2, 1830), "@attraiseduci", font=fs, fill=(255, 255, 255))
    return img


def genera(brand: dict, n: int = 1, uscita: Path | None = None) -> list[Path]:
    """n storie-citazione (una immagine OpenAI ciascuna). Restituisce i file jpg."""
    uscita = uscita or Path(config.DATA_DIR) / "citazioni"
    uscita.mkdir(parents=True, exist_ok=True)
    logo = Image.open(LOGO)
    rosso = storie.colore_chiave(brand)
    out = []
    for c in random.sample(CITAZIONI, min(n, len(CITAZIONI))):
        scena = _scena(c, logo)
        if scena is None:
            continue
        p = uscita / f"citazione_{random.randint(1000, 9999)}.jpg"
        _cartiglio(scena, c, rosso).save(p, quality=93)
        out.append(p)
    return out
