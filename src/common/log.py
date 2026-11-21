"""Logging for the whole project, configured from environment variables (or .env).

    LOG_LEVEL    DEBUG | INFO | WARNING | ERROR. Default: INFO for the API and the MCP server, WARNING for the
                 CLI and the evaluation (so a terminal stays quiet).
    LOG_FORMAT   text (default) or json (one object per line, easy to search)
    LOG_FILE     optional path, for example logs/assistant.log. Rotates at 5 MB and keeps 3 old files.

What is logged, by level
    INFO     one line per API request, per model call (seconds, ok or the error), per agent tool call, per
             fallback, and per follow-up that was resolved from memory. Enough to see what happened and why.
    WARNING  a model call that failed, an LLM or agent draft the guards rejected, a safe-writer fallback.
    DEBUG    each pipeline stage, what retrieval found, throttle waits, session lifecycle, and a preview of the
             question text.

What is never logged: API keys, answer text, snippet text, or the full question at INFO (questions can be
personal, so only their length is logged unless you ask for DEBUG).

Every line carries a request id (a short random id set per API request, MCP call or CLI question), so the
lines one question caused can be found together. Logs go to stderr: the MCP server talks to its client over
stdout, so anything printed there would break the protocol.
"""
from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import sys
import uuid
from pathlib import Path

ROOT_NAME = "oralcare"
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_configured = False


def new_request_id(value: str | None = None) -> str:
    """Start a new request: set (and return) the id that every log line from now on will carry."""
    rid = (value or uuid.uuid4().hex[:8])[:32]
    _request_id.set(rid)
    return rid


def current_request_id() -> str:
    return _request_id.get()


class _Context(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def _fields(record: logging.LogRecord) -> dict:
    return getattr(record, "fields", None) or {}


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = " ".join(f"{k}={_short(v)}" for k, v in _fields(record).items())
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} [{record.request_id}] {record.getMessage()}"
        out = f"{base} {extra}".rstrip()
        return out + ("\n" + self.formatException(record.exc_info) if record.exc_info else "")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                "logger": record.name, "request_id": record.request_id, "event": record.getMessage(), **_fields(record)}
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def _short(value, limit: int = 200) -> str:
    text = str(value)
    text = text if len(text) <= limit else text[: limit - 3] + "..."
    return json.dumps(text, ensure_ascii=False) if (" " in text or "=" in text or not text) else text


def _load_log_settings() -> None:
    """Apply LOG_* settings from the project's .env. Only LOG_* keys, and only non-empty ones: logging setup must
    never pull the API key or other settings into the process, and `LOG_LEVEL=` (empty) must not cancel a flag."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    for key, value in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
        if key.startswith("LOG_") and value:
            os.environ[key] = value


def setup_logging(default_level: str = "INFO", force: bool = False, level: str | None = None) -> logging.Logger:
    """Configure the project logger once (calling it again does nothing unless force=True).

    `level` (for example from a -v flag) beats LOG_LEVEL, which beats `default_level`.
    """
    global _configured
    logger = logging.getLogger(ROOT_NAME)
    if _configured and not force:
        return logger
    _load_log_settings()
    level = (level or os.getenv("LOG_LEVEL") or default_level).upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    formatter = _JsonFormatter() if os.getenv("LOG_FORMAT", "text").lower() == "json" else _TextFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]      # stderr: stdout is MCP's channel
    path = (os.getenv("LOG_FILE") or "").strip()
    if path:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_Context())
        logger.addHandler(handler)
    logger.propagate = False                       # never also print through the root logger
    _configured = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """A child of the project logger, e.g. get_logger("agent") -> "oralcare.agent"."""
    return logging.getLogger(f"{ROOT_NAME}.{name}")


def event(logger: logging.Logger, level: int, name: str, /, **fields) -> None:
    """Log one named event with key=value fields (or JSON fields). Cheap when the level is off.

    Fields named `question` and `detail` can hold user text, so they are dropped unless DEBUG is on.
    """
    if logger.isEnabledFor(level):
        if not logger.isEnabledFor(logging.DEBUG):
            fields = {k: v for k, v in fields.items() if k not in ("question", "detail")}
        logger.log(level, name, extra={"fields": fields})


def preview(question: str, limit: int = 80) -> str:
    """A short, single-line preview of a question, for DEBUG logs only."""
    one = " ".join(question.split())
    return one if len(one) <= limit else one[: limit - 3] + "..."
