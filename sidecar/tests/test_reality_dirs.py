"""The import folder (ADR-016): absolute, and never equal to, inside or containing the vault, the
index, the questionnaire folder or the export folder. An export holds personal data, so it must not
sit next to derived state or answers. Checked when settings load and again on the resolved paths
(a link can fool the first check), in both directions."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from grc_sidecar.config import Settings, load_settings, validate_runtime_dirs
from grc_sidecar.errors import ConfigurationError
from grc_sidecar.reality.runtime import build_reality_runtime

pytestmark = pytest.mark.usefixtures("clean_grc_env")

OTHERS = ["vault", "index", "questionnaires", "exports"]
OTHER_FIELDS = {
    "vault": "vault_dir",
    "index": "index_dir",
    "questionnaires": "questionnaire_dir",
    "exports": "export_dir",
}


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    found = {name: tmp_path / name for name in [*OTHERS, "imports"]}
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
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_the_import_folder_is_unset_by_default(tmp_path: Path) -> None:
    assert Settings(log_dir=tmp_path / "logs").import_dir is None


def test_a_folder_apart_from_the_others_is_accepted(dirs: dict[str, Path]) -> None:
    settings = make(dirs)
    assert settings.import_dir == dirs["imports"]
    assert validate_runtime_dirs(settings).import_dir == dirs["imports"].resolve()


def test_a_relative_folder_is_rejected(dirs: dict[str, Path]) -> None:  # [NEG]
    with pytest.raises(ValidationError):
        make(dirs, import_dir=Path("relative/folder"))


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
        make(dirs, import_dir=folder)
    assert "import_dir" in str(info.value)


@pytest.mark.parametrize("other", OTHERS)
def test_another_folder_inside_the_import_folder_is_rejected(  # [NEG]
    dirs: dict[str, Path], other: str
) -> None:
    # The import folder is declared after the others, so this is the "contains" direction seen
    # from the other folder's side: it must still name import_dir.
    inside_imports = dirs["imports"] / "inside"
    with pytest.raises(ValidationError) as info:
        make(dirs, **{OTHER_FIELDS[other]: inside_imports})
    assert "import_dir" in str(info.value)


def test_load_settings_names_the_field_and_never_the_path(
    monkeypatch: pytest.MonkeyPatch, dirs: dict[str, Path]
) -> None:  # [NEG]
    monkeypatch.setenv("GRC_VAULT_DIR", str(dirs["vault"]))
    monkeypatch.setenv("GRC_IMPORT_DIR", str(dirs["vault"] / "inside"))
    with pytest.raises(ConfigurationError) as info:
        load_settings()
    assert str(info.value) == "Invalid configuration for: import_dir"


@pytest.mark.parametrize("other", OTHERS)
def test_an_import_folder_that_is_a_link_into_another_folder_is_rejected_when_resolved(  # [NEG]
    dirs: dict[str, Path], symlink_support: None, other: str
) -> None:
    link = dirs["tmp"] / "import-link"
    link.symlink_to(dirs[other] / "inside", target_is_directory=True)
    settings = make(dirs, import_dir=link)  # lexically apart, so settings accept it
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(settings)
    assert str(info.value) == "Invalid configuration for: import_dir"
    assert str(link) not in str(info.value)


@pytest.mark.parametrize("other", OTHERS)
def test_another_folder_that_is_a_link_into_the_import_folder_is_rejected_when_resolved(  # [NEG]
    dirs: dict[str, Path], symlink_support: None, other: str
) -> None:
    link = dirs["tmp"] / f"{other}-link"
    link.symlink_to(dirs["imports"] / "inside", target_is_directory=True)
    settings = make(dirs, **{OTHER_FIELDS[other]: link})
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(settings)
    assert "Invalid configuration for:" in str(info.value)
    assert str(link) not in str(info.value)


def test_a_missing_import_folder_is_rejected_without_its_path(  # [NEG]
    dirs: dict[str, Path],
) -> None:
    missing = dirs["tmp"] / "does-not-exist"
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(make(dirs, import_dir=missing))
    assert str(info.value) == "Invalid configuration for: import_dir"
    assert "does-not-exist" not in str(info.value)


def test_the_reality_runtime_needs_both_the_import_and_the_vault_folder(
    dirs: dict[str, Path],
) -> None:
    assert build_reality_runtime(make(dirs)) is not None
    assert build_reality_runtime(make(dirs, import_dir=None)) is None
    assert build_reality_runtime(make(dirs, vault_dir=None)) is None
