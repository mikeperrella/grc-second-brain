"""Local-file logging.

Logs go to one rotating file only. There is no stream handler, because stdout is
reserved for the single JSON "ready" line that the Rust host reads (M1b).

Rules for everything that logs: never log the auth token, and never log document
or questionnaire content above DEBUG.
"""

import logging
from logging.handlers import RotatingFileHandler

from grc_sidecar.config import Settings
from grc_sidecar.errors import ConfigurationError

LOG_FILE_NAME = "grc-sidecar.log"
HANDLER_NAME = "grc-sidecar-file"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3


def configure_logging(settings: Settings) -> None:
    """Install the rotating file handler on the root logger (replacing any earlier one)."""
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            settings.log_dir / LOG_FILE_NAME,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        raise ConfigurationError("Log directory is not usable") from None

    handler.set_name(HANDLER_NAME)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == HANDLER_NAME:
            root.removeHandler(existing)
            existing.close()
    root.addHandler(handler)
    root.setLevel(settings.log_level)
