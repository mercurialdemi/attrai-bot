"""Claude: traduce le slide, scrive descrizione e CTA, controlla le slide generate, applica le modifiche.

Tre lavori separati, ognuno con le sue istruzioni:
1. translate_slides  -> traduzione delle slide (fedele o adattata, da profilo brand)
2. write_caption     -> descrizione e CTA nel tono di AttraiSeduci, con la guida di stile ricavata da /analizza
3. check_slide       -> controllo automatico di ogni slide prodotta da Gemini
"""
from __future__ import annotations

import base64
import io
import json
import logging
import random
import re

import anthropic
import yaml
from PIL import Image

from . import config

log = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

HOOK_TYPES = ["frase secca", "confessione in prima persona", "verità brutale",
              "aneddoto di terzi", "domanda retorica"]

_SLIDE_ITEM = {
    "type": "object",
    "properties": {
        "numero": {"type": "integer"},
        "testo_originale": {"type": "string", "description": "Il testo che leggi sulla slide originale (vuoto se non ce n'è)."},
        "testo": {"type": "string", "description": "Il testo italiano che va sulla slide, con gli stessi a capo dell'originale. Vuoto se la slide non ha testo."},
        "watermark_originale": {"type": "string", "description": "Handle, nome o logo di una pagina DIVERSA dalla nostra visibile sulla slide e dove si trova (es. '@pagina in basso a destra'). Vuoto se assente o se è il nostro."},
        "watermark_riquadro": {
            "type": "object",
            "description": "Riquadro stretto attorno al watermark/firma/handle della PAGINA CHE HA PUBBLICATO il post "
                           "(quella indicata come pagina di origine; spesso una firma piccola in basso), in millesimi della "
                           "slide. Lì andrà il nostro logo. Tutti 0 se non c'è.",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "w": {"type": "integer"}, "h": {"type": "integer"}},
            "required": ["x", "y", "w", "h"],
        },
        "altri_marchi": {
            "type": "array",
            "description": "Riquadri (millesimi) di ALTRI watermark/handle di altre pagine sulla slide (es. la fonte della foto), "
                           "da cancellare. Lista vuota se non ce ne sono.",
            "items": {"type": "object",
                      "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "w": {"type": "integer"}, "h": {"type": "integer"}},
                      "required": ["x", "y", "w", "h"]},
        },
        "blocchi": {
            "type": "array",
            "description": "Ogni blocco di testo separato della slide (titolo, paragrafo, didascalia), in ordine di lettura. Vuoto se la slide non ha testo.",
            "items": {
                "type": "object",
                "properties": {
                    "testo": {"type": "string", "description": "Il testo italiano di questo blocco, con gli a capo. Le parole che nell'originale hanno un colore diverso (evidenziate) vanno tra [[ ]], es. 'TRE [[FIGLI MILIONARI.]]'. Le parole in grassetto in mezzo a testo normale vanno tra {{ }}, es. 'non è {{debolezza}}'. Metti i segni sulle parole italiane che corrispondono a quelle evidenziate nell'originale."},
                    "x": {"type": "integer", "description": "Bordo sinistro del testo ORIGINALE, in millesimi della larghezza (0-1000)."},
                    "y": {"type": "integer", "description": "Bordo superiore del testo originale, in millesimi dell'altezza (0-1000)."},
                    "w": {"type": "integer", "description": "Larghezza del riquadro del testo originale, in millesimi della larghezza."},
                    "h": {"type": "integer", "description": "Altezza del riquadro del testo originale, in millesimi dell'altezza."},
                    "colore": {"type": "string", "description": "Colore del testo, es. #FFFFFF."},
                    "allineamento": {"type": "string", "enum": ["left", "center", "right"]},
                    "peso": {"type": "string", "enum": ["regular", "semibold", "bold"]},
                    "maiuscolo": {"type": "boolean", "description": "true se l'originale è tutto in maiuscolo."},
                    "carattere": {"type": "string", "enum": ["normale", "titolo", "stretto", "fumetto"],
                                  "description": "titolo = carattere stretto e molto pesante (tipo Anton, Bebas, Impact); stretto = condensato ma meno pesante (tipo Oswald); fumetto = lettering a mano maiuscolo dei fumetti/vignette; normale = tutti gli altri."},
                    "colore_evidenziato": {"type": "string", "description": "Colore delle parole evidenziate (es. #E5484D). Vuoto se non ce ne sono."},
                },
                "required": ["testo", "x", "y", "w", "h", "colore", "allineamento", "peso", "maiuscolo", "carattere", "colore_evidenziato"],
            },
        },
        "e_cta": {"type": "boolean", "description": "true se la slide è una call to action (segui la pagina, salva, condividi, link in bio, acquista), di qualsiasi pagina."},
        "sfondo": {"type": "string", "enum": ["tinta_unita", "foto", "disegno"],
                   "description": "Che cosa c'è dietro il testo: tinta_unita = colore pieno o sfumatura semplice; foto = fotografia vera; disegno = illustrazione, fumetto, vignetta, grafica disegnata."},
        "da_modificare": {"type": "boolean", "description": "true solo se la slide va cambiata: testo non in italiano, oppure CTA/handle/watermark di un'altra pagina. false se è già in italiano e non ha riferimenti ad altre pagine: in quel caso la slide resta identica all'originale."},
    },
    "required": ["numero", "testo_originale", "testo", "sfondo", "watermark_originale", "watermark_riquadro", "altri_marchi", "blocchi", "e_cta", "da_modificare"],
}

