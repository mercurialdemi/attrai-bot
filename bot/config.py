"""Impostazioni del bot, lette dalle variabili d'ambiente (su Render: scheda Environment)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# --- Chiavi obbligatorie ---
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
GEMINI_API_KEY = _env("GEMINI_API_KEY")
APIFY_TOKEN = _env("APIFY_TOKEN")

# --- Chi può usare il bot (ID Telegram separati da virgola: tu e il tuo socio) ---
ALLOWED_USER_IDS = {
    int(x) for x in (_env("ALLOWED_USER_IDS") + "," + _env("SOCI_IDS")).replace(" ", "").split(",") if x.isdigit()
}
# SOCI_IDS: ID Telegram dei soci aggiunti dopo (stesso accesso, ricevono anche il report)
_SOCI = {int(x) for x in _env("SOCI_IDS").replace(" ", "").split(",") if x.isdigit()}

# --- Chi riceve il report (se vuoto: tutti gli ALLOWED_USER_IDS) ---
REPORT_CHAT_IDS = {
    int(x) for x in _env("REPORT_CHAT_IDS").replace(" ", "").split(",") if x.lstrip("-").isdigit()
}
if REPORT_CHAT_IDS:
    REPORT_CHAT_IDS |= _SOCI

# --- Modelli (cambiabili senza toccare il codice) ---
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-sonnet-5-5")
# La descrizione è il testo che vende: la scrive il modello più forte nella scrittura (una chiamata per post)
CAPTION_MODEL = _env("CAPTION_MODEL", "claude-opus-5-5")
GEMINI_IMAGE_MODEL = _env("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_IMAGE_MODEL = _env("OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")   # il migliore nelle modifiche e nel testo piccolo
OPENAI_IMAGE_QUALITY = _env("OPENAI_IMAGE_QUALITY", "high")
# Motore per le immagini che il codice non sa fare (uno solo, per non pagare due volte):
#   openai (predefinito se c'è la sua chiave) = OpenAI riscrive direttamente il testo italiano sulla slide
#   gemini            = solo Gemini
#   gemini-poi-openai = prima Gemini, OpenAI solo se Gemini fallisce
MOTORE_IMMAGINI = (_env("MOTORE_IMMAGINI", "") or ("openai" if OPENAI_API_KEY else "gemini")).lower()
APIFY_ACTOR = _env("APIFY_ACTOR", "apify/instagram-scraper")
# Per i post con limite d'età: cookie di un account Instagram secondario ("sessionid=...; csrftoken=...")
# e l'attore Apify che li accetta. Si usano solo quando la lettura normale viene bloccata.
INSTAGRAM_COOKIES = _env("INSTAGRAM_COOKIES")
# API ufficiale di Instagram per le statistiche vere della nostra pagina (copertura, salvataggi, clic sul link...)
INSTAGRAM_TOKEN = _env("INSTAGRAM_TOKEN")
IG_API_VERSION = _env("IG_API_VERSION", "v23.0")
# Rapporto notturno su GitHub (ramo "diagnostica") per il miglioramento automatico del codice
GITHUB_TOKEN = _env("GITHUB_TOKEN")
GITHUB_REPO = _env("GITHUB_REPO", "mercurialdemi/attrai-bot")
DIAG_BRANCH = _env("DIAG_BRANCH", "diagnostica")
APIFY_LOGIN_ACTOR = _env("APIFY_LOGIN_ACTOR", "fmchisti/instagram-posts-scraper")
# Modello economico che controlla ogni slide prodotta da Gemini (testo giusto, niente residui)
CHECK_MODEL = _env("CHECK_MODEL", "claude-haiku-5-5")
# analisi del perché dei post (report e studio): Sonnet basta e costa molto meno di Opus
ANALYSIS_MODEL = _env("ANALYSIS_MODEL", "claude-sonnet-5-5")
SLIDE_CHECK = _env("SLIDE_CHECK", "on").lower() not in ("off", "0", "no", "false")
SLIDE_ATTEMPTS = int(_env("SLIDE_ATTEMPTS", "2"))   # tentativi Gemini per slide prima di segnalarla

# "codice"  = (consigliato) Gemini cancella solo il testo, il testo italiano di Claude lo scrive il codice.
# "gemini"  = Gemini riscrive il testo in italiano direttamente sulla slide originale (stesso stile).
# "overlay" = Gemini pulisce la slide da testi e watermark, il testo italiano lo scrive il codice.
SLIDE_TEXT_MODE = _env("SLIDE_TEXT_MODE", "codice").lower()

# --- File e cartelle ---
DATA_DIR = Path(_env("DATA_DIR", str(ROOT / "data")))
BRAND_FILE = Path(_env("BRAND_FILE", str(ROOT / "brands" / "attraiseduci.yaml")))
LOGO_PATH = Path(_env("LOGO_PATH", str(ROOT / "assets" / "logo.png")))
FONT_PATH = Path(_env("FONT_PATH", str(ROOT / "assets" / "font.ttf")))

MAX_PARALLEL_IMAGES = int(_env("MAX_PARALLEL_IMAGES", "2"))
SLIDE_SIZE = (1080, 1350)  # formato verticale 4:5 di Instagram


def missing_keys() -> list[str]:
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "GEMINI_API_KEY o OPENAI_API_KEY": GEMINI_API_KEY or OPENAI_API_KEY,
        "APIFY_TOKEN": APIFY_TOKEN,
    }
    return [k for k, v in required.items() if not v]

# Slide disegnate (fumetti, illustrazioni): si ricostruiscono da zero senza scritte e il testo lo scrive il codice.
#   gemini (ora) | off (vecchio metodo: si cancellano solo le lettere)
RICOSTRUISCI_DISEGNI = _env("RICOSTRUISCI_DISEGNI", "off").lower()   # prova del 23/10: Gemini sposta fumetti e personaggi
