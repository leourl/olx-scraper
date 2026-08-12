import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _resolve_db_url(url: str) -> str:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        if path and not path.startswith("/"):
            return f"sqlite:////{(BASE_DIR / path).resolve()}"
    return url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = _resolve_db_url(
        os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'olx.db'}")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
    USER_AGENT = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    )
    SCRAPER_DELAY = float(os.getenv("SCRAPER_DELAY", "1.0"))
    SCRAPER_MAX_PAGES = int(os.getenv("SCRAPER_MAX_PAGES", "5"))
    SCRAPER_TIMEOUT = float(os.getenv("SCRAPER_TIMEOUT", "30"))
    SCRAPER_IMPERSONATE = os.getenv("SCRAPER_IMPERSONATE", "chrome")
    SCRAPER_BLOCK_COOLDOWN_MINUTES = int(os.getenv("SCRAPER_BLOCK_COOLDOWN_MINUTES", "60"))

    DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY", "")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    LLM_MAX_CHARS = int(os.getenv("LLM_MAX_CHARS", "1500"))
    LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
    LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none")
    LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

    AUTORUN_ENABLED = os.getenv("AUTORUN_ENABLED", "0") == "1"
    AUTORUN_INTERVAL_MINUTES = int(os.getenv("AUTORUN_INTERVAL_MINUTES", "120"))

    # Lote de specs sem cpu_generation reprocessado a cada etapa "process" default
    # (evita o backlog de geração crescer de novo; D-031).
    PROCESS_MISSING_GEN_LIMIT = int(os.getenv("PROCESS_MISSING_GEN_LIMIT", "50"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = os.getenv("LOG_FILE", "")
