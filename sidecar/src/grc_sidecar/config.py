"""Typed, validated settings for the sidecar.

Settings come from GRC_* environment variables only (no .env file). There is
deliberately no host, port, or auth-token field: the server always binds
127.0.0.1 on an OS-assigned port, and the auth token is handled separately and
never stored in settings (M1b).
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from grc_sidecar.errors import ConfigurationError
from grc_sidecar.security.net import validate_loopback_url

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _default_log_dir() -> Path:
    try:
        home = Path.home()
    except RuntimeError:
        # Raised when neither USERPROFILE (or HOMEDRIVE+HOMEPATH) nor HOME/the user database
        # gives a home directory. Setting GRC_LOG_DIR avoids this lookup entirely.
        raise ConfigurationError("Home directory is not available; set GRC_LOG_DIR") from None
    return home / ".grc-second-brain" / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GRC_",
        env_file=None,
        extra="ignore",
        frozen=True,
        validate_default=True,
    )

    log_dir: Path = Field(default_factory=_default_log_dir)
    log_level: LogLevel = "INFO"
    ollama_base_url: str = "http://127.0.0.1:11434"
    # The knowledge vault folder. Chosen only here, from the environment: no IPC command or HTTP
    # route takes a path yet (ADR-009 records why the Rust-side path check is still deferred).
    # Whether it exists and is a directory is checked when the vault is scanned.
    vault_dir: Path | None = None

    @field_validator("log_dir", "vault_dir")
    @classmethod
    def _dirs_must_be_absolute(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("directory settings must be absolute paths")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("ollama_base_url")
    @classmethod
    def _ollama_url_must_be_loopback(cls, value: str) -> str:
        return validate_loopback_url(value)


def load_settings() -> Settings:
    """Load settings from the environment.

    A pydantic ValidationError echoes the offending input values, which could be a
    URL with credentials. So we report only the names of the invalid fields.
    """
    try:
        return Settings()
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]) for err in exc.errors() if err["loc"]})
        detail = ", ".join(names) if names else "unknown field"
        raise ConfigurationError(f"Invalid configuration for: {detail}") from None
