import socket

import pytest

from . import egress_guard
from .egress_guard import EgressBlockedError


def test_non_loopback_ipv4_connect_blocked() -> None:  # [NEG]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(EgressBlockedError):
        s.connect(("192.0.2.1", 80))


def test_non_loopback_ipv6_connect_blocked() -> None:  # [NEG]
    with (
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s,
        pytest.raises(EgressBlockedError),
    ):
        s.connect(("2001:db8::1", 80, 0, 0))


def test_hostname_connect_blocked() -> None:  # [NEG]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(EgressBlockedError):
        s.connect(("example.com", 80))


def test_connect_ex_non_loopback_blocked() -> None:  # [NEG]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(EgressBlockedError):
        s.connect_ex(("192.0.2.1", 80))


# create_connection only closes its socket on OSError. Our error is deliberately not one,
# so the stdlib leaks the half-open socket; ignore that warning for this one test.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.filterwarnings("ignore::ResourceWarning")
def test_create_connection_blocked() -> None:  # [NEG]
    with pytest.raises(EgressBlockedError):
        socket.create_connection(("192.0.2.1", 80), timeout=1)


def test_getaddrinfo_hostname_blocked() -> None:  # [NEG]
    with pytest.raises(EgressBlockedError):
        socket.getaddrinfo("example.com", 80)


def test_ipv4_mapped_ipv6() -> None:  # [NEG] for the blocked half
    assert not egress_guard.is_allowed_address(socket.AF_INET6, ("::ffff:8.8.8.8", 80, 0, 0))
    assert egress_guard.is_allowed_address(socket.AF_INET6, ("::ffff:127.0.0.1", 80, 0, 0))
    with (
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s,
        pytest.raises(EgressBlockedError),
    ):
        s.connect(("::ffff:8.8.8.8", 80, 0, 0))


def test_loopback_is_allowed() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(server.getsockname())
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo(None, 80)
    assert not egress_guard.is_allowed_address(-1, ("127.0.0.1", 80))


def test_af_unix_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # AF_UNIX does not exist on Windows. Define it for this test (raising=False) so the
    # assertion runs on every platform instead of being skipped or silently passing.
    monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)
    assert egress_guard.is_allowed_address(1, "x.sock")


def test_guard_is_active_by_default() -> None:
    assert egress_guard.is_installed()


def test_nested_install_uninstall_keeps_session_guard() -> None:
    uninstall = egress_guard.install()
    uninstall()
    assert egress_guard.is_installed()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(EgressBlockedError):
        s.connect(("192.0.2.1", 80))


def test_egress_error_is_not_an_oserror() -> None:  # [NEG]
    assert not issubclass(EgressBlockedError, OSError)
