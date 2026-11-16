"""Storie-citazione di AttraiSeduci (Gabriele 16/11): una scena illustrata con il NOSTRO personaggio (lo scheletro
del logo) al posto dell'autore, e la citazione in un cartiglio. Solo autori classici e citazioni vere, con l'opera:
quelle inventate o attribuite male sono le prime che vengono contestate nei commenti."""
from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw

import logging

from . import config, storie

log = logging.getLogger(__name__)

LOGO = config.ROOT / "assets" / "logo.png"

# testo (con **parole rosse**), autore, opera, scena da illustrare (il personaggio prende il posto dell'autore)
CITAZIONI = [
    # le 5 scelte da Gabriele il 16/11
    ("Amare ed essere **saggi** è concesso a stento a un **dio**.", "Publilio Siro", "Sentenze",
     "un attore e poeta nel teatro dell'antica Roma, dietro le quinte a lume di torcia, che osserva sul palco una coppia di amanti che si dispera",
     ""),
    ("Forse **tremate più voi** nel pronunciare questa sentenza che io nell'ascoltarla.", "Giordano Bruno", "ai giudici, Roma 1600",
     "un'aula del tribunale dell'Inquisizione nel 1600, giudici in tonaca seduti in alto e visibilmente tesi, l'imputato in piedi al centro, calmo e fiero",
     "Giordano_Bruno"),
    ("L'amore, come esiste nella società, non è che lo scambio di **due fantasie** e il contatto di **due epidermidi**.",
     "Chamfort", "Massime e pensieri",
     "un salotto parigino del Settecento, dame e gentiluomini che flirtano con ventagli e parrucche, un uomo che li osserva in disparte con un sorriso ironico",
     "Nicolas_Chamfort"),
    ("L'assenza diminuisce le passioni **mediocri** e accresce le **grandi**, come il vento spegne le candele e attizza il fuoco.",
     "François de La Rochefoucauld", "Massime, 276",
     "un uomo che sale su una carrozza di notte, una donna alla finestra di un palazzo seicentesco lo guarda partire, il vento spegne una candela sul davanzale",
     "François_de_La_Rochefoucauld"),
    ("Nelle prime passioni le donne amano **l'amante**; nelle altre amano **l'amore**.", "François de La Rochefoucauld", "Massime, 471",
     "un ballo in un palazzo francese del Seicento, una dama che danza con lo sguardo perso, mentre un gentiluomo la osserva consapevole",
     "François_de_La_Rochefoucauld"),
    ("Desideriamo sempre ciò che è **vietato**, e vogliamo ciò che ci viene **negato**.", "Ovidio", "Amori, III, 4",
     "un poeta nell'antica Roma che legge i suoi versi a una festa serale tra colonne e lucerne, donne eleganti che lo ascoltano rapite", "Publio_Ovidio_Nasone"),
    ("È molto più sicuro essere **temuto** che amato, quando si debba rinunciare a una delle due cose.", "Niccolò Machiavelli", "Il Principe, XVII",
     "lo studio di un consigliere nella Firenze del Rinascimento, scrivania con manoscritti e candele, un principe in piedi che lo ascolta", "Niccolò_Machiavelli"),
    ("Non è che abbiamo **poco tempo**: è che ne **perdiamo molto**.", "Seneca", "La brevità della vita, I",
     "un filosofo romano seduto nel peristilio di una villa al tramonto, una clessidra sul tavolo, un allievo che prende appunti", "Lucio_Anneo_Seneca"),
    ("Chi ha un **perché** per vivere sopporta quasi ogni **come**.", "Friedrich Nietzsche", "Crepuscolo degli idoli",
     "un filosofo su un sentiero di montagna in Engadina, cappotto lungo, vento, vallata e lago sotto di lui", "Friedrich_Nietzsche"),
    ("La **felicità** della tua vita dipende dalla qualità dei tuoi **pensieri**.", "Marco Aurelio", "Pensieri, V, 16",
     "un imperatore romano in una tenda militare di notte, mappe sul tavolo, una lucerna, che scrive su una tavoletta", "Marco_Aurelio"),
    ("Chi vince se stesso è **più forte** di chi vince mille uomini in battaglia.", "Dhammapada", "versetto 103",
     "un guerriero che depone la spada sui gradini di un antico tempio orientale all'alba, nebbia e montagne", ""),
]


