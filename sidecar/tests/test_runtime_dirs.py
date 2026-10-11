"""`validate_runtime_dirs`: the vault and index folders the host hands to the sidecar (M4b).

The symlink tests run on every platform (they create real symlinks, and fail rather than skip when
that is not possible: see the `symlink_support` fixture). Only the junction variant is
Windows-only, like `test_paths.py`.
"""

import secrets
import sys
from pathlib import Path

import pytest

from grc_sidecar.config import Settings, validate_runtime_dirs
from grc_sidecar.errors import ConfigurationError

from .childproc import minimal_env, run_python, running_python
from .leakcheck import assert_no_leak

pytestmark = pytest.mark.usefixtures("clean_grc_env")

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path behavior")


def settings_for(vault: Path | None, index: Path | None, tmp_path: Path) -> Settings:
    return Settings(log_dir=tmp_path / "logs", vault_dir=vault, index_dir=index)


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    vault = tmp_path / "vault"
    (vault / "inside").mkdir(parents=True)
    index = tmp_path / "index"
    index.mkdir()
    return {"vault": vault, "index": index, "tmp": tmp_path}


def test_valid_directories_are_accepted_and_returned_resolved(dirs: dict[str, Path]) -> None:
    result = validate_runtime_dirs(settings_for(dirs["vault"], dirs["index"], dirs["tmp"]))
    assert result.vault_dir == dirs["vault"].resolve()
    assert result.index_dir == dirs["index"].resolve()


def test_unset_directories_are_fine(dirs: dict[str, Path]) -> None:
    result = validate_runtime_dirs(settings_for(None, None, dirs["tmp"]))
    assert result.vault_dir is None
    assert result.index_dir is None
    only_index = validate_runtime_dirs(settings_for(None, dirs["index"], dirs["tmp"]))
    assert only_index.vault_dir is None
    assert only_index.index_dir == dirs["index"].resolve()


def test_a_relative_directory_is_rejected_by_the_settings(dirs: dict[str, Path]) -> None:  # [NEG]
    with pytest.raises(ValueError, match="absolute"):
        Settings(log_dir=dirs["tmp"] / "logs", vault_dir=Path("vault"))
    with pytest.raises(ValueError, match="absolute"):
        Settings(log_dir=dirs["tmp"] / "logs", index_dir=Path("index"))


@pytest.mark.parametrize("name", ["vault_dir", "index_dir"])
def test_a_missing_directory_is_rejected_without_its_path(  # [NEG]
    name: str, dirs: dict[str, Path]
) -> None:
    missing = dirs["tmp"] / "SECRET-MISSING-FOLDER-NAME"
    kwargs = {"vault_dir": dirs["vault"], "index_dir": dirs["index"], name: missing}
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(Settings(log_dir=dirs["tmp"] / "logs", **kwargs))
    assert str(info.value) == f"Invalid configuration for: {name}"
    assert_no_leak(info.value, "SECRET-MISSING-FOLDER-NAME")


@pytest.mark.parametrize("name", ["vault_dir", "index_dir"])
def test_a_file_is_not_a_directory(name: str, dirs: dict[str, Path]) -> None:  # [NEG]
    a_file = dirs["tmp"] / "a-file.txt"
    a_file.write_text("x", encoding="utf-8")
    kwargs = {"vault_dir": dirs["vault"], "index_dir": dirs["index"], name: a_file}
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(Settings(log_dir=dirs["tmp"] / "logs", **kwargs))
    assert str(info.value) == f"Invalid configuration for: {name}"


# ----- symlinks: every platform ----------------------------------------------------------------


def test_symlinked_vault_root_resolves_to_its_real_path(
    dirs: dict[str, Path], symlink_support: None
) -> None:
    # The scanner accepts a vault root that is itself a link (test_scan.py), and uses what it
    # points to. The validated setting must be that real path, not the link.
    link = dirs["tmp"] / "vault-link"
    link.symlink_to(dirs["vault"], target_is_directory=True)
    result = validate_runtime_dirs(settings_for(link, dirs["index"], dirs["tmp"]))
    assert result.vault_dir == dirs["vault"].resolve()
    assert result.vault_dir != link


