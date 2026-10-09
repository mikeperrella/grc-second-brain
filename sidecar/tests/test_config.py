import secrets
import sys
import traceback
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import ValidationError

from grc_sidecar.config import Settings, load_settings, resolve_index_dir
from grc_sidecar.errors import ConfigurationError

pytestmark = pytest.mark.usefixtures("clean_grc_env")


def test_defaults() -> None:
    settings = load_settings()
    assert settings.log_dir == Path.home() / ".grc-second-brain" / "logs"
    assert settings.log_level == "INFO"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GRC_LOG_LEVEL", "debug")
    monkeypatch.setenv("GRC_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("GRC_OLLAMA_BASE_URL", "http://127.0.0.1:9999")
    settings = load_settings()
    assert settings.log_level == "DEBUG"
    assert settings.log_dir == tmp_path / "logs"
    assert settings.ollama_base_url == "http://127.0.0.1:9999"


def test_invalid_log_level_rejected(monkeypatch: pytest.MonkeyPatch) -> None:  # [NEG]
    monkeypatch.setenv("GRC_LOG_LEVEL", "verbose")
    with pytest.raises(ConfigurationError):
        load_settings()


@pytest.mark.parametrize(
    "url", ["http://example.com", "http://localhost:11434", "https://127.0.0.1"]
)
def test_non_loopback_ollama_url_rejected(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    monkeypatch.setenv("GRC_OLLAMA_BASE_URL", url)
    with pytest.raises(ConfigurationError):
        load_settings()


def test_relative_log_dir_rejected(monkeypatch: pytest.MonkeyPatch) -> None:  # [NEG]
    monkeypatch.setenv("GRC_LOG_DIR", "relative/logs")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_no_host_port_or_token_field_and_token_never_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # [NEG]
    assert {"host", "port", "auth_token"}.isdisjoint(Settings.model_fields)
    token = secrets.token_hex(32)
    monkeypatch.setenv("GRC_AUTH_TOKEN", token)
    settings = load_settings()
    assert token not in repr(settings)
    assert token not in str(settings.model_dump())
    assert token not in str(vars(settings))
    assert not hasattr(settings, "auth_token")


def test_dotenv_in_working_directory_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # [NEG]
    (tmp_path / ".env").write_text(
        "GRC_LOG_LEVEL=DEBUG\nGRC_OLLAMA_BASE_URL=http://example.com\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.log_level == "INFO"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"


def test_error_never_contains_a_secret_from_a_bad_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # [NEG]
    monkeypatch.setenv("GRC_OLLAMA_BASE_URL", "http://user:s3cr3t-pw@evil.example/")
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    rendered = "".join(traceback.format_exception(info.value))
    assert "s3cr3t" not in str(info.value)
    assert "s3cr3t" not in rendered
    assert "evil.example" not in rendered
    assert "ollama_base_url" in str(info.value)


def test_settings_are_frozen() -> None:
    settings = load_settings()
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


def _remove_home_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME"):
        monkeypatch.delenv(name, raising=False)
    if sys.platform != "win32":
        import pwd

        def no_such_user(uid: int) -> NoReturn:
            raise KeyError(uid)

        monkeypatch.setattr(pwd, "getpwuid", no_such_user)


def test_missing_home_directory_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # [NEG]
    _remove_home_directory(monkeypatch)
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    assert str(info.value) == "Home directory is not available; set GRC_LOG_DIR"
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__  # no RuntimeError chained behind it


def test_explicit_log_dir_needs_no_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _remove_home_directory(monkeypatch)
    monkeypatch.setenv("GRC_LOG_DIR", str(tmp_path))
    assert load_settings().log_dir == tmp_path


def test_vault_dir_defaults_to_unset() -> None:
    assert load_settings().vault_dir is None


def test_vault_dir_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GRC_VAULT_DIR", str(tmp_path / "vault"))
    assert load_settings().vault_dir == tmp_path / "vault"


def test_relative_vault_dir_rejected(monkeypatch: pytest.MonkeyPatch) -> None:  # [NEG]
    monkeypatch.setenv("GRC_VAULT_DIR", "relative/vault")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "relative" not in str(caught.value)  # reports the field name, never the value


def test_index_settings_defaults() -> None:
    settings = load_settings()
    assert settings.index_dir is None  # the home-based default is worked out on use, not at startup
    assert settings.packs_dir is None
    assert settings.embedding_model == "nomic-embed-text"
    assert resolve_index_dir(settings) == Path.home() / ".grc-second-brain" / "index"


def test_index_settings_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GRC_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("GRC_PACKS_DIR", str(tmp_path / "packs"))
    monkeypatch.setenv("GRC_EMBEDDING_MODEL", "other-embed")
    settings = load_settings()
    assert resolve_index_dir(settings) == tmp_path / "index"
    assert settings.packs_dir == tmp_path / "packs"
    assert settings.embedding_model == "other-embed"


@pytest.mark.parametrize("name", ["GRC_INDEX_DIR", "GRC_PACKS_DIR"])
def test_relative_index_and_packs_dirs_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:  # [NEG]
    monkeypatch.setenv(name, "relative/dir")
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert "relative" not in str(caught.value)


@pytest.mark.parametrize("inside", ["", "sub/folder", "sub/../other"])
def test_index_dir_inside_the_vault_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inside: str
) -> None:  # [NEG]
    vault = tmp_path / "vault"
    monkeypatch.setenv("GRC_VAULT_DIR", str(vault))
    monkeypatch.setenv("GRC_INDEX_DIR", str(vault / inside))
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert str(caught.value) == "Invalid configuration for: index_dir"
    assert "vault" not in str(caught.value)


def test_letter_case_decides_whether_the_index_dir_is_inside_the_vault_only_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # [NEG]
    # On Windows "Vault" and "VAULT" are one folder, so this is rejected. On Linux they are two
    # different folders, so it is allowed. The test runs on both.
    monkeypatch.setenv("GRC_VAULT_DIR", str(tmp_path / "Vault"))
    monkeypatch.setenv("GRC_INDEX_DIR", str(tmp_path / "VAULT" / "index"))
    if sys.platform == "win32":
        with pytest.raises(ConfigurationError):
            load_settings()
    else:
        assert load_settings().index_dir == tmp_path / "VAULT" / "index"


def test_index_dir_beside_the_vault_is_fine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GRC_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("GRC_INDEX_DIR", str(tmp_path / "vault-index"))  # shares a name prefix only
    assert load_settings().index_dir == tmp_path / "vault-index"


def test_the_default_index_dir_must_also_be_outside_the_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # [NEG]
    monkeypatch.setenv("GRC_VAULT_DIR", str(Path.home() / ".grc-second-brain"))
    settings = load_settings()  # nothing explicit to reject yet...
    with pytest.raises(ConfigurationError):  # ...but the default lands inside the vault
        resolve_index_dir(settings)


def test_missing_home_directory_only_matters_when_the_index_dir_is_needed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # [NEG]
    _remove_home_directory(monkeypatch)
    monkeypatch.setenv("GRC_LOG_DIR", str(tmp_path))
    settings = load_settings()  # startup must still work: this is how the host starts the sidecar
    with pytest.raises(ConfigurationError) as info:
        resolve_index_dir(settings)
    assert str(info.value) == "Home directory is not available; set GRC_INDEX_DIR"


def test_embedding_dimension_defaults_to_768_and_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_settings().embedding_dimension == 768
    monkeypatch.setenv("GRC_EMBEDDING_DIMENSION", "384")
    assert load_settings().embedding_dimension == 384


@pytest.mark.parametrize("value", ["0", "-5", "9000", "abc", "768.5"])
def test_an_invalid_embedding_dimension_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:  # [NEG]
    monkeypatch.setenv("GRC_EMBEDDING_DIMENSION", value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert value not in str(caught.value)  # the field name is reported, never the value
