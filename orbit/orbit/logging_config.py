# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
from .core.runtime_paths import app_root
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any

# Control characters (incl. CRLF) used to forge fake log lines / split records.
_CTRL_RE = re.compile(r"[\r\n\t]+")


class LogInjectionFilter(logging.Filter):
    """Strip control chars (CRLF/tab) from log messages and args.

    Prevents a crafted user/agent string containing newlines from forging extra
    log lines or breaking structured (console) log parsing. JSON logs already
    escape newlines, but this is defense-in-depth across all handlers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _CTRL_RE.sub(" ", record.msg)
        if record.args:
            try:
                record.args = tuple(_CTRL_RE.sub(" ", a) if isinstance(a, str) else a for a in record.args)
            except Exception:
                pass
        return True


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging (secret-redacted)"""

    def _redact(self, value: Any) -> Any:
        try:
            from .core.secret_redactor import redact_value

            return redact_value(value)
        except Exception:
            return value

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact(record.getMessage()),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            log_data["exception"] = self._redact(self.formatException(record.exc_info))

        if hasattr(record, "extra_data"):
            log_data["extra"] = self._redact(record.extra_data)

        return json.dumps(log_data, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output"""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        reset = self.RESET
        record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


def setup_logging(
    level: int = logging.INFO,
    log_file: str | None = None,
    json_format: bool | None = None,
) -> None:
    """Configure application logging.

    `json_format` defaults to the ORBIT_JSON_LOGS env var (1/true = JSON
    structured output on console); file handlers are always JSON + redacted.
    """

    if json_format is None:
        json_format = os.environ.get("ORBIT_JSON_LOGS", "").lower() in ("1", "true", "yes", "on")

    log_dir = app_root() / "logs"
    log_dir.mkdir(exist_ok=True)

    handlers = []

    logging.getLogger().addFilter(LogInjectionFilter())

    console_handler = logging.StreamHandler(sys.stdout)
    if json_format:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(
            ColoredFormatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    handlers.append(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_dir / log_file, encoding="utf-8")
        file_handler.setFormatter(JSONFormatter())
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance"""
    return logging.getLogger(name)


class LoggerMixin:
    """Mixin to add logging capabilities to classes"""

    @property
    def logger(self) -> logging.Logger:
        if not hasattr(self, "_logger"):
            self._logger = get_logger(self.__class__.__module__ + "." + self.__class__.__name__)
        return self._logger

    def log_debug(self, msg: str, **extra) -> None:
        self.logger.debug(msg, extra={"extra_data": extra} if extra else None)

    def log_info(self, msg: str, **extra) -> None:
        self.logger.info(msg, extra={"extra_data": extra} if extra else None)

    def log_warning(self, msg: str, **extra) -> None:
        self.logger.warning(msg, extra={"extra_data": extra} if extra else None)

    def log_error(self, msg: str, **extra) -> None:
        self.logger.error(msg, extra={"extra_data": extra} if extra else None)

    def log_exception(self, msg: str, **extra) -> None:
        self.logger.exception(msg, extra={"extra_data": extra} if extra else None)
