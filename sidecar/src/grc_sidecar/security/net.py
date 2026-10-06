"""The single choke point for outbound HTTP (CLAUDE.md 3.2).

Every HTTP client in the sidecar is built by create_async_client. It accepts only
plain-http URLs whose host is a loopback IP literal, ignores proxy environment
variables, never follows redirects, and re-checks every request URL, because httpx
lets an absolute request URL override the client's base_url.

Hostnames (including "localhost") are rejected on purpose: a name needs DNS, and
DNS is a way for traffic to leave the machine.
"""

import ipaddress
from typing import Final

import httpx

from grc_sidecar.errors import NonLoopbackURLError

# Placeholder timeouts in seconds; revisit when retrieval and the questionnaire engine land.
DEFAULT_TIMEOUT: Final = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)

_SCHEME_PREFIX: Final = "http://"
_MAX_PORT: Final = 65535


def _is_printable_ascii(text: str) -> bool:
    return all(0x21 <= ord(ch) <= 0x7E for ch in text) and "\\" not in text


def _is_loopback_ip(host: str, *, bracketed: bool | None = None) -> bool:
    """True if host is a canonical loopback IP literal.

    bracketed=True requires IPv6, bracketed=False requires IPv4, None allows either.
    Short forms like "127.1" and numeric forms like "2130706433" fail ip_address().
    """
    if "%" in host:  # IPv6 zone ids
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if bracketed is True and ip.version != 6:
        return False
    if bracketed is False and ip.version != 4:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return False
    return ip.is_loopback


def _valid_port(text: str) -> bool:
    return text.isascii() and text.isdigit() and 1 <= int(text) <= _MAX_PORT


def validate_loopback_url(url: object) -> str:
    """Return url unchanged if it is a plain http URL to a loopback IP literal.

    This is a strict allow-list: anything unexpected is rejected. The error never
    echoes the input, since it may be attacker-controlled or contain credentials.
    """
    if (
        not isinstance(url, str)
        or not url
        or not _is_printable_ascii(url)
        or not url.startswith(_SCHEME_PREFIX)
        or "?" in url
        or "#" in url
    ):
        raise NonLoopbackURLError
    netloc = url[len(_SCHEME_PREFIX) :].split("/", 1)[0]
    if "@" in netloc:
        raise NonLoopbackURLError

    if netloc.startswith("["):
        host, sep, rest = netloc[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else None
        ok = bool(sep) and (rest == "" or port is not None)
        bracketed = True
    else:
        host, colon, port_text = netloc.partition(":")
        port = port_text if colon else None
        ok = ":" not in port_text
        bracketed = False

    if not ok or not _is_loopback_ip(host, bracketed=bracketed):
        raise NonLoopbackURLError
    if port is not None and not _valid_port(port):
        raise NonLoopbackURLError
    return url


async def _enforce_loopback_request(request: httpx.Request) -> None:
    """Request hook: re-check every outgoing URL, including absolute-URL overrides."""
    url = request.url
    if url.scheme != "http" or url.userinfo or not _is_loopback_ip(url.host):
        raise NonLoopbackURLError


def create_async_client(
    base_url: str,
    *,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the only kind of HTTP client the sidecar is allowed to use."""
    validate_loopback_url(base_url)
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
        event_hooks={"request": [_enforce_loopback_request]},
    )
