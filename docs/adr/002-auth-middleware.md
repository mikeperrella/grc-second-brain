# ADR-002: Pure-ASGI auth middleware wrapping the whole app

Status: Accepted

## Context

The sidecar listens on a loopback TCP port, and any local process can connect to a loopback port. CLAUDE.md Section 3.4 therefore requires zero-trust IPC: the Rust host generates a 32-byte random token, hex-encodes it, passes it to the sidecar in `GRC_AUTH_TOKEN`, and every HTTP request (including `/health`) must carry it in `X-GRC-Auth`.

We need to decide where the check lives so that no response, including the framework's own error responses, can be produced without the token.

## Decision

- `AuthMiddleware` is a plain ASGI class (no framework base class) that wraps the entire FastAPI application from the outside. Starlette's `add_middleware` would place the check inside Starlette's error-handling layer, so a 404, 405, 422 or 500 could be produced before authentication. Wrapping from outside means unknown routes, disabled docs routes and framework errors all answer `401` to an unauthenticated caller.
- The token is read once by `read_auth_token`, which removes `GRC_AUTH_TOKEN` from `os.environ` on every outcome so child processes never inherit it. It must be exactly 64 **lowercase** hex characters. **The M2 Rust host must generate lowercase hex**; uppercase is rejected on purpose so there is only one valid spelling.
- The comparison uses `hmac.compare_digest`. A missing header or more than one `X-GRC-Auth` header is rejected. WebSocket scopes are always rejected, and unknown scope types never reach the app. Only `lifespan` events pass through, because uvicorn needs them to start and stop.
- A failed check returns `401` with an empty body and no `WWW-Authenticate` header. RFC 9110 expects that header on a 401. It is omitted deliberately, for minimal disclosure: the only client is our own host, which already knows the scheme, so the header would only describe the mechanism to anyone else probing the port.
- Errors about the token carry fixed messages and are raised outside `except` blocks, so the token cannot appear in an error, its exception chain, or a traceback.
- The server binds `127.0.0.1` on an OS-assigned port. After the socket is listening it prints exactly one line, `{"event":"ready","port":<int>}`, to stdout, and nothing else is ever written there.

## Consequences

Positive:

- One small class holds the whole check, so it is easy to review and test, including with mutation tests.
- Nothing can answer a request before authentication, so there is no unauthenticated route, error page or documentation endpoint to probe.

Negative or risky:

- The token travels in a plain HTTP header over loopback. Anyone who can read the sidecar's memory or environment before it is deleted can obtain it; this ADR does not defend against an attacker already running as the same user.
- Rejecting uppercase hex makes the host and sidecar coupled on one spelling. A host that hex-encodes in uppercase would fail at startup with exit code 2 and a clear message.
- **Orphan handling is deferred to M2.** If the Tauri host dies, nothing in the sidecar stops it. In M2 the Rust host should place the sidecar in a Windows Job Object with kill-on-job-close, so the OS terminates the sidecar if the host dies.

Revisit if a second client (other than the Rust host) is ever needed, or if the transport changes (for example Unix domain sockets or named pipes).
