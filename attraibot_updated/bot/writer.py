"""Claude: legge le slide, traduce e adatta in italiano, scrive descrizione e CTA, applica le modifiche."""
from __future__ import annotations

import base64
import io
import json
import logging
import re

import anthropic
import yaml
from PIL import Image

from . import config

log = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

HOOK_TYPES = ["frase secca", "confessione in prima persona", "verità brutale",
              "aneddoto di terzi", "domanda retorica"]

POST_TOOL = {
    "name": "post_pronto",
    "description": "Restituisce il post italiano finito, pronto da impaginare e pubblicare.",
    "input_schema": {
        "type": "object",
        "properties": {
            "senso": {"type": "string",
                      "description": "1-2 frasi: di cosa parla il post e perché funziona (dolore, meccanismo, hook)."},
            "formato": {"type": "string", "enum": ["carosello", "singolo"]},
            "natura": {"type": "string", "enum": ["didattico", "virale"],
                       "description": "didattico = valore reale, problema specifico o dolore forte; virale = volume, poco valore reale."},
            "slides": {
                "type": "array",
                "description": "Una voce per ogni slide originale, nello stesso ordine.",
                "items": {
                    "type": "object",
                    "properties": {
                        "numero": {"type": "integer"},
                        "testo_originale": {"type": "string", "description": "Il testo che leggi sulla slide originale (vuoto se non ce n'è)."},
                        "testo": {"type": "string", "description": "Il testo italiano che va sulla slide, con gli stessi a capo logici dell'originale. Vuoto se la slide non ha testo."},
                        "watermark_originale": {"type": "string", "description": "Handle, nome o logo della pagina di origine visibile sulla slide e dove si trova (es. '@pagina in basso a destra'). Vuoto se assente."},
                    },
                    "required": ["numero", "testo_originale", "testo", "watermark_originale"],
                },
            },
            "descrizione": {"type": "string",
                            "description": "Descrizione Instagram in italiano, max 2200 caratteri, che termina con la CTA."},
            "tipo_hook": {"type": "string", "enum": HOOK_TYPES},
            "autori_citati": {"type": "array", "items": {"type": "string"}},
            "cta": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["segui", "manuale"]},
                    "manuale": {"type": "string", "description": "id del manuale se tipo=manuale, altrimenti vuoto."},
                    "frase": {"type": "string", "description": "La CTA esatta usata in fondo alla descrizione."},
                    "motivo": {"type": "string", "description": "Perché questa CTA per questo post, in una frase."},
                },
                "required": ["tipo", "manuale", "frase", "motivo"],
            },
            "slide_da_rifare": {
                "type": "array", "items": {"type": "integer"},
                "description": "Solo nelle modifiche: numeri delle slide da rigenerare anche se il testo non cambia (es. richiesta grafica). Altrimenti lista vuota.",
            },
        },
        "required": ["senso", "formato", "natura", "slides", "descrizione",
                     "tipo_hook", "autori_citati", "cta", "slide_da_rifare"],
    },
}


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def load_brand() -> dict:
    with open(config.BRAND_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _system_prompt(brand: dict, rules: list[dict], recent: dict) -> str:
    manuali = "\n".join(
        f"- id: {m['id']} | {m['nome']} | dolori che risolve: {m.get('dolori', '')}"
        for m in brand.get("manuali", [])
    )
    regole = "\n".join(f"- {r['text']}" for r in rules) or "- (nessuna)"
    ultimi_hook = ", ".join(recent.get("hook", [])[:3]) or "nessuno"
    ultimi_autori = ", ".join(recent.get("autori", [])[:15]) or "nessuno"
    return f"""Sei il copywriter senior della pagina Instagram italiana {brand['nome']} (@{brand['handle']}).
Ricevi le slide di un post di una pagina pubblica qualsiasi (anche competitor, spesso in inglese) e lo trasformi in un post italiano pronto da pubblicare su {brand['nome']}.
La pagina di origine è solo una fonte di spunto: non citarla mai, non menzionarla, non usare il suo handle o la sua CTA. Tutto deve essere riscritto per {brand['nome']}.

POSIZIONAMENTO
{brand.get('posizionamento', '').strip()}

TONO
{chr(10).join('- ' + t for t in brand.get('tono', []))}

FORMATO
{chr(10).join('- ' + t for t in brand.get('regole_formato', []))}

SLIDE
- Leggi con attenzione tutto il testo di ogni slide e trascrivilo in testo_originale.
- Scrivi il testo italiano: stesso significato e stessa forza, adattato al tono, mai parola per parola.
- Mantieni lo stesso numero di slide, lo stesso ordine e una lunghezza simile, così entra nello stesso spazio grafico.
- Se una slide contiene una CTA o un riferimento alla pagina di origine (segui @..., link in bio di un'altra pagina, nome della pagina), sostituiscilo con il nostro equivalente per {brand['nome']}.
- Segnala in watermark_originale handle, nome o logo della pagina di origine e dove si trova sulla slide. Se non c'è watermark, lascia vuoto.

HOOK E STRUTTURA DELLA DESCRIZIONE
{brand['hook']['regola'].strip()}
{brand['hook']['struttura'].strip()}
Ultimi tipi di hook usati (dal più recente): {ultimi_hook}. Non usare lo stesso del più recente.
Autori citati di recente, da non ripetere: {ultimi_autori}.
Cita solo autori e studi reali che conosci con certezza; se non sei sicuro, non citare.

CTA
{brand['cta']['regola'].strip()}
{brand['cta']['stile'].strip()}
Dove si compra: {brand['cta'].get('dove_si_compra', 'link in bio')}.
Manuali disponibili:
{manuali}

REGOLE FISSE DATE DA GABRIELE (hanno la precedenza su tutto il resto)
{regole}
"""


def _image_block(data: bytes, max_side: int = 1280) -> dict:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def clean_text(s: str) -> str:
    s = s.replace("**", "").replace("*", "")
    s = re.sub(r"\s*[—–]\s*", ", ", s)
    return s.strip()


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


def _call(system: str, content: list) -> dict:
    resp = client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=8000,
        system=system,
        tools=[POST_TOOL],
        tool_choice={"type": "tool", "name": "post_pronto"},
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Claude ha troncato la risposta (max_tokens). Riduci il testo o alza il limite.")
    for block in resp.content:
        if block.type == "tool_use":
            return _finalize(dict(block.input))
    raise RuntimeError("Claude non ha restituito il post.")


def create_post(caption: str, owner: str, images: list[bytes], brand: dict,
                rules: list[dict], recent: dict) -> dict:
    content: list = []
    for i, data in enumerate(images, 1):
        content.append({"type": "text", "text": f"Slide {i}:"})
        content.append(_image_block(data))
    content.append({"type": "text", "text": (
        f"Pagina di origine: @{owner} (fonte di spunto, anche competitor).\n"
        f"Numero di slide: {len(images)}.\n\n"
        f"Descrizione originale:\n<<<\n{caption or '(vuota)'}\n>>>\n\n"
        "Crea il post italiano per la nostra pagina con lo strumento post_pronto. "
        "La descrizione non è una traduzione di quella originale: riscrivila seguendo le nostre regole, "
        "e sostituisci la CTA originale con la nostra."
    )})
    post = _call(_system_prompt(brand, rules, recent), content)
    post["slide_da_rifare"] = []
    return post


def revise_post(post: dict, instruction: str, brand: dict, rules: list[dict], recent: dict) -> dict:
    current = {k: v for k, v in post.items() if not k.startswith("_")}
    content = [{"type": "text", "text": (
        "Questo è il post attuale in JSON:\n"
        f"{json.dumps(current, ensure_ascii=False, indent=1)}\n\n"
        f"Richiesta di modifica di Gabriele:\n<<<\n{instruction}\n>>>\n\n"
        "Applica SOLO la modifica richiesta e restituisci il post completo con lo strumento post_pronto. "
        "Lascia identico tutto quello che la richiesta non tocca (stesse slide, stessi testi). "
        "Se la richiesta riguarda la grafica di slide specifiche senza cambiare il testo, mettile in slide_da_rifare."
    )}]
    return _call(_system_prompt(brand, rules, recent), content)