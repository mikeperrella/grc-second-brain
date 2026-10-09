import importlib
import os
from types import ModuleType
from typing import Any

import pytest

from grc_sidecar.errors import InvalidAuthTokenError, MissingAuthTokenError
from grc_sidecar.security.auth import AUTH_TOKEN_ENV_VAR, read_auth_token

from .childproc import run_python
from .leakcheck import assert_no_leak

pytestmark = pytest.mark.usefixtures("clean_grc_env")

entry: ModuleType = importlib.import_module("grc_sidecar.__main__")

VALID = "0123456789abcdef" * 4
PROBE = f"import os; print({AUTH_TOKEN_ENV_VAR!r} in os.environ)"

INVALID_VALUES = {
    "63 chars": VALID[:63],
    "65 chars": VALID + "a",
    "uppercase hex": VALID.upper(),
    "non-hex letters": "g" * 64,
    "leading space": " " + VALID,
    "trailing newline": VALID + "\n",
    "0x prefix": "0x" + VALID[2:],
    "non-ascii": "é" * 64,
    "arbitrary text": "not-a-token-at-all",
}


def test_valid_token_is_returned_and_removed_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, VALID)
    # Prove the probe can see the variable before we read it...
    assert run_python(["-c", PROBE]).stdout.strip() == "True"
    assert read_auth_token() == VALID
    assert AUTH_TOKEN_ENV_VAR not in os.environ
    # ...and that a real child process no longer inherits it.
    assert run_python(["-c", PROBE]).stdout.strip() == "False"


def test_second_read_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, VALID)
    read_auth_token()
    with pytest.raises(MissingAuthTokenError):
        read_auth_token()


@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_empty_token_rejected(
    value: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    if value is not None:
        monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, value)
    with pytest.raises(MissingAuthTokenError) as info:
        read_auth_token()
    assert_no_leak(info.value)


@pytest.mark.parametrize("bad", INVALID_VALUES.values(), ids=INVALID_VALUES.keys())
def test_invalid_token_rejected_removed_and_never_leaked(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:  # [NEG]
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, bad)
    with pytest.raises(InvalidAuthTokenError) as info:
        read_auth_token()
    assert AUTH_TOKEN_ENV_VAR not in os.environ  # removed even though invalid
    assert_no_leak(info.value, bad)


def test_caller_supplied_mapping_is_used_and_popped() -> None:
    env = {AUTH_TOKEN_ENV_VAR: VALID, "OTHER": "kept"}
    assert read_auth_token(env) == VALID
    assert env == {"OTHER": "kept"}


def test_main_removes_the_token_before_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, VALID)
    seen: dict[str, Any] = {}

    def fake_serve(token: str, settings: object, sock: object) -> None:
        seen["token"] = token
        seen["still_in_environ"] = AUTH_TOKEN_ENV_VAR in os.environ

    monkeypatch.setattr(entry, "serve", fake_serve)
    monkeypatch.setattr(entry, "configure_logging", lambda settings: None)
    assert entry.main() == 0
    assert seen == {"token": VALID, "still_in_environ": False}


def test_ctrl_c_exits_130_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, VALID)

    def interrupted(token: str, settings: object, sock: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(entry, "serve", interrupted)
    monkeypatch.setattr(entry, "configure_logging", lambda settings: None)
    try:
        exit_code = entry.main()
    except KeyboardInterrupt:
        # Without this, pytest would abort the whole session instead of failing one test.
        pytest.fail("main() let KeyboardInterrupt escape")
    assert exit_code == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def _forbid_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object) -> None:
        raise AssertionError("serve must not run after a startup error")

    monkeypatch.setattr(entry, "serve", fail)
    monkeypatch.setattr(entry, "configure_logging", lambda settings: None)


def test_main_missing_token_exits_2_with_fixed_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:  # [NEG]
    _forbid_serve(monkeypatch)
    assert entry.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "grc-sidecar: Authentication token is missing\n"


def test_main_invalid_token_exits_2_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:  # [NEG]
    bad = INVALID_VALUES["arbitrary text"]
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, bad)
    _forbid_serve(monkeypatch)
    assert entry.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "grc-sidecar: Authentication token is invalid\n"
    assert bad not in captured.out + captured.err


def test_main_bad_config_exits_2_and_token_is_already_gone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:  # [NEG]
    monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, VALID)
    monkeypatch.setenv("GRC_LOG_LEVEL", "verbose")
    _forbid_serve(monkeypatch)
    assert entry.main() == 2
    assert AUTH_TOKEN_ENV_VAR not in os.environ
    captured = capsys.readouterr()
    assert captured.err == "grc-sidecar: Invalid configuration for: log_level\n"
    assert VALID not in captured.out + captured.err