_CTA = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": ["segui", "manuale"]},
        "manuale": {"type": "string", "description": "id del manuale se tipo=manuale, altrimenti vuoto."},
        "frase": {"type": "string", "description": "La CTA esatta usata in fondo alla descrizione."},
        "motivo": {"type": "string", "description": "Perché questa CTA per questo post, in una frase."},
    },
    "required": ["tipo", "manuale", "frase", "motivo"],
}

SLIDES_TOOL = {
    "name": "slide_tradotte",
    "description": "Restituisce la trascrizione e la traduzione italiana di ogni slide.",
    "input_schema": {
        "type": "object",
        "properties": {
            "concetto": {"type": "string", "description": "PRIMA di tradurre: il messaggio di tutto il carosello in 3-5 frasi, come lo spiegheresti a voce a un amico italiano, e il registro (provocatorio, ironico, motivazionale...)."},
            "senso": {"type": "string", "description": "1-2 frasi: di cosa parla il post e perché funziona."},
            "formato": {"type": "string", "enum": ["carosello", "singolo"]},
            "natura": {"type": "string", "enum": ["didattico", "virale"],
                       "description": "didattico = valore reale, problema specifico o dolore forte; virale = volume, poco valore reale."},
            "slides": {"type": "array", "items": _SLIDE_ITEM},
        },
        "required": ["concetto", "senso", "formato", "natura", "slides"],
    },
}

CAPTION_TOOL = {
    "name": "descrizione_pronta",
    "description": "Restituisce descrizione Instagram e CTA.",
    "input_schema": {
        "type": "object",
        "properties": {
            "descrizione": {"type": "string", "description": "Descrizione Instagram in italiano, max 2200 caratteri, che termina con la CTA."},
            "tipo_hook": {"type": "string", "enum": HOOK_TYPES},
            "autori_citati": {"type": "array", "items": {"type": "string"}},
            "cta": _CTA,
            "testo_slide_finale": {
                "type": "string",
                "description": "Testo della NOSTRA slide finale di CTA (c'è in OGNI post), che verrà scritta con lo stesso stile "
                               "grafico delle altre slide. Parti da uno dei TESTI DELLE NOSTRE SLIDE CTA dello stesso tipo della "
                               "CTA scelta (per un manuale, uno di quel manuale; se non ce ne sono, uno 'segui') e ADATTALO al "
                               "post: stessa struttura, stessa leva e lunghezza simile, ma con le parole che lo legano al tema e al "
                               "dolore di questo post. Mai partire dalle CTA delle descrizioni. Righe corte con a capo. Niente emoji. "
                               "Il titolo del manuale sempre tra virgolette \"...\".",
            },
            "slide_cta": {
                "type": "array",
                "description": "Solo se nel post ci sono slide CTA della pagina di origine (indicate nel messaggio): "
                               "per ognuna i nuovi testi dei blocchi, nello stesso ordine e con lunghezza simile. "
                               "Altrimenti lista vuota.",
                "items": {
                    "type": "object",
                    "properties": {
                        "numero": {"type": "integer"},
                        "blocchi": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["numero", "blocchi"],
                },
            },
        },
        "required": ["descrizione", "tipo_hook", "autori_citati", "cta", "testo_slide_finale", "slide_cta"],
    },
}

POST_TOOL = {
    "name": "post_pronto",
    "description": "Restituisce il post completo dopo la modifica.",
    "input_schema": {
        "type": "object",
        "properties": {
            **SLIDES_TOOL["input_schema"]["properties"],
            **{k: v for k, v in CAPTION_TOOL["input_schema"]["properties"].items() if k not in ("slide_cta", "testo_slide_finale")},
            "slide_da_rifare": {
                "type": "array", "items": {"type": "integer"},
                "description": "Numeri delle slide da rigenerare anche se il testo non cambia (es. richiesta grafica). Altrimenti lista vuota.",
            },
        },
        "required": SLIDES_TOOL["input_schema"]["required"]
                    + [k for k in CAPTION_TOOL["input_schema"]["required"] if k not in ("slide_cta", "testo_slide_finale")] + ["slide_da_rifare"],
    },
}

CHECK_TOOL = {
    "name": "esito_controllo",
    "description": "Esito del controllo di una slide generata.",
    "input_schema": {
        "type": "object",
        "properties": {
            "testo_corretto": {"type": "boolean", "description": "Il testo sulla slide corrisponde a quello atteso, senza errori di battitura, lettere sbagliate o parole mancanti. True se non era atteso testo e non ce n'è."},
            "residui": {"type": "boolean", "description": "True se restano testo originale non tradotto, il vecchio watermark o il vecchio handle, oppure pezzi di lettere, parole sovrapposte o scritte che si accavallano."},
            "problemi": {"type": "string", "description": "Cosa c'è di sbagliato, in una o due frasi brevi in italiano semplice. Vuoto se tutto ok."},
        },
        "required": ["testo_corretto", "residui", "problemi"],
    },
}

TRADUZIONE = {
    "fedele": (
        "Fedele al SIGNIFICATO, non alle parole. Prima capisci il concetto di tutto il carosello (campo concetto), "
        "poi riscrivi ogni slide come la scriverebbe da zero un copywriter italiano che vuole dire esattamente "
        "la stessa cosa: stesse idee, stesso ordine, stessa forza, nessuna idea aggiunta o tolta. "
        "Cambia liberamente la costruzione delle frasi, i verbi, gli esempi culturali e i modi di dire perché "
        "suonino naturali in italiano. Vietati i calchi: 'cosa hai intenzione di fare al riguardo' diventa "
        "'e tu cosa fai per risolverlo?'; 'la risposta si riduceva a una sola cosa' diventa 'il segreto? Una cosa sola.'"
    ),
    "adattata": (
        "ADATTATA: stesso significato e stessa forza, ma riscritta nel tono di AttraiSeduci "
        "(maschile, crudo, diretto, mai romantico), mai parola per parola."
    ),
}


# ---------------------------------------------------------------- base

def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=300, max_retries=2)
    return _client


