"""Configuração de logging da app (console + arquivo rotativo opcional).

O autorun e o scraper emitem logs via `logging`; sem handlers configurados o
Python só imprime WARNING+ (lastResort) e os `log.info` eram engolidos. Este
módulo resolve isso com `LOG_LEVEL`/`LOG_FILE` do `.env`.
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(app) -> None:
    if app.config.get("TESTING"):
        return
    root = logging.getLogger()
    if root.handlers:  # não duplicar handlers (reloader/chamadas repetidas)
        return
    level = getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [logging.StreamHandler()]
    if app.config.get("LOG_FILE"):
        path = Path(app.config["LOG_FILE"])
        if not path.is_absolute():
            path = Path(app.instance_path) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        )
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(level)