def ritratto(pagina: str) -> Image.Image | None:
    """Il ritratto storico dell'autore (dipinto, busto o foto d'epoca, di pubblico dominio) dall'immagine
    principale della sua pagina Wikipedia."""
    if not pagina:
        return None
    import httpx
    try:
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": "AttraiBot/1.0 (citazioni; contatto via Telegram)"}) as h:
            info = h.get(f"https://it.wikipedia.org/api/rest_v1/page/summary/{pagina}").json()
            url = (info.get("originalimage") or info.get("thumbnail") or {}).get("source")
            if not url:
                return None
            return Image.open(io.BytesIO(h.get(url).content)).convert("RGB")
    except Exception as e:
        log.warning("Ritratto di %s non trovato: %s", pagina, e)
        return None


def _volto_nostro(ritr: Image.Image, img_logo: Image.Image) -> Image.Image | None:
    """OpenAI: stesso ritratto, stesso stile e vestiti, ma con la testa del nostro scheletro."""
    import base64
    from openai import OpenAI
    from . import costi
    def png(im, nome):
        b = io.BytesIO(); im.convert("RGB").save(b, "PNG"); return (nome, b.getvalue(), "image/png")
    w, h = ritr.size
    size = "1024x1536" if h > w * 1.1 else ("1536x1024" if w > h * 1.1 else "1024x1024")
    prompt = ("Prima immagine: un ritratto storico. Seconda immagine: il nostro personaggio, uno scheletro elegante "
              "con cappello di paglia. Rifai la PRIMA immagine identica (stessa composizione, stessi vestiti, stessa "
              "posa, stesso sfondo, stesso stile pittorico o fotografico, stessi colori) ma sostituisci la testa e il "
              "volto della persona con il teschio del personaggio della seconda immagine, con il suo cappello di "
              "paglia e la sua espressione sicura e beffarda, dipinto nello stesso stile del ritratto. Nessuna scritta.")
    costi.controlla("immagini_openai")
    cl = OpenAI(api_key=config.OPENAI_API_KEY, timeout=180, max_retries=1)
    resp = cl.images.edit(model=config.OPENAI_IMAGE_MODEL, image=[png(ritr, "ritratto.png"), png(img_logo, "personaggio.png")],
                          prompt=prompt, quality=config.OPENAI_IMAGE_QUALITY, size=size)
    costi.registra_openai(getattr(resp, "usage", None), config.OPENAI_IMAGE_QUALITY)
    data = resp.data[0].b64_json if resp.data else None
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB") if data else None


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


def _impagina_ritratto(img: Image.Image) -> Image.Image:
    """Il ritratto occupa i 2/3 alti della storia; sotto, fondo scuro per il cartiglio."""
    from PIL import ImageFilter
    W, H = 1080, 1920
    fondo = img.convert("RGB").resize((W, H)).filter(ImageFilter.GaussianBlur(40))
    fondo = Image.blend(fondo, Image.new("RGB", (W, H), (0, 0, 0)), 0.6)
    r = min(W / img.width, 1240 / img.height)
    im = img.convert("RGB").resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
    fondo.paste(im, ((W - im.width) // 2, 60))
    return fondo


def _cartiglio(img: Image.Image, c, rosso, y0: int = 200) -> Image.Image:
    img = img.convert("RGB").resize((1080, 1920))
    d = ImageDraw.Draw(img)
    f, fa = storie._font(storie.FONT_BOLD, 54), storie._font(storie.FONT_BOLD, 40)
    righe = storie._righe(d, c[0], f, 840)
    h = len(righe) * 68 + 150
    x0, x1 = 90, 990
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
    rosso = (222, 0, 20)      # rosso acceso sul cartiglio chiaro (Gabriele 16/11)
    out = []
    scelte = CITAZIONI[:5] if n == 5 else random.sample(CITAZIONI, min(n, len(CITAZIONI)))   # /citazione 5 = le 5 scelte
    for c in scelte:
        ritr = ritratto(c[4])
        if ritr is not None:                     # Gabriele 16/11: il ritratto vero dell'autore con il nostro volto
            img = _volto_nostro(ritr, logo)
            img = _cartiglio(_impagina_ritratto(img), c, rosso, y0=1360) if img is not None else None
        else:                                    # niente ritratto: scena illustrata
            sc = _scena(c, logo)
            img = _cartiglio(sc, c, rosso) if sc is not None else None
        if img is None:
            continue
        p = uscita / f"citazione_{random.randint(1000, 9999)}.jpg"
        img.save(p, quality=93)
        out.append(p)
    return out
