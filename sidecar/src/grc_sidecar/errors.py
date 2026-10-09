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


class VaultError(GrcError):
    """A vault file could not be indexed. Carries *where* and *why* as attributes.

    `str(exc)` stays a fixed string (see the module docstring). The file, field and line are
    structured attributes so the index report can show them without the message ever echoing
    document content. `source_path` is always vault-relative, never absolute.
    """

    default_message = "Vault file could not be indexed"
    code = "vault_error"

    def __init__(
        self,
        *,
        source_path: str | None = None,
        field: str | None = None,
        line: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__()
        self.source_path = source_path
        self.field = field
        self.line = line
        if code is not None:
            self.code = code


class MissingFrontmatterError(VaultError):
    """The file does not start with a `---` frontmatter block."""

    default_message = "Frontmatter block is missing"
    code = "missing_frontmatter"


class FrontmatterSyntaxError(VaultError):
    """The frontmatter block is not valid, safe YAML."""

    default_message = "Frontmatter is not valid"
    code = "frontmatter_syntax"


class FrontmatterFieldError(VaultError):
    """A frontmatter field is missing or has an invalid value."""

    default_message = "Frontmatter field is missing or invalid"
    code = "frontmatter_field"


class InvalidEncodingError(VaultError):
    """The file is not valid UTF-8 text (or contains a NUL byte)."""

    default_message = "File is not valid UTF-8 text"
    code = "invalid_encoding"


class FileTooLargeError(VaultError):
    """The file is larger than the indexer accepts."""

    default_message = "File is too large to index"
    code = "file_too_large"


class DuplicateDocIdError(VaultError):
    """Two or more files declare the same doc_id."""

    default_message = "doc_id is used by more than one file"
    code = "duplicate_doc_id"


class UnsafePathError(VaultError):
    """A vault file is a link, escapes the vault, or otherwise fails the path guard."""

    default_message = "Path is not safe to index"
    code = "unsafe_path"


class PackMetadataError(GrcError):
    """A framework pack's pack.json is missing fields or has invalid values.

    `fields` lists the offending field names only, never their values.
    """

    default_message = "Pack metadata is invalid"

    def __init__(self, fields: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.fields = fields
