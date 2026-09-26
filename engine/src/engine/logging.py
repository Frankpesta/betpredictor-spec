"""Structured logging: JSON lines to data/logs/engine.log + readable console output.

Usage:
    from engine.logging import get_logger
    log = get_logger(__name__)
    log.info("ingested file", extra={"fields": {"league": "EPL", "inserted": 380}})
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from engine.config import LOGS_DIR

_CONFIGURED = False
_MAX_LOG_BYTES = 10 * 1024 * 1024
_LOG_BACKUPS = 5


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%SZ")
        line = f"{ts} {record.levelname:<7} {record.name}: {record.getMessage()}"
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            line += "  " + " ".join(f"{k}={v}" for k, v in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: int = logging.INFO, log_dir: Path | None = None) -> None:
    """Idempotently attach the JSON file handler and console handler to the root logger."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_dir = log_dir or LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = RotatingFileHandler(
        log_dir / "engine.log",
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_LOG_BACKUPS,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    # Keep third-party noise down; our own loggers stay at `level`.
    for noisy in ("httpx", "httpcore", "alembic.runtime.migration"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