def load_brand() -> dict:
    with open(config.BRAND_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_style() -> dict:
    """Guida di stile ed esempi ricavati da /analizza (vuoto finché non la lanci)."""
    p = config.DATA_DIR / "stile.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _image_block(data: bytes, max_side: int = 1280) -> dict:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def plain(t: str) -> str:
    """Testo senza i segni di colore [[ ]] e grassetto {{ }}."""
    return t.replace("[[", "").replace("]]", "").replace("{{", "").replace("}}", "")


def clean_text(s: str) -> str:
    s = s.replace("**", "").replace("*", "")
    s = re.sub(r"\s*[—–]\s*", ", ", s)
    return s.strip()


def _strict_schema(schema):
    """Per gli strumenti 'strict' ogni oggetto deve dichiarare additionalProperties: false."""
    if isinstance(schema, dict):
        out = {k: _strict_schema(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [_strict_schema(x) for x in schema]
    return schema


def _tool_call(system: str, content: list, tool: dict, model: str | None = None, max_tokens: int = 8000) -> dict:
    """Chiede a Claude di rispondere usando lo strumento indicato.
    I modelli 5.5 non accettano tool_choice forzato: si usa 'auto' con lo strumento 'strict'
    e l'istruzione esplicita di usarlo. Se il modello risponde a parole, si riprova una volta."""
    instruction = {"type": "text", "text": f"Rispondi esclusivamente chiamando lo strumento {tool['name']}."}
    strict_tool = {**tool, "input_schema": _strict_schema(tool["input_schema"]), "strict": True}
    use_strict = True
    for attempt in range(3):
        try:
            resp = client().messages.create(
                model=model or config.CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=system,
                tools=[strict_tool if use_strict else tool],
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": list(content) + [instruction]}],
            )
        except anthropic.BadRequestError as e:
            if use_strict:   # schema non accettato in modalità strict: riprova con lo strumento normale
                log.warning("Strumento strict non accettato, riprovo senza: %s", e)
                use_strict = False
                continue
            raise
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Risposta di Claude troncata: post troppo lungo.")
        for block in resp.content:
            if block.type == "tool_use":
                return dict(block.input)
        log.warning("Claude ha risposto senza usare %s (tentativo %s)", tool["name"], attempt + 1)
    raise RuntimeError("Claude non ha restituito il risultato.")


def _finalize(post: dict) -> dict:
    post["descrizione"] = clean_text(post.get("descrizione", ""))
    if len(post["descrizione"]) > 2200:
        cut = post["descrizione"][:2200]
        post["descrizione"] = cut[: max(cut.rfind("\n"), cut.rfind(". ") + 1, 1800)].strip()
        post["_avviso"] = "Descrizione tagliata a 2200 caratteri: controlla il finale."
    for s in post.get("slides", []):
        s["testo"] = clean_text(s.get("testo", ""))
    post.setdefault("slide_da_rifare", [])
    return post


def _rules_block(rules: list[dict]) -> str:
    return "\n".join(f"- {r['text']}" for r in rules) or "- (nessuna)"


# ---------------------------------------------------------------- 1. slide

def _slides_system(brand: dict, rules: list[dict]) -> str:
    modo = (brand.get("traduzione_slide") or "fedele").lower()
    return f"""Sei il traduttore delle slide per la pagina Instagram italiana {brand['nome']} (@{brand['handle']}).
Ricevi le slide di un post di una pagina collaboratrice che ci ha autorizzato a ripubblicare i suoi contenuti.

- Leggi con attenzione tutto il testo di ogni slide e trascrivilo in testo_originale.
- {TRADUZIONE.get(modo, TRADUZIONE['fedele'])}
- Impaginazione identica all'originale: STESSI A CAPO e stesso numero di righe. Ogni riga italiana deve avere
  una lunghezza simile alla riga inglese corrispondente; se in italiano viene più lunga, riformula la riga in
  modo più asciutto (meno parole, stesso senso) invece di allungarla. Negli elenchi: un elemento per riga, sempre.
- Battute di vignette/fumetti e testi scritti sopra un disegno o una foto: l'italiano deve essere LUNGO QUANTO
  L'INGLESE O PIÙ CORTO (massimo +5% di caratteri), anche a costo di riformulare in modo più asciutto,
  perché lo spazio attorno ai personaggi è limitato ("I ran into my ex tonight." -> "Stasera ho visto il mio ex.").
- Stesso numero di slide e stesso ordine.
- Unica eccezione alla fedeltà: se una slide contiene una CTA o un riferimento alla pagina di origine
  (segui @..., link in bio, nome della pagina), sostituiscilo con il nostro equivalente per {brand['nome']}.
- Niente asterischi, niente trattini lunghi, niente emoji nel testo delle slide (i font delle slide non le hanno).
- In blocchi descrivi ogni blocco di testo separato: testo italiano e riquadro che occupa il testo ORIGINALE
  (in millesimi, stretto attorno alle lettere), colore, allineamento, spessore, maiuscolo. Il testo verrà
  riscritto esattamente lì sopra l'immagine ripulita, quindi le misure devono essere accurate.
  OGNI scritta della slide deve stare in un blocco (titoletti colorati, sottotitoli piccoli, numeri, frasi
  finali): se una scritta resta fuori dai blocchi rimane in inglese sulla slide. Se titoletto e testo hanno
  colori diversi, o se c'è un sottotitolo separato, sono blocchi separati.
  Non includere nei blocchi il watermark o l'handle della pagina. Ogni slide con testo da tradurre deve
  avere i suoi blocchi: un blocco per ogni gruppo di righe separato da uno spazio vuoto.
  PAROLE EVIDENZIATE: guarda con attenzione ogni slide. Se alcune parole hanno un colore diverso dal resto
  (es. rosse in un testo bianco) mettile tra [[ ]] e scrivi il loro colore in colore_evidenziato; se alcune
  parole sono in grassetto in mezzo a testo normale mettile tra {{ }}. Nella traduzione evidenzia le parole
  italiane che hanno lo stesso ruolo. Non perdere mai un'evidenziazione dell'originale.
  colore_evidenziato vuoto solo se non ci sono parole di un altro colore.
- Segnala in watermark_originale handle, nome o logo di un'altra pagina e dove si trova. Se è il nostro (@{brand['handle']}, {brand['nome']}) o non c'è, lascialo vuoto.
- da_modificare = false per le slide già in italiano e senza riferimenti ad altre pagine (comprese le slide con la nostra CTA o il nostro profilo): restano identiche all'originale e testo = testo_originale.

Regole fisse date da Gabriele (valgono anche qui se riguardano le slide):
{_rules_block(rules)}
"""


def translate_slides(caption: str, owner: str, images: list[bytes], brand: dict, rules: list[dict]) -> dict:
    content: list = []
    for i, data in enumerate(images, 1):
        content.append({"type": "text", "text": f"Slide {i}:"})
        content.append(_image_block(data))
    content.append({"type": "text", "text": (
        f"Pagina di origine: @{owner}. Numero di slide: {len(images)}.\n"
        f"Descrizione originale, solo per capire il contesto:\n<<<\n{(caption or '(vuota)')[:2000]}\n>>>"
    )})
    out = _tool_call(_slides_system(brand, rules), content, SLIDES_TOOL, model=config.CAPTION_MODEL, max_tokens=16000)
    try:
        out = polish_slides(out)
    except Exception as e:
        log.warning("Rilettura delle slide non riuscita, tengo la prima versione: %s", e)
    out["slides"] = out.get("slides", [])[:len(images)]
    ours = {brand["handle"].lower(), brand["nome"].lower().replace(" ", "")}
    own_post = (owner or "").lower() in ours
    for s in out["slides"]:
        s["testo"] = plain(clean_text(s.get("testo", "")))
        for b in s.get("blocchi") or []:
            b["testo"] = clean_text(b.get("testo", ""))
        wm = (s.get("watermark_originale") or "").lower().replace(" ", "").replace("@", "")
        if own_post or any(o in wm for o in ours):
            s["watermark_originale"] = ""      # il nostro marchio non va mai sostituito
        if own_post:
            s["da_modificare"] = False         # post della nostra pagina: le slide restano com'erano
        s.setdefault("da_modificare", True)
    return out


POLISH_TOOL = {
    "name": "slide_rilette",
    "description": "Testi delle slide riletti da un madrelingua.",
    "input_schema": {
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "numero": {"type": "integer"},
                        "blocchi": {"type": "array", "items": {"type": "string"},
                                    "description": "Il testo di ogni blocco, nello stesso ordine e numero, con gli stessi [[ ]] e {{ }}."},
                    },
                    "required": ["numero", "blocchi"],
                },
            },
        },
        "required": ["slides"],
    },
}


