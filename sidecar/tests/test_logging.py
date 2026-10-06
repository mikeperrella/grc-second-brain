import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from grc_sidecar.config import Settings
from grc_sidecar.errors import ConfigurationError
from grc_sidecar.logging_config import HANDLER_NAME, LOG_FILE_NAME, configure_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    # Close our file handler after each test so Windows can delete the temp directory.
    root = logging.getLogger()
    level = root.level
    yield
    for handler in list(root.handlers):
        if handler.get_name() == HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def _our_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if h.get_name() == HANDLER_NAME]


def _settings(log_dir: Path, level: str = "INFO") -> Settings:
    return Settings(log_dir=log_dir, log_level=level)  # type: ignore[arg-type]


def test_record_lands_in_file_and_nested_dir_is_created(tmp_path: Path) -> None:
    log_dir = tmp_path / "x" / "y" / "logs"
    configure_logging(_settings(log_dir))
    logging.getLogger("grc.test").info("hello-record")
    _our_handlers()[0].flush()
    assert "hello-record" in (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")


def test_configure_twice_leaves_one_handler(tmp_path: Path) -> None:
    configure_logging(_settings(tmp_path / "a"))
    configure_logging(_settings(tmp_path / "b"))
    assert len(_our_handlers()) == 1


def test_nothing_on_stdout_or_stderr_and_handler_is_a_file_handler(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # [NEG]
    configure_logging(_settings(tmp_path / "logs"))
    logging.getLogger("grc.test").error("should-not-print")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    (handler,) = _our_handlers()
    assert isinstance(handler, logging.FileHandler)


def test_debug_not_written_at_default_level(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    configure_logging(_settings(log_dir))
    logging.getLogger("grc.test").debug("debug-detail")
    logging.getLogger("grc.test").info("info-detail")
    _our_handlers()[0].flush()
    text = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "debug-detail" not in text
    assert "info-detail" in text


def test_log_dir_that_is_a_file_rejected(tmp_path: Path) -> None:  # [NEG]
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        configure_logging(_settings(not_a_dir))