def test_index_symlinked_into_the_vault_is_rejected(  # [NEG]
    dirs: dict[str, Path], symlink_support: None
) -> None:
    # Lexically the index is outside the vault, so `Settings` accepts it; resolved, it is inside.
    link = dirs["tmp"] / "index-link"
    link.symlink_to(dirs["vault"] / "inside", target_is_directory=True)
    settings = settings_for(dirs["vault"], link, dirs["tmp"])  # no error from the lexical check
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(settings)
    assert str(info.value) == "Invalid configuration for: index_dir"


def test_index_that_is_a_link_to_the_vault_root_is_rejected(  # [NEG]
    dirs: dict[str, Path], symlink_support: None
) -> None:
    link = dirs["tmp"] / "index-is-the-vault"
    link.symlink_to(dirs["vault"], target_is_directory=True)
    with pytest.raises(ConfigurationError):
        validate_runtime_dirs(settings_for(dirs["vault"], link, dirs["tmp"]))


def test_vault_symlinked_into_the_index_is_rejected(  # [NEG]
    dirs: dict[str, Path], symlink_support: None
) -> None:
    (dirs["index"] / "stuff").mkdir()
    link = dirs["tmp"] / "vault-link"
    link.symlink_to(dirs["index"] / "stuff", target_is_directory=True)
    with pytest.raises(ConfigurationError):
        validate_runtime_dirs(settings_for(link, dirs["index"], dirs["tmp"]))


def test_a_link_to_a_missing_target_is_rejected(  # [NEG]
    dirs: dict[str, Path], symlink_support: None
) -> None:
    link = dirs["tmp"] / "dangling"
    link.symlink_to(dirs["tmp"] / "nowhere", target_is_directory=True)
    with pytest.raises(ConfigurationError):
        validate_runtime_dirs(settings_for(link, dirs["index"], dirs["tmp"]))


# ----- junctions: Windows only -----------------------------------------------------------------


@windows_only
def test_index_junctioned_into_the_vault_is_rejected(dirs: dict[str, Path]) -> None:  # [NEG]
    import _winapi

    junction = dirs["tmp"] / "index-junction"
    _winapi.CreateJunction(str(dirs["vault"] / "inside"), str(junction))
    with pytest.raises(ConfigurationError) as info:
        validate_runtime_dirs(settings_for(dirs["vault"], junction, dirs["tmp"]))
    assert str(info.value) == "Invalid configuration for: index_dir"


# ----- the real process ------------------------------------------------------------------------


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return minimal_env(
        {"GRC_AUTH_TOKEN": secrets.token_hex(32), "GRC_LOG_DIR": str(tmp_path / "logs"), **extra}
    )


def test_a_sidecar_with_a_missing_vault_exits_2_with_one_fixed_line(  # [NEG]
    dirs: dict[str, Path],
) -> None:
    env = _env(dirs["tmp"], GRC_VAULT_DIR=str(dirs["tmp"] / "SECRET-MISSING-FOLDER-NAME"))
    result = run_python(["-m", "grc_sidecar"], env=env, cwd=dirs["tmp"])
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "grc-sidecar: Invalid configuration for: vault_dir\n"
    assert "SECRET-MISSING-FOLDER-NAME" not in result.stdout + result.stderr


def test_a_sidecar_with_an_index_inside_the_vault_exits_2(dirs: dict[str, Path]) -> None:  # [NEG]
    inside = dirs["vault"] / "inside"
    env = _env(dirs["tmp"], GRC_VAULT_DIR=str(dirs["vault"]), GRC_INDEX_DIR=str(inside))
    result = run_python(["-m", "grc_sidecar"], env=env, cwd=dirs["tmp"])
    assert result.returncode == 2
    assert result.stderr == "grc-sidecar: Invalid configuration for: index_dir\n"


def test_a_sidecar_with_valid_directories_starts(dirs: dict[str, Path]) -> None:
    env = _env(dirs["tmp"], GRC_VAULT_DIR=str(dirs["vault"]), GRC_INDEX_DIR=str(dirs["index"]))
    stderr_path = dirs["tmp"] / "stderr.txt"
    with running_python(
        ["-m", "grc_sidecar"], env=env, cwd=dirs["tmp"], stderr_path=stderr_path
    ) as (_proc, out):
        line = out.next_line(timeout=60)
        assert line.startswith('{"event":"ready","port":')
