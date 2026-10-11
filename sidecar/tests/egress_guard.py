"""Test-time egress guard (CLAUDE.md 3.8).

Patches the socket entry points so any attempt to reach a non-loopback address
raises EgressBlockedError. DNS lookups count as egress, so all six lookup functions
are guarded:

- getaddrinfo, gethostbyname, gethostbyname_ex (name to address): only a missing host
  or an IP literal (which needs no DNS) is allowed. Every name raises, including
  "localhost".
- gethostbyaddr, getnameinfo (address to name): a reverse lookup of a non-loopback
  address is a real DNS query, so only a loopback IP literal is allowed. A name, or
  any other address, raises, even with NI_NUMERICHOST set (stricter than needed, on
  purpose).

EgressBlockedError deliberately does not subclass OSError. Libraries such as
socket.create_connection catch OSError and try the next address, which would
swallow the violation; a plain Exception propagates and fails the test.

Not covered (stated plainly): code that reaches the network without going through
these Python socket functions (native extensions). Child processes started by the
sidecar spawn tests load this module through tests/_child_guard/sitecustomize.py.
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


def _is_allowed_reverse_lookup(address: object) -> bool:
    ip = _parse_ip(address)
    return ip is not None and ip.is_loopback


def _mark(func: Callable[..., Any]) -> None:
    setattr(func, _MARK, True)


def is_installed() -> bool:
    return bool(getattr(socket.socket.connect, _MARK, False)) and all(
        bool(getattr(func, _MARK, False))
        for func in (
            socket.getaddrinfo,
            socket.gethostbyname,
            socket.gethostbyname_ex,
            socket.gethostbyaddr,
            socket.getnameinfo,
        )
    )


def install() -> Callable[[], None]:
    """Install the guard and return a function that restores the previous state."""
    prev_connect = socket.socket.connect
    prev_connect_ex = socket.socket.connect_ex
    prev_getaddrinfo = socket.getaddrinfo
    prev_gethostbyname = socket.gethostbyname
    prev_gethostbyname_ex = socket.gethostbyname_ex
    prev_gethostbyaddr = socket.gethostbyaddr
    prev_getnameinfo = socket.getnameinfo

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

    def guarded_gethostbyname(host: Any) -> Any:
        if not _is_allowed_lookup(host):
            raise EgressBlockedError("blocked name lookup")
        return prev_gethostbyname(host)

    def guarded_gethostbyname_ex(host: Any) -> Any:
        if not _is_allowed_lookup(host):
            raise EgressBlockedError("blocked name lookup")
        return prev_gethostbyname_ex(host)

    def guarded_gethostbyaddr(ip_address: Any) -> Any:
        if not _is_allowed_reverse_lookup(ip_address):
            raise EgressBlockedError("blocked reverse lookup")
        return prev_gethostbyaddr(ip_address)

    def guarded_getnameinfo(sockaddr: Any, flags: Any) -> Any:
        host = sockaddr[0] if isinstance(sockaddr, tuple | list) and sockaddr else None
        if not _is_allowed_reverse_lookup(host):
            raise EgressBlockedError("blocked reverse lookup")
        return prev_getnameinfo(sockaddr, flags)

    for func in (
        guarded_connect,
        guarded_connect_ex,
        guarded_getaddrinfo,
        guarded_gethostbyname,
        guarded_gethostbyname_ex,
        guarded_gethostbyaddr,
        guarded_getnameinfo,
    ):
        _mark(func)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo
    socket.gethostbyname = guarded_gethostbyname
    socket.gethostbyname_ex = guarded_gethostbyname_ex
    socket.gethostbyaddr = guarded_gethostbyaddr
    socket.getnameinfo = guarded_getnameinfo

    def uninstall() -> None:
        socket.socket.connect = prev_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = prev_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = prev_getaddrinfo
        socket.gethostbyname = prev_gethostbyname
        socket.gethostbyname_ex = prev_gethostbyname_ex
        socket.gethostbyaddr = prev_gethostbyaddr
        socket.getnameinfo = prev_getnameinfo

    return uninstall
