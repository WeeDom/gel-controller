"""Structured logging helpers for GEL controller runtime."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonLineFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    _RESERVED = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
            "incident": bool(getattr(record, "incident", False)),
            "event_type": getattr(record, "event_type", None),
        }

        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


class IncidentOnlyFilter(logging.Filter):
    """Allow only records explicitly tagged as incidents."""

    def filter(self, record: logging.LogRecord) -> bool:
        return bool(getattr(record, "incident", False))


def setup_logging(log_dir: Path) -> dict[str, Path]:
    """Configure root logging with JSON files and human-readable console output."""
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log = log_dir / f"gel-debug-{stamp}.jsonl"
    incident_log = log_dir / f"gel-incidents-{stamp}.jsonl"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    json_formatter = JsonLineFormatter()
    console_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    debug_handler = logging.FileHandler(debug_log, encoding="utf-8")
    debug_handler.setLevel(logging.INFO)
    debug_handler.setFormatter(json_formatter)

    incident_handler = logging.FileHandler(incident_log, encoding="utf-8")
    incident_handler.setLevel(logging.INFO)
    incident_handler.setFormatter(json_formatter)
    incident_handler.addFilter(IncidentOnlyFilter())

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)

    root.addHandler(debug_handler)
    root.addHandler(incident_handler)
    root.addHandler(console_handler)

    return {"debug_log": debug_log, "incident_log": incident_log}


def log_debug_event(logger: logging.Logger, message: str, *, event_type: str | None = None, **fields: Any) -> None:
    """Emit a non-incident event into the main JSON log."""
    extra = {"incident": False, "event_type": event_type}
    extra.update(fields)
    logger.info(message, extra=extra)


def log_incident(logger: logging.Logger, message: str, *, event_type: str, **fields: Any) -> None:
    """Emit an incident event into both the main and incident JSON logs."""
    extra = {"incident": True, "event_type": event_type}
    extra.update(fields)
    logger.info(message, extra=extra)
