import contextlib
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


def test_gethostbyname_blocks_every_name_even_localhost() -> None:  # [NEG]
    for name in ("example.com", "localhost"):
        with pytest.raises(EgressBlockedError):
            socket.gethostbyname(name)


def test_gethostbyname_allows_an_ip_literal() -> None:
    assert socket.gethostbyname("127.0.0.1") == "127.0.0.1"


def test_gethostbyname_ex_blocks_every_name_even_localhost() -> None:  # [NEG]
    for name in ("example.com", "localhost"):
        with pytest.raises(EgressBlockedError):
            socket.gethostbyname_ex(name)


def test_gethostbyname_ex_allows_an_ip_literal() -> None:
    assert "127.0.0.1" in socket.gethostbyname_ex("127.0.0.1")[2]


@pytest.mark.parametrize("target", ["example.com", "localhost", "192.0.2.1", "2001:db8::1"])
def test_gethostbyaddr_blocks_names_and_non_loopback_addresses(target: str) -> None:  # [NEG]
    with pytest.raises(EgressBlockedError):
        socket.gethostbyaddr(target)


def test_gethostbyaddr_does_not_block_a_loopback_literal() -> None:
    # An OSError means the system has no name for it: that answer came from the OS, not the guard.
    with contextlib.suppress(OSError):
        socket.gethostbyaddr("127.0.0.1")


@pytest.mark.parametrize(
    "sockaddr",
    [("192.0.2.1", 80), ("example.com", 80), ("localhost", 80), ("2001:db8::1", 80, 0, 0)],
)
def test_getnameinfo_blocks_names_and_non_loopback_addresses(
    sockaddr: tuple[str | int, ...],
) -> None:  # [NEG]
    with pytest.raises(EgressBlockedError):
        socket.getnameinfo(sockaddr, 0)  # type: ignore[arg-type]


def test_getnameinfo_blocks_a_non_loopback_address_even_when_numeric() -> None:  # [NEG]
    flags = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
    with pytest.raises(EgressBlockedError):
        socket.getnameinfo(("192.0.2.1", 80), flags)


def test_getnameinfo_allows_a_loopback_literal() -> None:
    flags = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
    assert socket.getnameinfo(("127.0.0.1", 80), flags) == ("127.0.0.1", "80")


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
