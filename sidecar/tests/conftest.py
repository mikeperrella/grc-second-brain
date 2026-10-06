import os
from collections.abc import Iterator
from pathlib import Path

import pytest

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
