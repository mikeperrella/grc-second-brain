"""The ledger folder (ADR-017 decision 7): absolute, optional, and never equal to, inside or
containing the vault, the index, the questionnaire folder, the export folder or the import folder.
The ledger cannot be rebuilt, so it must never sit next to derived state, answers or exports.
Checked when settings load and again on the resolved paths (a link can fool the first check), in
both directions."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from grc_sidecar.config import Settings, load_settings, validate_runtime_dirs
from grc_sidecar.errors import ConfigurationError, LedgerRootNotConfiguredError
from grc_sidecar.repositories.sqlite_ledger_repo import LEDGER_FILE_NAME, ledger_path

pytestmark = pytest.mark.usefixtures("clean_grc_env")

OTHERS = ["vault", "index", "questionnaires", "exports", "imports"]
OTHER_FIELDS = {
    "vault": "vault_dir",
    "index": "index_dir",
    "questionnaires": "questionnaire_dir",
    "exports": "export_dir",
    "imports": "import_dir",
}


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    found = {name: tmp_path / name for name in [*OTHERS, "ledger"]}
    for folder in found.values():
        (folder / "inside").mkdir(parents=True)
    found["tmp"] = tmp_path
    return found


def make(dirs: dict[str, Path], **overrides: Path | None) -> Settings:
    values: dict[str, Path | None] = {
        "log_dir": dirs["tmp"] / "logs",
        "vault_dir": dirs["vault"],
        "index_dir": dirs["index"],
        "questionnaire_dir": dirs["questionnaires"],
        "export_dir": dirs["exports"],
        "import_dir": dirs["imports"],
        "ledger_dir": dirs["ledger"],
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_the_ledger_folder_is_unset_by_default(tmp_path: Path) -> None:
    assert Settings(log_dir=tmp_path / "logs").ledger_dir is None


def test_with_no_ledger_folder_there_is_no_ledger_path(tmp_path: Path) -> None:  # [NEG]
    with pytest.raises(LedgerRootNotConfiguredError):
        ledger_path(Settings(log_dir=tmp_path / "logs"))


def test_the_ledger_file_lives_directly_in_the_ledger_folder(dirs: dict[str, Path]) -> None:
    assert ledger_path(make(dirs)) == dirs["ledger"] / LEDGER_FILE_NAME


def test_the_ledger_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, dirs: dict[str, Path]
) -> None:
    monkeypatch.setenv("GRC_LEDGER_DIR", str(dirs["ledger"]))
    assert load_settings().ledger_dir == dirs["ledger"]


def test_a_folder_apart_from_the_others_is_accepted(dirs: dict[str, Path]) -> None:
    settings = make(dirs)
    assert settings.ledger_dir == dirs["ledger"]
    assert validate_runtime_dirs(settings).ledger_dir == dirs["ledger"].resolve()


def test_a_relative_folder_is_rejected(dirs: dict[str, Path]) -> None:  # [NEG]
    with pytest.raises(ValidationError):
        make(dirs, ledger_dir=Path("relative/folder"))


@pytest.mark.parametrize("other", OTHERS)
@pytest.mark.parametrize("relation", ["inside", "same", "contains"])
def test_a_folder_overlapping_any_other_is_rejected_when_settings_load(  # [NEG]
    dirs: dict[str, Path], other: str, relation: str
) -> None:
    folder = {
        "inside": dirs[other] / "inside",
        "same": dirs[other],
        "contains": dirs["tmp"],
    }[relation]
    with pytest.raises(ValidationError) as info:
        make(dirs, ledger_dir=folder)
    assert "ledger_dir" in str(info.value)


@pytest.mark.parametrize("other", OTHERS)
def test_another_folder_inside_the_ledger_folder_is_rejected(  # [NEG]
    dirs: dict[str, Path], other: str
) -> None:
    # The ledger folder is declared after the others, so this is the "contains" direction seen
    # from the other folder's side: it must still name ledger_dir.
    inside_ledger = dirs["ledger"] / "inside"
    with pytest.raises(ValidationError) as info:
        make(dirs, **{OTHER_FIELDS[other]: inside_ledger})
    assert "ledger_dir" in str(info.value)


def test_load_settings_names_the_field_and_never_the_path(
    monkeypatch: pytest.MonkeyPatch, dirs: dict[str, Path]
) -> None:  # [NEG]
    monkeypatch.setenv("GRC_INDEX_DIR", str(dirs["index"]))
    monkeypatch.setenv("GRC_LEDGER_DIR", str(dirs["index"] / "inside"))
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    assert str(info.value) == "Invalid configuration for: ledger_dir"


@pytest.mark.parametrize("other", OTHERS)
def test_a_ledger_folder_that_is_a_link_into_another_folder_is_rejected_when_resolved(  # [NEG]
    dirs: dict[str, Path], symlink_support: None, other: str
) -> None:
    link = dirs["tmp"] / "ledger-link"
    link.symlink_to(dirs[other] / "inside", target_is_directory=True)
    settings = make(dirs, ledger_dir=link)  # lexically apart, so settings accept it
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(settings)
    assert str(info.value) == "Invalid configuration for: ledger_dir"
    assert str(link) not in str(info.value)


@pytest.mark.parametrize("other", OTHERS)
def test_another_folder_that_is_a_link_into_the_ledger_folder_is_rejected_when_resolved(  # [NEG]
    dirs: dict[str, Path], symlink_support: None, other: str
) -> None:
    link = dirs["tmp"] / f"{other}-link"
    link.symlink_to(dirs["ledger"] / "inside", target_is_directory=True)
    settings = make(dirs, **{OTHER_FIELDS[other]: link})
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(settings)
    assert "Invalid configuration for:" in str(info.value)
    assert str(link) not in str(info.value)


def test_a_missing_ledger_folder_is_rejected_without_its_path(  # [NEG]
    dirs: dict[str, Path],
) -> None:
    missing = dirs["tmp"] / "does-not-exist"
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(make(dirs, ledger_dir=missing))
    assert str(info.value) == "Invalid configuration for: ledger_dir"
    assert "does-not-exist" not in str(info.value)