def polish_slides(out: dict) -> dict:
    """Seconda passata solo testo: un madrelingua rilegge tutte le slide di fila e sistema ciò che suona tradotto."""
    items = []
    for sl in out.get("slides", []):
        bl = [b.get("testo", "") for b in (sl.get("blocchi") or [])]
        if sl.get("da_modificare", True) and bl:
            items.append({"numero": sl["numero"], "originale": sl.get("testo_originale", ""), "blocchi": bl})
    if not items:
        return out
    guida = (load_style().get("guida") or "").strip()
    system = ("Sei il copywriter italiano che scrive i caroselli di AttraiSeduci (pagina di psicologia maschile, "
              "tono maschile, crudo, diretto, adulto). Ricevi un carosello tradotto dall'inglese: il tuo lavoro è "
              "farlo sembrare SCRITTO in italiano, non tradotto. Il lettore non deve mai percepire l'inglese sotto.\n"
              "COME LAVORI\n"
              "- Leggi tutte le slide di fila come un unico discorso: ogni slide deve agganciarsi alla precedente.\n"
              "- Per ogni frase chiediti: un italiano la direbbe così a voce? Se no, riscrivila da zero con la stessa idea.\n"
              "- Frasi corte e piene. Verbi concreti. Niente giri di parole, niente costruzioni passive.\n"
              "- Le chiuse ad effetto devono suonare come una sentenza italiana, non come uno slogan tradotto "
              "('Un gioco spietato.' -> 'E vince sempre lei.'; 'Ignorarlo ti costa caro' -> 'Ignoralo e la paghi').\n"
              "- Ordine delle parole italiano: 'Quasi tutte le donne moderne fanno troppo rumore per capirne il motivo' "
              "-> 'Oggi quasi nessuna lo capisce: è troppo impegnata ad alzare la voce.'\n"
              "- Vietato lo slang giovanile o da strada: 'fra', 'frate', 'bro', 'zio', 'raga', 'tipo' come intercalare.\n"
              "- Vietati calchi e formule da traduzione ('il fatto è che', 'alla fine della giornata', 'fare la differenza', "
              "'in termini di', 'questo significa che'). Parole inglesi solo se un italiano le usa davvero così.\n"
              "VINCOLI\n"
              "- Stesse idee, stesso ordine, nessuna idea aggiunta o tolta.\n"
              "- Mantieni maiuscole, a capo, virgolette, i [[ ]] (parole colorate) e i {{ }} (parole in grassetto). Lunghezza simile "
              "(massimo +10% caratteri), perché il testo deve stare nello stesso spazio.\n"
              "- Se una frase suona già scritta da un italiano, lasciala com'è."
              + (f"\nVOCE DELLA PAGINA (dai post migliori):\n{guida[:2500]}" if guida else ""))
    content = [{"type": "text", "text": (
        f"Concetto del carosello: {out.get('concetto', '')}\n\n"
        f"Slide (JSON):\n{json.dumps(items, ensure_ascii=False, indent=1)}\n\n"
        "Restituisci tutte le slide ricevute con lo strumento slide_rilette.")}]
    res = _tool_call(system, content, POLISH_TOOL, model=config.CAPTION_MODEL, max_tokens=12000)
    by_num = {s["numero"]: s.get("blocchi") or [] for s in res.get("slides", []) if isinstance(s.get("numero"), int)}
    for sl in out.get("slides", []):
        new = by_num.get(sl.get("numero"))
        blocks = sl.get("blocchi") or []
        if new and len(new) == len(blocks):
            for b, t in zip(blocks, new):
                if t and t.strip():
                    b["testo"] = clean_text(t)
            sl["testo"] = "\n\n".join(plain(b["testo"]) for b in blocks)
    return out


