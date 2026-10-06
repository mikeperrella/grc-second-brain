"""Entry point: `python -m grc_sidecar`."""

import sys

from grc_sidecar.config import load_settings
from grc_sidecar.errors import GrcError
from grc_sidecar.logging_config import configure_logging
from grc_sidecar.main import serve
from grc_sidecar.security.auth import read_auth_token

EXIT_STARTUP_ERROR = 2
EXIT_INTERRUPTED = 130  # 128 + SIGINT, the shell convention for Ctrl+C


def main() -> int:
    try:
        # Read (and delete) the token before anything else can fail or spawn a process.
        token = read_auth_token()
        settings = load_settings()
        configure_logging(settings)
    except GrcError as exc:
        # Every GrcError message is a fixed string: no token, URL, or path can appear.
        sys.stderr.write(f"grc-sidecar: {exc}\n")
        return EXIT_STARTUP_ERROR
    try:
        serve(token, settings)
    except KeyboardInterrupt:
        # uvicorn finishes its graceful shutdown, then re-raises Ctrl+C. Exit quietly.
        return EXIT_INTERRUPTED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
