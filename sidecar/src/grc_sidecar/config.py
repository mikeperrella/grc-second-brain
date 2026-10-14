"""Typed, validated settings for the sidecar.

Settings come from GRC_* environment variables only (no .env file). There is
deliberately no host, port, or auth-token field: the server always binds
127.0.0.1 on an OS-assigned port, and the auth token is handled separately and
never stored in settings (M1b).
"""

import os
from pathlib import Path, PurePath
from typing import Literal

from pydantic import Field, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from grc_sidecar.errors import ConfigurationError, GrcError
from grc_sidecar.security.net import validate_loopback_url
from grc_sidecar.security.paths import resolve_within

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _default_app_dir(folder: str, env_var: str) -> Path:
    try:
        home = Path.home()
    except RuntimeError:
        # Raised when neither USERPROFILE (or HOMEDRIVE+HOMEPATH) nor HOME/the user database
        # gives a home directory. Setting the environment variable avoids this lookup entirely.
        raise ConfigurationError(f"Home directory is not available; set {env_var}") from None
    return home / ".grc-second-brain" / folder


def _default_log_dir() -> Path:
    return _default_app_dir("logs", "GRC_LOG_DIR")


def _is_inside(inner: Path, outer: Path) -> bool:
    """Lexical "is `inner` the same folder as, or below, `outer`", case-folded on Windows.

    Nothing is resolved, because neither folder has to exist yet. A link that fooled this check
    is caught later, when files are read through the path guard.
    """
    inner_key = PurePath(os.path.normcase(os.path.normpath(inner)))
    outer_key = PurePath(os.path.normcase(os.path.normpath(outer)))
    return inner_key.is_relative_to(outer_key)


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
    # Where the derived search index lives. It must stay OUTSIDE the vault: the vault is the
    # source of truth and the index is disposable (CLAUDE.md 8.1). Created on demand. Unset means
    # "~/.grc-second-brain/index", worked out by `resolve_index_dir` when the index is opened and
    # not at startup: the host starts the sidecar with an allowlisted environment that has no home
    # directory, and startup must not fail over a setting nothing uses yet (same reasoning as
    # ADR-009's deviation from CLAUDE.md 3.7).
    index_dir: Path | None = None
    # Where questionnaire workbooks are read from and where answers files are written (ADR-014).
    # Both are chosen only here until the folder pickers exist (M6). Neither may be inside or
    # contain the vault or the index: ALIBI never writes into the vault (CLAUDE.md 8.1), and an
    # export folder that held the index would put a workbook next to derived state. Unset means
    # the questionnaire routes refuse with a typed error (M5k), not that a default is guessed.
    questionnaire_dir: Path | None = None
    export_dir: Path | None = None
    # Where offline IT exports (the reality check, ADR-016) are read from. Chosen only here until
    # the folder picker exists (M6). It may not equal, sit inside or contain the vault, the index,
    # the questionnaire folder or the export folder: an export holds personal data and must never
    # sit next to derived state or answers. Unset means the reality routes refuse (503).
    import_dir: Path | None = None
    # Where the promise ledger lives (ADR-017): `<ledger_dir>/ledger.sqlite3`. No default, on
    # purpose: unset means the ledger routes refuse (503) and questionnaire runs still work. It
    # cannot be rebuilt from anything, so it may not equal, sit inside or contain the vault, the
    # index (disposable derived state), the questionnaire, export or import folder.
    ledger_dir: Path | None = None
    # Bundled framework packs (ADR-008). Whether it exists is checked when packs are loaded.
    packs_dir: Path | None = None
    embedding_model: str = "nomic-embed-text"
    # The model M5 will use to answer questions. Nothing generates text in M4; it is listed here
    # because the startup model check (ADR-011) reports on every model the app needs.
    generation_model: str = Field(default="llama3.1:8b", min_length=1)
    # The vector size the model must produce. A probe embed at startup measures the real size and
    # the run fails closed (before any database write) if it differs from this.
    embedding_dimension: int = Field(default=768, gt=0, le=8192)

    @field_validator("questionnaire_dir", "export_dir")
    @classmethod
    def _questionnaire_dirs_must_not_overlap_vault_or_index(
        cls, value: Path | None, info: ValidationInfo
    ) -> Path | None:
        if value is None:
            return None
        for other_name in ("vault_dir", "index_dir"):
            other = info.data.get(other_name)  # declared above, so already validated
            if other is not None and (_is_inside(value, other) or _is_inside(other, value)):
                raise ValueError(f"{info.field_name} must not overlap {other_name}")
        return value

    @field_validator("import_dir")
    @classmethod
    def _import_dir_must_not_overlap_other_folders(
        cls, value: Path | None, info: ValidationInfo
    ) -> Path | None:
        if value is None:
            return None
        for other_name in ("vault_dir", "index_dir", "questionnaire_dir", "export_dir"):
            other = info.data.get(other_name)  # declared above, so already validated
            if other is not None and (_is_inside(value, other) or _is_inside(other, value)):
                raise ValueError(f"import_dir must not overlap {other_name}")
        return value

    @field_validator("ledger_dir")
    @classmethod
    def _ledger_dir_must_not_overlap_other_folders(
        cls, value: Path | None, info: ValidationInfo
    ) -> Path | None:
        if value is None:
            return None
        for other_name in (
            "vault_dir",
            "index_dir",
            "questionnaire_dir",
            "export_dir",
            "import_dir",
        ):
            other = info.data.get(other_name)  # declared above, so already validated
            if other is not None and (_is_inside(value, other) or _is_inside(other, value)):
                raise ValueError(f"ledger_dir must not overlap {other_name}")
        return value

    @field_validator(
        "log_dir",
        "vault_dir",
        "index_dir",
        "packs_dir",
        "questionnaire_dir",
        "export_dir",
        "import_dir",
        "ledger_dir",
    )
    @classmethod
    def _dirs_must_be_absolute(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("directory settings must be absolute paths")
        return value

    @field_validator("index_dir")
    @classmethod
    def _index_dir_must_be_outside_the_vault(
        cls, value: Path | None, info: ValidationInfo
    ) -> Path | None:
        vault_dir = info.data.get("vault_dir")  # declared above, so already validated
        if value is not None and vault_dir is not None and _is_inside(value, vault_dir):
            raise ValueError("index_dir must not be inside vault_dir")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("ollama_base_url")
    @classmethod
    def _ollama_url_must_be_loopback(cls, value: str) -> str:
        return validate_loopback_url(value)


def resolve_index_dir(settings: Settings) -> Path:
    """The folder that holds the index: the setting, or the default under the home directory."""
    index_dir = settings.index_dir or _default_app_dir("index", "GRC_INDEX_DIR")
    if settings.vault_dir is not None and _is_inside(index_dir, settings.vault_dir):
        raise ConfigurationError("Invalid configuration for: index_dir")
    return index_dir


def _existing_directory(path: Path | None, name: str) -> Path | None:
    """The resolved, existing directory behind a setting, or None if the setting is unset.

    `resolve_within(".", path)` resolves the path strictly (every symlink and junction is followed)
    and requires a directory. The error names the setting only, never the path, and is raised
    after the `except` block so no chained error can carry the path.
    """
    if path is None:
        return None
    resolved: Path | None = None
    try:
        resolved = resolve_within(".", path)
    except GrcError:
        resolved = None
    if resolved is None:
        raise ConfigurationError(f"Invalid configuration for: {name}")
    return resolved


def validate_runtime_dirs(settings: Settings) -> Settings:
    """Check the vault and index folders the host handed over, and use their resolved paths.

    The lexical rule in `Settings` ("index_dir is not inside vault_dir") cannot see a symlink or
    junction, so it is repeated here on the RESOLVED paths, in both directions: an index folder
    that is a link into the vault is rejected, and so is a vault inside the index folder. A vault
    root that is itself a symlink is accepted (the scanner's rule, ADR-009: only links INSIDE the
    vault are refused, per file); the real path behind it is what the rest of the sidecar uses.

    An unset setting is fine (no vault until the folder picker exists, M6; the index default is
    worked out lazily, ADR-009). A set but invalid one is a host bug, so the caller exits 2.
    """
    vault = _existing_directory(settings.vault_dir, "vault_dir")
    index = _existing_directory(settings.index_dir, "index_dir")
    nested = (
        vault is not None
        and index is not None
        and (_is_inside(index, vault) or _is_inside(vault, index))
    )
    if nested:
        raise ConfigurationError("Invalid configuration for: index_dir")
    questionnaire = _existing_directory(settings.questionnaire_dir, "questionnaire_dir")
    export = _existing_directory(settings.export_dir, "export_dir")
    for name, folder in (("questionnaire_dir", questionnaire), ("export_dir", export)):
        for other in (vault, index):
            overlaps = (
                folder is not None
                and other is not None
                and (_is_inside(folder, other) or _is_inside(other, folder))
            )
            if overlaps:
                raise ConfigurationError(f"Invalid configuration for: {name}")
    import_folder = _existing_directory(settings.import_dir, "import_dir")
    if import_folder is not None:
        for other in (vault, index, questionnaire, export):
            if other is not None and (
                _is_inside(import_folder, other) or _is_inside(other, import_folder)
            ):
                raise ConfigurationError("Invalid configuration for: import_dir")
    ledger_folder = _existing_directory(settings.ledger_dir, "ledger_dir")
    if ledger_folder is not None:
        for other in (vault, index, questionnaire, export, import_folder):
            if other is not None and (
                _is_inside(ledger_folder, other) or _is_inside(other, ledger_folder)
            ):
                raise ConfigurationError("Invalid configuration for: ledger_dir")
    return settings.model_copy(
        update={
            "vault_dir": vault,
            "index_dir": index,
            "questionnaire_dir": questionnaire,
            "export_dir": export,
            "import_dir": import_folder,
            "ledger_dir": ledger_folder,
        }
    )


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
