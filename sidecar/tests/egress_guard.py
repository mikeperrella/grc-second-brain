"""Test-time egress guard (CLAUDE.md 3.8).

Patches the socket entry points so any attempt to reach a non-loopback address
raises EgressBlockedError. DNS lookups count as egress, so getaddrinfo is guarded
too: only a missing host or an IP literal (which needs no DNS) is allowed.

EgressBlockedError deliberately does not subclass OSError. Libraries such as
socket.create_connection catch OSError and try the next address, which would
swallow the violation; a plain Exception propagates and fails the test.

Not covered (stated plainly): gethostbyname*, gethostbyaddr, getnameinfo, and child
processes. Child-process coverage arrives with the sidecar spawn tests in M1b.
"""

import ipaddress
import socket
from collections.abc import Callable
from typing import Any

_MARK = "_grc_egress_guard"


class EgressBlockedError(Exception):
    """Raised when code tries to reach a non-loopback network address."""


def _decode_host(host: object) -> str | None:
    if isinstance(host, bytes):
        try:
            return host.decode("ascii")
        except UnicodeDecodeError:
            return None
    return host if isinstance(host, str) else None


def _parse_ip(host: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = _decode_host(host)
    if text is None:
        return None
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_allowed_address(family: int, address: object) -> bool:
    """Return True only for loopback IP literals and AF_UNIX sockets."""
    if family == getattr(socket, "AF_UNIX", None):
        return True
    if family not in (socket.AF_INET, socket.AF_INET6):
        return False
    if not isinstance(address, tuple | list) or not address:
        return False
    ip = _parse_ip(address[0])
    return ip is not None and ip.is_loopback


def _is_allowed_lookup(host: object) -> bool:
    return host is None or _parse_ip(host) is not None


def _mark(func: Callable[..., Any]) -> None:
    setattr(func, _MARK, True)


def is_installed() -> bool:
    return bool(getattr(socket.socket.connect, _MARK, False)) and bool(
        getattr(socket.getaddrinfo, _MARK, False)
    )


def install() -> Callable[[], None]:
    """Install the guard and return a function that restores the previous state."""
    prev_connect = socket.socket.connect
    prev_connect_ex = socket.socket.connect_ex
    prev_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: Any) -> None:
        if not is_allowed_address(self.family, address):
            raise EgressBlockedError("blocked non-loopback connect")
        prev_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        if not is_allowed_address(self.family, address):
            raise EgressBlockedError("blocked non-loopback connect_ex")
        return prev_connect_ex(self, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_allowed_lookup(host):
            raise EgressBlockedError("blocked name lookup")
        return prev_getaddrinfo(host, *args, **kwargs)

    for func in (guarded_connect, guarded_connect_ex, guarded_getaddrinfo):
        _mark(func)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo

    def uninstall() -> None:
        socket.socket.connect = prev_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = prev_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = prev_getaddrinfo

    return uninstall
