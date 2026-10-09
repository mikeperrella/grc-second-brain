import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from grc_sidecar import __main__ as sidecar_entry
from grc_sidecar.security import process_guard

from . import egress_guard


def pytest_configure(config: pytest.Config) -> None:
    # Telemetry stays off even if a transitive package ever starts an SDK.
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["OTEL_TRACES_EXPORTER"] = "none"


@pytest.fixture(scope="session", autouse=True)
def _egress_guard() -> Iterator[None]:
    uninstall = egress_guard.install()
    yield
    uninstall()


@pytest.fixture(scope="session", autouse=True)
def _process_guard_never_installed_in_pytest() -> Iterator[None]:
    """An audit hook cannot be removed, so it must never be installed in the pytest process.

    Tests that call `main()` in-process get a recorder instead (next fixture); the real guard is
    exercised only in child processes. If one slipped through, fail the session instead of
    letting every later test run under a hook that vetoes process creation.
    """
    yield
    assert not process_guard.is_installed(), "the real process guard was installed in pytest"


@pytest.fixture(autouse=True)
def _no_real_process_guard_in_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sidecar_entry, "install_process_guard", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_bind_in_main(monkeypatch: pytest.MonkeyPatch) -> None:
    # In-process tests of main() replace serve(); a real bound socket would only leak.
    monkeypatch.setattr(sidecar_entry, "bind_loopback_socket", lambda: SimpleNamespace(close=int))


@pytest.fixture(autouse=True)
def _no_real_stdout_seal_in_main(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sealing redirects file descriptor 1 of the pytest process itself and cannot be undone.
    monkeypatch.setattr(sidecar_entry, "seal_stdout", lambda: SimpleNamespace(close=int))


@pytest.fixture
def clean_grc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.upper().startswith("GRC_")]:
        monkeypatch.delenv(name)


@pytest.fixture
def symlink_support(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Fail (never skip) when symlinks cannot be created, so security tests always run."""
    base = tmp_path_factory.mktemp("symlink_probe")
    target = base / "target.txt"
    target.write_text("x", encoding="utf-8")
    try:
        os.symlink(target, Path(base / "link.txt"))
    except OSError:
        pytest.fail(
            "Cannot create symlinks. On Windows enable Developer Mode "
            "(Settings > System > For developers) and restart the terminal/editor."
        )
