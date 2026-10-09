# ADR-007: Sidecar process guard (audit hook that vetoes process creation)

Status: Accepted

## Context

On Windows, uvicorn's `Server.startup` calls `platform.system()` (`uvicorn/server.py:151`, inside the pre-bound-socket branch we use), and on CPython 3.11 that runs `ver` through `cmd.exe` (`platform.py:371` `win32_ver()` always calls `_syscmd_ver()`, which calls `subprocess.check_output('ver', shell=True)`). ADR-004 found it and proved the auth token does not reach that process (`tests/test_child_spawns.py`). It still contradicts the spirit of CLAUDE.md 3.5 (no shell execution), and nothing at runtime stops the next library from doing the same.

Evidence for the candidates (read from the installed interpreters on 2026-10-08):

- Python 3.13.1 and 3.14: `_win32_ver()` asks WMI first, but on any `OSError` (WMI unavailable: stopped service, Server Core, a container, a restricted token) it still falls back to `_syscmd_ver()` and so to `cmd.exe`; `uname()` has a second `_syscmd_ver` fallback. A newer Python removes the spawn on a healthy machine, not the code path. Python 3.12 is not installed here and was not examined.
- In 3.11, 3.13 and 3.14 `_syscmd_ver` catches `(OSError, subprocess.CalledProcessError)` and tries the next command. When all fail it returns its defaults, `win32_ver()` falls back to `sys.getwindowsversion()`, and `uname()` still normalizes the system name to `Windows`. So a refused spawn that raises an `OSError` degrades gracefully.
- `subprocess.Popen` raises the documented audit event `subprocess.Popen` before `CreateProcess` (`subprocess.py:1534` in 3.11).
- The other runtime dependencies (fastapi, starlette, pydantic, anyio, h11, httpx) do not start processes or call `platform` on the startup path.

## Decision

`grc_sidecar.security.process_guard.install_process_guard()` installs a PEP 578 audit hook (`sys.addaudithook`) that raises `SpawnBlockedError` for every process-creation event: `subprocess.Popen`, `_winapi.CreateProcess`, `os.system`, `os.exec`, `os.spawn`, `os.posix_spawn`, `os.fork`, `os.forkpty`, `os.startfile`. `__main__.main()` installs it right after reading the token, before settings, logging or uvicorn.

- `SpawnBlockedError` is a `PermissionError`, so `platform` treats the blocked `ver` as "unknown" and falls back to `sys.getwindowsversion()`; `platform.system()` still returns `Windows` (tested).
- The hook runs for every audit event in the process, so its first statement is one `frozenset` membership test and it returns for anything else. It never raises for another event and never reads the event arguments (which can contain an environment).
- Installation is verified: `sys.addaudithook` silently does nothing when an earlier hook vetoes it, so the installer fires a private event and requires that its hook saw it; otherwise startup fails with exit 2 and one fixed stderr line.
- The pytest process never gets the real guard (a hook cannot be removed). `conftest.py` replaces the installer in `main()` for in-process tests and fails the session if the guard was ever installed there. The real guard is tested in child processes.
- `tests/test_child_spawns.py` is the acceptance test, flipped: no spawn attempted by the sidecar ever starts; on Windows the blocked `ver` attempt must be present (so the test cannot pass just because nothing tried).

Alternatives rejected:

| Option | Why not |
|---|---|
| Move to Python 3.13/3.14 | Does not remove the code path (WMI failure still spawns). Touches CLAUDE.md Section 4, `pyproject.toml`, ruff and mypy targets, `constraints.txt`, five workflows, the air-gap Dockerfile digest, Dependabot, the dev venv, and M8 packaging. |
| Seed `platform._uname_cache` | Private CPython global; a rename silently brings the spawn back; we would have to reproduce `win32_ver`'s values. |
| Replace `uvicorn.server.platform` | Patches one call site in a third-party module; silent breakage when uvicorn changes its import. |
| Own the serve path | Maintains a copy of uvicorn's startup on every upgrade. |
| Let uvicorn bind the port itself | Loses `SO_EXCLUSIVEADDRUSE`; another process of the same user could share the port and receive requests carrying the token. |

## Consequences

Positive: CLAUDE.md 3.5 becomes a runtime control that also covers libraries added later. Public, documented APIs only. Stays on Python 3.11, so CLAUDE.md Section 4 does not change.

Negative or risky:

- An audit hook is not a sandbox. Native code that calls `CreateProcess` or `fork` directly (for example through `ctypes`) raises none of these events.
- M7 (terraform, docker validation) must start processes. It extends the guard with an allowlist of absolute executable paths and an argument list, never `ComSpec` or a shell. It must not remove the guard.
- If a future CPython stops tolerating a failed `ver`, startup fails closed (a startup test fails) instead of spawning.

Follow-ups (recorded only, not done here):

- Ask uvicorn upstream to compare `sys.platform == "win32"` instead of calling `platform.system()`. Filing it is outward-facing and is Mike's call.
- A host-side Job Object `ActiveProcessLimit` would enforce the same rule from the kernel, for native code too. It changes the limit-flag test (`0x2000`) and the fake sidecar's grandchild tests in ADR-004.