# ---------------------------------------------------------------- 2. descrizione e CTA

def _feedback_memory(style: dict) -> str:
    fb = style.get("feedback_bot") or {}
    if not fb.get("pubblicati_dal_bot") or fb["pubblicati_dal_bot"] < 3:
        return ""
    def fmt(d):
        return "; ".join(f"{k} {v['rendimento']}x ({v['post']} post)" for k, v in d.items())
    return ("\nRISULTATI DEI POST PREPARATI DA TE E GIÀ PUBBLICATI (rendimento rispetto alla media della pagina):\n"
            f"- per tipo di hook: {fmt(fb.get('per_hook', {}))}\n"
            f"- per CTA: {fmt(fb.get('per_cta', {}))}\n"
            "Preferisci gli hook e le CTA che rendono di più, ma continua a variare: non usare due volte di fila la stessa struttura.\n")


def _cta_memory(style: dict) -> str:
    """CTA che hanno reso di più sulla nostra pagina (da /analizza): circa metà delle volte se ne riusa
    una adattata al post, l'altra metà se ne scrive una nuova costruita sulle stesse leve."""
    best = style.get("cta_migliori") or []
    if not best and not style.get("testi_slide_cta"):
        return ""
    def info(c):
        extra = (f", {c['clic_ogni_1000']} clic sul link e {c['follow_ogni_1000']} follow ogni 1000 persone raggiunte"
                 if "clic_ogni_1000" in c else "")
        return f"- ({c['rendimento']}x la media{extra}, {c['tipo']}{' ' + c['manuale'] if c.get('manuale') else ''}) {c['testo']}"
    lines = "\n".join(info(c) for c in best[:8])
    worst = "\n".join(f"- {c['testo']}" for c in (style.get("cta_peggiori") or [])[:4])
    if random.random() < 0.5:
        mode = ("In questo post RIUSA una delle CTA migliori qui sotto (quella più adatta al tema e al tipo di CTA "
                "richiesto), adattandola al dolore del post: stessa struttura e stessa leva, parole aggiornate.")
    else:
        mode = ("In questo post scrivi una CTA NUOVA, costruita sulle stesse leve delle CTA migliori qui sotto, "
                "senza copiarle.")
    slide_txt = "\n".join(f"- ({t['tipo']}{' ' + t['manuale'] if t.get('manuale') else ''}) {' / '.join(t['testo'].splitlines())}"
                          for t in (style.get("testi_slide_cta") or [])[:15])
    return ((f"\nTESTI DELLE NOSTRE SLIDE CTA (dai nostri caroselli):\n{slide_txt}\n" if slide_txt else "")
            + f"\nCTA CHE HANNO RESO DI PIÙ SULLA NOSTRA PAGINA:\n{lines}\n"
            + (f"Lezioni dallo studio: {style.get('lezioni_cta')}\n" if style.get("lezioni_cta") else "")
            + (f"CTA da NON imitare (post che hanno reso poco):\n{worst}\n" if worst else "")
            + mode)


