"""Prove the container has no network, and that this check can actually connect.

Run inside `docker run --network none`. Plain standard-library Python: it deliberately
does not import the app or the pytest egress guard, so it tests the container, not our code.

Three checks, and all three must hold for exit code 0:

1. LOOPBACK works: connect to a listener on 127.0.0.1. If this fails the canary itself is
   broken (it could not connect even where connecting is allowed), so a failed outbound
   check below would prove nothing. That is reported as BROKEN CANARY.
2. OUTBOUND is blocked: a TCP connect to a public address (1.1.1.1:443) must fail.
3. DNS is blocked: resolving a public name must fail.

Exit codes: 0 isolated, 1 not isolated (outbound or DNS reachable), 2 broken canary.
"""

import socket
import sys

OUTBOUND_ADDRESS = ("1.1.1.1", 443)
DNS_NAME = "example.com"
TIMEOUT_SECONDS = 3.0


def check_loopback() -> tuple[bool, str]:
    """Return (connected, detail) for a connection to a listener on 127.0.0.1."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT_SECONDS):
                return True, f"connected to 127.0.0.1:{port}"
    except OSError as error:
        return False, f"{type(error).__name__}: {error}"


def check_outbound() -> tuple[bool, str]:
    """Return (reachable, detail) for a TCP connect to a public address."""
    try:
        with socket.create_connection(OUTBOUND_ADDRESS, timeout=TIMEOUT_SECONDS):
            return True, f"connected to {OUTBOUND_ADDRESS[0]}:{OUTBOUND_ADDRESS[1]}"
    except OSError as error:
        return False, f"{type(error).__name__}: {error}"


def check_dns() -> tuple[bool, str]:
    """Return (resolved, detail) for a lookup of a public name."""
    try:
        addresses = socket.getaddrinfo(DNS_NAME, 443, type=socket.SOCK_STREAM)
        return True, f"{DNS_NAME} resolved to {addresses[0][4][0]}"
    except OSError as error:
        return False, f"{type(error).__name__}: {error}"


def decide(
    loopback_ok: bool, outbound_reachable: bool, dns_resolves: bool
) -> tuple[int, list[str], str]:
    """Return (exit code, one labelled line per check, the final RESULT line)."""
    checks = [
        f"loopback  {'PASS' if loopback_ok else 'FAIL'}: loopback connection "
        f"{'succeeded' if loopback_ok else 'failed'} (must succeed)",
        f"outbound  {'FAIL' if outbound_reachable else 'PASS'}: public address "
        f"{'REACHABLE' if outbound_reachable else 'BLOCKED'} (must be blocked)",
        f"dns       {'FAIL' if dns_resolves else 'PASS'}: public name "
        f"{'RESOLVES' if dns_resolves else 'BLOCKED'} (must be blocked)",
    ]
    if not loopback_ok:
        return (
            2,
            checks,
            "RESULT: BROKEN CANARY - it cannot connect even to loopback, so it proves nothing",
        )
    if outbound_reachable or dns_resolves:
        return 1, checks, "RESULT: NOT ISOLATED - the container can reach the network"
    return 0, checks, "RESULT: ISOLATED - loopback works, outbound and DNS are blocked"


def main() -> int:
    loopback_ok, loopback_detail = check_loopback()
    outbound_reachable, outbound_detail = check_outbound()
    dns_resolves, dns_detail = check_dns()
    code, checks, result = decide(loopback_ok, outbound_reachable, dns_resolves)
    details = [loopback_detail, outbound_detail, dns_detail]
    for check, detail in zip(checks, details, strict=True):
        print(f"{check}\n          detail: {detail}")
    print(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
