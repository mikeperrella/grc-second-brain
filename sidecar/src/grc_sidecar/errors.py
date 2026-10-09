"""Typed domain exceptions for the sidecar.

Every message is a fixed, generic string. Messages never echo a URL, path, or any
other caller-supplied value, because those can be attacker-controlled or sensitive
and error text ends up in logs. Mapping these errors to HTTP responses happens in
one place, added with the first router.
"""


class GrcError(Exception):
    """Base class for all sidecar domain errors."""

    default_message = "GRC sidecar error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class ConfigurationError(GrcError):
    """Settings or required local resources are invalid."""

    default_message = "Invalid configuration"


class NonLoopbackURLError(GrcError, ValueError):
    """A URL is not a plain http URL pointing at a loopback IP address."""

    default_message = "URL must be http and point at a loopback IP address"


class MissingAuthTokenError(GrcError):
    """The auth token environment variable is absent or empty."""

    default_message = "Authentication token is missing"


class InvalidAuthTokenError(GrcError):
    """The auth token is not exactly 64 lowercase hex characters."""

    default_message = "Authentication token is invalid"


class PathNotAllowedError(GrcError):
    """A path is outside the approved root or is not acceptable input."""

    default_message = "Path is not allowed"


class PathNotFoundError(GrcError):
    """A path is inside the approved root but does not exist."""

    default_message = "Path was not found"


class ProcessGuardError(GrcError):
    """The process-creation guard could not be installed or is not working."""

    default_message = "Process guard could not be installed"


class SpawnBlockedError(GrcError, PermissionError):
    """Starting a process was refused by the process guard.

    It is a PermissionError (an OSError) on purpose: the standard library already treats a
    failed `ver` lookup on Windows as "unknown" and carries on, but only for OSError.
    """

    default_message = "Starting a process is not allowed"


class StdoutSealError(GrcError):
    """Standard output could not be redirected away from the host's pipe."""

    default_message = "Standard output could not be sealed"
