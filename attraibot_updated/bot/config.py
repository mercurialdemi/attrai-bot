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
    int(x) for x in _env("ALLOWED_USER_IDS").replace(" ", "").split(",") if x.isdigit()
}

# --- Modelli (cambiabili senza toccare il codice) ---
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-sonnet-4-5")
GEMINI_IMAGE_MODEL = _env("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
APIFY_ACTOR = _env("APIFY_ACTOR", "apify/instagram-scraper")

# "gemini"  = Gemini riscrive il testo in italiano direttamente sulla slide originale.
# "overlay" = Gemini pulisce la slide da testi e watermark, il testo italiano lo scrive il codice.
SLIDE_TEXT_MODE = _env("SLIDE_TEXT_MODE", "gemini").lower()

# --- File e cartelle ---
DATA_DIR = Path(_env("DATA_DIR", str(ROOT / "data")))
BRAND_FILE = Path(_env("BRAND_FILE", str(ROOT / "brands" / "attraiseduci.yaml")))
LOGO_PATH = Path(_env("LOGO_PATH", str(ROOT / "assets" / "logo.png")))
FONT_PATH = Path(_env("FONT_PATH", str(ROOT / "assets" / "font.ttf")))

MAX_PARALLEL_IMAGES = int(_env("MAX_PARALLEL_IMAGES", "4"))
SLIDE_SIZE = (1080, 1350)  # formato verticale 4:5 di Instagram


def missing_keys() -> list[str]:
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "GEMINI_API_KEY": GEMINI_API_KEY,
        "APIFY_TOKEN": APIFY_TOKEN,
    }
    return [k for k, v in required.items() if not v]