def _caption_system(brand: dict, rules: list[dict], recent: dict) -> str:
    manuali = "\n".join(
        f"- id: {m['id']} | {m['nome']} | dolori che risolve: {' '.join(str(m.get('dolori', '')).split())}"
        + (f" | COME SI VENDE: {' '.join(str(m['vendita']).split())}" if m.get("vendita") else "")
        for m in brand.get("manuali", [])
    )
    ultimi_hook = ", ".join(recent.get("hook", [])[:3]) or "nessuno"
    ultimi_autori = ", ".join(recent.get("autori", [])[:15]) or "nessuno"
    style = load_style()
    guida = style.get("guida", "").strip()
    esempi = style.get("esempi") or []
    esempi_txt = ""
    if esempi:
        scelti = random.sample(esempi, min(3, len(esempi)))
        esempi_txt = ("\nESEMPI DI DESCRIZIONI CHE HANNO FUNZIONATO SULLA NOSTRA PAGINA "
                      "(imita la voce, non copiare frasi né struttura):\n"
                      + "\n\n".join(f"<<<\n{e}\n>>>" for e in scelti) + "\n")
    return f"""Sei il copywriter senior della pagina Instagram italiana {brand['nome']} (@{brand['handle']}).
Ricevi le slide già tradotte di un post e scrivi la descrizione Instagram con la CTA.

POSIZIONAMENTO
{brand.get('posizionamento', '').strip()}

TONO
{chr(10).join('- ' + t for t in brand.get('tono', []))}

SOSTANZA (la descrizione deve insegnare, non intrattenere)
- Pragmatica: spiega il MECCANISMO psicologico dietro il contenuto del post (perché succede, cosa scatta nella
  testa di lei o di lui) e dai indicazioni concrete: cosa fare, cosa smettere di fare, cosa dire, con un esempio reale.
- Autorevole: parla da chi conosce la materia da anni. Affermazioni nette e motivate, niente "forse", niente
  frasi motivazionali vuote ("credi in te stesso", "sii la versione migliore di te").
- Didattica: struttura chiara. Hook che apre il problema, spiegazione del meccanismo, 2-4 punti pratici
  (ognuno una frase d'azione + il perché), chiusura che fissa il concetto, poi la CTA.
- Ogni frase deve aggiungere informazione. Se una frase si può togliere senza perdere niente, toglila.
- Basi scientifiche: quando il tema lo permette, porta almeno 2 riferimenti reali e verificabili (studio,
  ricercatore, università o anno) e spiega in parole semplici cosa hanno scoperto e perché conta per il lettore.
  Solo riferimenti che conosci con certezza: meglio uno vero che due inventati. Mai inventare numeri.
- Chiusura prima della CTA: niente domande generiche ("Sei d'accordo?", "Cosa ne pensi?"). Se fai una domanda
  deve essere introspettiva e specifica sul tema del post.

FLUIDITÀ (la descrizione deve sembrare scritta di getto da un italiano, non tradotta)
- Frasi brevi e parlate, ritmo da messaggio vocale: una idea per frase, molti a capo.
- Vietati i calchi dall'inglese e le formule da traduzione: "il fatto è che", "questo significa che",
  "è importante notare", "alla fine della giornata", "fare la differenza", "in termini di", "nel momento in cui".
- Niente elenchi di concetti astratti in fila: meglio un esempio concreto, una scena, una frase che punge.
- Usa il tu diretto, il presente, parole semplici. Se una frase suona da libro, riscrivila come la diresti a voce.
- Prima di consegnare rileggi tutto come se lo leggessi ad alta voce e sistema ogni punto che inciampa.

FORMATO
{chr(10).join('- ' + t for t in brand.get('regole_formato', []))}
{('GUIDA DI STILE RICAVATA DAI NOSTRI POST MIGLIORI' + chr(10) + guida + chr(10)) if guida else ''}{esempi_txt}
HOOK E STRUTTURA
{brand['hook']['regola'].strip()}
{brand['hook']['struttura'].strip()}
Ultimi tipi di hook usati (dal più recente): {ultimi_hook}. Non usare lo stesso del più recente.
Autori citati di recente, da non ripetere: {ultimi_autori}.
Cita solo autori e studi reali che conosci con certezza; se non sei sicuro, non citare.

CTA
{brand['cta']['regola'].strip()}
{brand['cta']['stile'].strip()}
{_cta_memory(style)}
{_feedback_memory(style)}
Dove si compra: {brand['cta'].get('dove_si_compra', 'link in bio')}.
Manuali disponibili:
{manuali}

REGOLE FISSE DATE DA GABRIELE (hanno la precedenza su tutto il resto)
{_rules_block(rules)}
"""



