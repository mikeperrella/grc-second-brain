"""The questionnaire and export folders (ADR-014): absolute, and never overlapping the vault or the
index. ALIBI never writes into the vault (CLAUDE.md 8.1), so an export folder inside it, or one that
contains it, is refused when settings load and again on the resolved paths (a link can fool the
first check)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from grc_sidecar.config import Settings, load_settings, validate_runtime_dirs
from grc_sidecar.errors import ConfigurationError

pytestmark = pytest.mark.usefixtures("clean_grc_env")


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    vault = tmp_path / "vault"
    (vault / "inside").mkdir(parents=True)
    index = tmp_path / "index"
    (index / "inside").mkdir(parents=True)
    questionnaires = tmp_path / "questionnaires"
    questionnaires.mkdir()
    exports = tmp_path / "exports"
    exports.mkdir()
    return {
        "vault": vault,
        "index": index,
        "questionnaires": questionnaires,
        "exports": exports,
        "tmp": tmp_path,
    }


def make(dirs: dict[str, Path], **overrides: Path | None) -> Settings:
    values: dict[str, Path | None] = {
        "log_dir": dirs["tmp"] / "logs",
        "vault_dir": dirs["vault"],
        "index_dir": dirs["index"],
        "questionnaire_dir": dirs["questionnaires"],
        "export_dir": dirs["exports"],
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_both_folders_are_unset_by_default(tmp_path: Path) -> None:
    settings = Settings(log_dir=tmp_path / "logs")
    assert (settings.questionnaire_dir, settings.export_dir) == (None, None)


def test_folders_apart_from_the_vault_and_index_are_accepted(dirs: dict[str, Path]) -> None:
    settings = make(dirs)
    assert settings.questionnaire_dir == dirs["questionnaires"]
    assert settings.export_dir == dirs["exports"]
    resolved = validate_runtime_dirs(settings)
    assert resolved.export_dir == dirs["exports"].resolve()


def test_the_two_folders_may_be_the_same_folder(dirs: dict[str, Path]) -> None:
    assert make(dirs, export_dir=dirs["questionnaires"]).export_dir == dirs["questionnaires"]


@pytest.mark.parametrize("field", ["questionnaire_dir", "export_dir"])
def test_a_relative_folder_is_rejected(dirs: dict[str, Path], field: str) -> None:  # [NEG]
    with pytest.raises(ValidationError):
        make(dirs, **{field: Path("relative/folder")})


@pytest.mark.parametrize("field", ["questionnaire_dir", "export_dir"])
@pytest.mark.parametrize("other", ["vault", "index"])
@pytest.mark.parametrize("relation", ["inside", "same", "contains"])
def test_a_folder_overlapping_the_vault_or_the_index_is_rejected(  # [NEG]
    dirs: dict[str, Path], field: str, other: str, relation: str
) -> None:
    folder = {
        "inside": dirs[other] / "inside",
        "same": dirs[other],
        "contains": dirs["tmp"],
    }[relation]
    with pytest.raises(ValidationError) as info:
        make(dirs, **{field: folder})
    assert field in str(info.value)


def test_load_settings_names_the_field_and_never_the_path(
    monkeypatch: pytest.MonkeyPatch, dirs: dict[str, Path]
) -> None:  # [NEG]
    monkeypatch.setenv("GRC_VAULT_DIR", str(dirs["vault"]))
    monkeypatch.setenv("GRC_EXPORT_DIR", str(dirs["vault"] / "inside"))
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    assert str(info.value) == "Invalid configuration for: export_dir"


@pytest.mark.parametrize("field", ["questionnaire_dir", "export_dir"])
@pytest.mark.parametrize("target", ["vault", "index"])
def test_a_link_into_the_vault_or_index_is_rejected_on_the_resolved_path(  # [NEG]
    dirs: dict[str, Path], symlink_support: None, field: str, target: str
) -> None:
    # Lexically the folder is outside both, so Settings accepts it; resolved, it is inside.
    link = dirs["tmp"] / f"{field}-link"
    link.symlink_to(dirs[target] / "inside", target_is_directory=True)
    settings = make(dirs, **{field: link})
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(settings)
    assert str(info.value) == f"Invalid configuration for: {field}"
    assert str(link) not in str(info.value)


@pytest.mark.parametrize("field", ["questionnaire_dir", "export_dir"])
def test_a_missing_folder_is_rejected_without_its_path(  # [NEG]
    dirs: dict[str, Path], field: str
) -> None:
    missing = dirs["tmp"] / "does-not-exist"
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(make(dirs, **{field: missing}))
    assert str(info.value) == f"Invalid configuration for: {field}"
    assert "does-not-exist" not in str(info.value)
