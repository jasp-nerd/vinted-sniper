"""Logging setup.

Console output while you are watching it, JSON when something else is collecting it.
Log entries carry the search and site they belong to, so a failing search is easy to spot
among a dozen healthy ones.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

# These libraries are chatty at INFO and none of it is about your searches.
_NOISY_LOGGERS = ("httpx", "httpcore", "aiogram.event", "uvicorn.access")


def configure(level: str = "INFO", fmt: str = "console") -> None:
    """Set up structlog and the standard library logger it bridges to."""
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
        if fmt == "console"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=level, stream=sys.stderr, format="%(message)s")
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str, **initial: Any) -> structlog.stdlib.BoundLogger:
    """Return a logger, optionally pre-bound with context such as a search id."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger.bind(**initial) if initial else logger
