"""Cooldown de bloqueio da coleta (persistido em `instance/scrape_block.json`).

Quando a OLX responde 403 (Cloudflare), a run termina com `status="blocked"` e
grava aqui "até quando" a coleta deve ficar parada. O autorun lê esse arquivo e
não dispara novas runs durante o cooldown — o bloqueio é limpo na primeira run
bem-sucedida. Toda leitura/escrita é tolerante a erro (best-effort): arquivo
ausente/corrompido = livre, falha de escrita só loga warning.
"""
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

BLOCK_FILE = "scrape_block.json"


def _path(instance_path: str) -> Path:
    return Path(instance_path) / BLOCK_FILE


def get_blocked_until(instance_path: str) -> float:
    """Epoch (time.time) até quando a coleta está bloqueada; `0` = livre."""
    try:
        data = json.loads(_path(instance_path).read_text(encoding="utf-8"))
        return float(data.get("blocked_until", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0.0


def set_blocked(instance_path: str, until_ts: float) -> None:
    """Grava o cooldown: `until_ts` (epoch) até quando ficar parado."""
    data = {
        "blocked_until": float(until_ts),
        "reason": "OLX respondeu 403 (Cloudflare)",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        _path(instance_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        log.exception("não foi possível gravar o cooldown de bloqueio")


def clear_blocked(instance_path: str) -> None:
    """Remove o bloqueio (run bem-sucedida). Best-effort."""
    try:
        _path(instance_path).unlink(missing_ok=True)
    except OSError:
        log.exception("não foi possível limpar o cooldown de bloqueio")
