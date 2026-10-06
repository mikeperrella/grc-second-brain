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