def _source_cta_note(slides_out: dict) -> str:
    """Se il post originale chiude vendendo un prodotto (guide, libri, corso), anche il nostro chiude con il manuale."""
    cta_txt = " | ".join((s.get("testo_originale") or "")[:300] for s in slides_out.get("slides", []) if s.get("e_cta"))
    if not cta_txt.strip():
        return ""
    return ("\n\nIL POST ORIGINALE CHIUDE CON QUESTA CTA: " + cta_txt + "\n"
            "Se quella CTA vende o regala un prodotto (guida, libro, ebook, corso, 'commenta X e te lo mando'), "
            "la nostra CTA DEVE essere al manuale più adatto al dolore del post (tipo=manuale), mai 'segui la pagina'.")


def _cta_slides_request(slides_out: dict) -> str:
    cta = [s for s in slides_out.get("slides", []) if s.get("e_cta") and s.get("da_modificare", True)
           and any((b.get("testo") or "").strip() for b in (s.get("blocchi") or []))]
    if not cta:
        return "\nNel post non ci sono slide CTA della pagina di origine: slide_cta = []."
    parts = []
    for s in cta:
        bl = [b for b in s["blocchi"] if (b.get("testo") or "").strip()]
        parts.append(f"Slide {s['numero']}:\n" + "\n".join(
            f"  blocco {i}: ({len(b['testo'])} caratteri) {b['testo']}" for i, b in enumerate(bl, 1)))
    return ("\n\nSLIDE CTA DELLA PAGINA DI ORIGINE DA RISCRIVERE (in slide_cta): sono la pubblicità della pagina di "
            "origine o di un suo prodotto. Riscrivi ogni blocco come la NOSTRA CTA, coerente con la CTA scelta "
            "(segui la pagina oppure il manuale giusto, chiamato col suo nome esatto), con lunghezza simile "
            "(massimo +15% caratteri) e stesso ruolo del blocco (titolo, testo, chiusura). Mai nomi, prodotti o "
            "promesse della pagina di origine. Usa [[parola]] per le parole colorate e {{parola}} per quelle in grassetto se nel blocco originale "
            "c'erano.\n" + "\n".join(parts))

def write_caption(slides_out: dict, caption: str, brand: dict, rules: list[dict], recent: dict) -> dict:
    testo_slide = "\n\n".join(f"Slide {s['numero']}:\n{s['testo']}" for s in slides_out["slides"] if s.get("testo"))
    content = [{"type": "text", "text": (
        f"Formato: {slides_out.get('formato')} | Natura: {slides_out.get('natura')}\n"
        f"Di cosa parla: {slides_out.get('senso')}\n\n"
        f"Testo delle slide in italiano:\n{testo_slide or '(nessun testo)'}\n\n"
        f"Descrizione originale, solo come contesto, da NON tradurre:\n<<<\n{(caption or '(vuota)')[:2000]}\n>>>\n\n"
        "Scrivi la nostra descrizione con lo strumento descrizione_pronta, seguendo le nostre regole, "
        "e sostituisci la CTA originale con la nostra."
        + _cta_slides_request(slides_out)
        + _source_cta_note(slides_out)
    )}]
    system = _caption_system(brand, rules, recent)
    try:
        return _tool_call(system, content, CAPTION_TOOL, model=config.CAPTION_MODEL, max_tokens=6000)
    except (anthropic.NotFoundError, anthropic.PermissionDeniedError) as e:
        log.warning("Modello %s non disponibile, uso %s: %s", config.CAPTION_MODEL, config.CLAUDE_MODEL, e)
        return _tool_call(system, content, CAPTION_TOOL, max_tokens=6000)


def apply_cta_texts(slides_out: dict, new: list[dict]) -> None:
    """Mette nelle slide CTA della pagina di origine i testi della nostra CTA."""
    by_num = {s.get("numero"): s for s in slides_out.get("slides", [])}
    for item in new:
        s = by_num.get(item.get("numero"))
        if not s:
            continue
        bl = [b for b in (s.get("blocchi") or []) if (b.get("testo") or "").strip()]
        texts = [clean_text(t) for t in item.get("blocchi") or []]
        if len(texts) != len(bl):
            continue
        for b, t in zip(bl, texts):
            b["testo"] = t
        s["testo"] = "\n\n".join(plain(t) for t in texts)
        s["_cta_riscritta"] = True


def quote_titles(text: str, brand: dict | None = None) -> str:
    """Mette tra virgolette i titoli dei manuali quando non lo sono già."""
    if not text:
        return text
    brand = brand or load_brand()
    titles = sorted({t for m in brand.get("manuali", []) for t in (m.get("titoli") or [])}, key=len, reverse=True)
    for t in titles:
        pat = re.compile(r'(?<!["“«@\w])(' + re.escape(t) + r')(?!["”»\w])', re.IGNORECASE)
        # niente virgolette se il titolo è già dentro un titolo più lungo tra virgolette
        def rep(m):
            before = text[:m.start()]
            if before.count('"') % 2 == 1 or before.count("“") > before.count("”"):
                return m.group(1)
            return f'"{m.group(1)}"'
        text = pat.sub(rep, text)
    return text


