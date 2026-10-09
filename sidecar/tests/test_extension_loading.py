"""The extension allowlist in the audit hook, called directly (no hook is installed in pytest).

The same rule running under the real guard is tested in child processes in test_process_guard.py.
"""

import os
import sys
from pathlib import Path

import pytest

from grc_sidecar.errors import ExtensionLoadBlockedError
from grc_sidecar.repositories.sqlite_vec_repo import vec_extension_path
from grc_sidecar.security import process_guard

EVENT = "sqlite3.load_extension"
CONNECTION = object()  # the first argument of the real event; the hook never looks at it


@pytest.fixture
def approved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    path = str(tmp_path / "ext" / "vec0")
    monkeypatch.setattr(
        process_guard, "_approved_extension", process_guard._normalize_extension_path(path)
    )
    return path


def test_the_exact_approved_path_is_allowed(approved: str) -> None:
    process_guard._audit_hook(EVENT, (CONNECTION, approved))


def test_a_path_that_normalizes_to_the_approved_path_is_allowed(approved: str) -> None:
    sep = os.sep
    respelled = approved.replace(f"{sep}ext{sep}", f"{sep}ext{sep}.{sep}")
    process_guard._audit_hook(EVENT, (CONNECTION, respelled))
    process_guard._audit_hook(
        EVENT, (CONNECTION, approved.replace("ext", f"nested{sep}..{sep}ext"))
    )


def test_a_different_absolute_path_is_blocked(approved: str, tmp_path: Path) -> None:  # [NEG]
    with pytest.raises(ExtensionLoadBlockedError):
        process_guard._audit_hook(EVENT, (CONNECTION, str(tmp_path / "other" / "vec0")))


def test_a_sibling_with_the_same_prefix_is_blocked(approved: str) -> None:  # [NEG]
    with pytest.raises(ExtensionLoadBlockedError):
        process_guard._audit_hook(EVENT, (CONNECTION, approved + "2"))


def test_a_relative_path_is_blocked_even_if_it_names_the_same_file(approved: str) -> None:  # [NEG]
    with pytest.raises(ExtensionLoadBlockedError):
        process_guard._audit_hook(EVENT, (CONNECTION, os.path.relpath(approved)))


@pytest.mark.parametrize("bad_args", [(), (CONNECTION,), (CONNECTION, None), (CONNECTION, b"vec0")])
def test_malformed_event_arguments_are_blocked(
    approved: str, bad_args: tuple[object, ...]
) -> None:  # [NEG]
    with pytest.raises(ExtensionLoadBlockedError):
        process_guard._audit_hook(EVENT, bad_args)


def test_with_no_approved_path_even_the_real_extension_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # [NEG]
    monkeypatch.setattr(process_guard, "_approved_extension", None)
    with pytest.raises(ExtensionLoadBlockedError):
        process_guard._audit_hook(EVENT, (CONNECTION, str(vec_extension_path())))


def test_letter_case_is_ignored_on_windows_only(approved: str) -> None:
    # Windows paths are case-insensitive, so case is folded there. On Linux they are not, so a
    # different spelling is a different file and must be blocked. The test runs on both.
    if sys.platform == "win32":
        process_guard._audit_hook(EVENT, (CONNECTION, approved.upper()))
    else:
        with pytest.raises(ExtensionLoadBlockedError):  # [NEG]
            process_guard._audit_hook(EVENT, (CONNECTION, approved.upper()))


@pytest.mark.parametrize(
    "event", ["sqlite3.connect", "sqlite3.enable_load_extension", "sqlite3.execute"]
)
def test_other_sqlite_events_are_never_vetoed(approved: str, event: str) -> None:
    process_guard._audit_hook(event, (CONNECTION, True))


def test_a_blocked_load_is_a_permission_error_with_a_fixed_message() -> None:
    assert issubclass(ExtensionLoadBlockedError, PermissionError)
    assert str(ExtensionLoadBlockedError()) == "Loading this extension is not allowed"


def test_the_vendored_extension_path_is_absolute_and_names_the_vec0_library() -> None:
    path = vec_extension_path()
    assert path.is_absolute()
    assert path.name == "vec0"
    assert path.with_name("vec0" + (".dll" if sys.platform == "win32" else ".so")).is_file()
