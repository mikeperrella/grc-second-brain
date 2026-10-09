import secrets
import sys
import traceback
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import ValidationError

from grc_sidecar.config import Settings, load_settings
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
