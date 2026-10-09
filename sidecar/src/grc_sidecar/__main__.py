"""Entry point: `python -m grc_sidecar`."""

import sys

from grc_sidecar.config import load_settings
from grc_sidecar.errors import ConfigurationError, GrcError
from grc_sidecar.logging_config import configure_logging
from grc_sidecar.main import bind_loopback_socket, serve
from grc_sidecar.security.auth import read_auth_token
from grc_sidecar.security.process_guard import install_process_guard
from grc_sidecar.security.stdio import seal_stdout

EXIT_STARTUP_ERROR = 2
EXIT_INTERRUPTED = 130  # 128 + SIGINT, the shell convention for Ctrl+C


def main() -> int:
    try:
        # Read (and delete) the token before anything else can fail or spawn a process.
        token = read_auth_token()
        # Before anything that can start a process (uvicorn's platform check does on Windows).
        install_process_guard()
        settings = load_settings()
        configure_logging(settings)
        try:
            sock = bind_loopback_socket()
        except OSError:
            # For example a missing SYSTEMROOT, which Winsock needs on Windows.
            raise ConfigurationError("Loopback socket could not be bound") from None
        # Last step before serving: from here only the ready line can reach the host's pipe.
        ready_stream = seal_stdout()
    except GrcError as exc:
        # Every GrcError message is a fixed string: no token, URL, or path can appear.
        sys.stderr.write(f"grc-sidecar: {exc}\n")
        return EXIT_STARTUP_ERROR
    try:
        serve(token, settings, sock, ready_stream)
    except KeyboardInterrupt:
        # uvicorn finishes its graceful shutdown, then re-raises Ctrl+C. Exit quietly.
        return EXIT_INTERRUPTED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
