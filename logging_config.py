"""Logging configuration.

Call ``get_logger(__name__)`` from any module. Configuration is idempotent so
importing repeatedly does not add duplicate handlers.
"""

import logging

_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level=logging.INFO) -> None:
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(level=level, format=_DEFAULT_FORMAT)
        _CONFIGURED = True


def get_logger(name):
    configure_logging()
    return logging.getLogger(name)