def pick_cta_slide_text(proposed: str, cta: dict) -> str:
    """Il testo della slide CTA finale è COPIATO IDENTICO da una delle nostre slide CTA (lette da /analizza,
    in ordine di rendimento): una delle migliori del manuale scelto, altrimenti una 'segui la pagina'.
    Solo se /analizza non ha ancora trovato nessuna nostra slide CTA si usa il testo proposto da Claude."""
    texts = load_style().get("testi_slide_cta") or []
    if not texts:
        return proposed
    if cta.get("tipo") == "manuale":
        same = [t for t in texts if t["tipo"] == "manuale" and t.get("manuale") == cta.get("manuale")]
        if same:
            return random.choice(same[:3])["testo"]          # tra le 3 migliori, per non ripetersi sempre
    follow = [t for t in texts if t["tipo"] == "segui"]
    if follow:
        return random.choice(follow[:3])["testo"]
    return proposed


def create_post(caption: str, owner: str, images: list[bytes], brand: dict,
                rules: list[dict], recent: dict) -> dict:
    slides_out = translate_slides(caption, owner, images, brand, rules)
    cap = write_caption(slides_out, caption, brand, rules, recent)
    apply_cta_texts(slides_out, cap.pop("slide_cta", None) or [])
    slides_out["_testo_cta_finale"] = quote_titles(pick_cta_slide_text(clean_text(cap.pop("testo_slide_finale", "") or ""),
                                                                       cap.get("cta") or {}), brand)
    if cap.get("descrizione"):
        cap["descrizione"] = quote_titles(cap["descrizione"], brand)
    post = _finalize({**slides_out, **cap})
    post["slide_da_rifare"] = []
    return post


# ---------------------------------------------------------------- 3. controllo slide

def check_slide(image_jpeg: bytes, expected_text: str, old_watermark: str,
                original_jpeg: bytes | None = None) -> tuple[bool, str]:
    """Controlla una slide generata (confrontandola con l'originale). Restituisce (ok, problemi)."""
    attese = (f'Testo atteso sulla slide, esattamente:\n"""\n{expected_text}\n"""' if expected_text.strip()
              else "Sulla slide non deve esserci nessun testo, a parte eventualmente il nostro logo AttraiSeduci.")
    content = []
    if original_jpeg:
        content += [{"type": "text", "text": "Slide ORIGINALE:"}, _image_block(original_jpeg, 1024)]
    content += [
        {"type": "text", "text": "Slide NUOVA da controllare:"},
        _image_block(image_jpeg, 1024),
        {"type": "text", "text": (
            f"{attese}\n\nVecchio watermark da eliminare: {old_watermark or 'nessuno'}.\n"
            + ("Confronta con l'originale: illustrazioni, sfondo e posizione dei blocchi di testo devono essere gli stessi. "
               "Se il testo ora copre un'illustrazione o un elemento che nell'originale era libero, oppure manca o è "
               "deformato un elemento grafico, metti testo_corretto=false e descrivilo in problemi.\n" if original_jpeg else "")
            +
            "Guarda con attenzione ogni riga: parole scritte una sopra l'altra, pezzi di lettere rimasti (es. 'ıl.', "
            "'about.'), testo che esce dalla slide o tocca il bordo sono errori gravi.\n"
            "Il logo circolare con la scritta AttraiSeduci, se presente, è corretto e non è un residuo.\n"
            "Controlla la slide con lo strumento esito_controllo. Ignora differenze di a capo e di maiuscole."
        )},
    ]
    try:
        out = _tool_call("Sei un correttore di bozze pignolo per slide Instagram in italiano.", content,
                         CHECK_TOOL, model=config.CHECK_MODEL, max_tokens=2000)
    except Exception as e:  # se il controllo non riesce, la slide NON si considera verificata
        log.warning("Controllo slide non riuscito: %s", e)
        # il controllo non ha dato un giudizio (errore di rete o crediti): non si scarta la slide per questo
        return True, f"controllo non eseguito ({str(e)[:120]})"
    ok = bool(out.get("testo_corretto")) and not out.get("residui")
    return ok, out.get("problemi", "")


# ---------------------------------------------------------------- modifiche

def revise_post(post: dict, instruction: str, brand: dict, rules: list[dict], recent: dict) -> dict:
    current = {k: v for k, v in post.items() if not k.startswith("_")}
    system = (_caption_system(brand, rules, recent)
              + "\nTRADUZIONE DELLE SLIDE\n" + TRADUZIONE.get((brand.get("traduzione_slide") or "fedele").lower(), TRADUZIONE["fedele"]))
    content = [{"type": "text", "text": (
        "Questo è il post attuale in JSON:\n"
        f"{json.dumps(current, ensure_ascii=False, indent=1)}\n\n"
        f"Richiesta di modifica di Gabriele:\n<<<\n{instruction}\n>>>\n\n"
        "Applica SOLO la modifica richiesta e restituisci il post completo con lo strumento post_pronto. "
        "Lascia identico tutto quello che la richiesta non tocca (stesse slide, stessi testi). "
        "Se la richiesta riguarda la grafica di slide specifiche senza cambiare il testo, mettile in slide_da_rifare."
    )}]
    return _finalize(_tool_call(system, content, POST_TOOL))
