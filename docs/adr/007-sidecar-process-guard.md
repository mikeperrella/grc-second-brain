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

## Addendum: stdout is sealed before the server starts

The host fails closed on any stdout byte after the ready line (ADR-004), but until now that only held by convention. `security/stdio.py` `seal_stdout()` makes it structural, in the same PR:

- `os.dup(1)` keeps a private copy of the pipe for the ready line. `os.dup2` then points file descriptor 1 at the null device, so `print`, `sys.stdout` and C code writing to fd 1 go nowhere. On Windows `SetStdHandle` points the operating system's standard-output handle at the same device, for native code that calls `GetStdHandle` and `WriteFile`.
- `main()` seals after the socket is bound and before uvicorn starts. `ReadySignalServer` writes the ready line to the private copy and closes it, so the sidecar holds no handle to the pipe afterwards. The host treats end-of-stream after the ready line as normal (`supervisor.rs`). The venv launcher (and a PyInstaller bootloader) keep their own copy of the pipe, which code in the sidecar process cannot reach.
- Stray stdout is discarded and not logged, because logging it could put document content into the log (CLAUDE.md Section 7). A seal failure exits 2 with one fixed line.
- Interaction with uvicorn's logging: the sidecar passes `log_config=None`, so uvicorn installs no handlers; its `uvicorn.error` messages propagate to our rotating file handler and `access_log=False` silences the rest. The only references to `sys.stdout` in uvicorn are its default logging dictionary (`config.py:106`, unused here) and a colour check (`logging.py:42`). Python's `logging.lastResort` and `warnings` write to stderr. So nothing on the serve path touched stdout before, and a handler added by a future dependency would now write to the null device instead of the pipe.
- Known gap: on this machine (Windows 11, CPython 3.11.9) removing the `SetStdHandle` call leaves the tests green, because the C runtime's `_dup2` already repoints the standard handle for descriptors 0 to 2. The call stays as defence in depth, but there is no mutation proof for it. The `dup2` itself, the closing of the private copy and the fail-closed exit each have one.

## Amendment (M3b): native extension loading is allowlisted to one path

### Context

M3b loads sqlite-vec, a native library, into the sidecar's SQLite connection. The guard above vetoes only the nine process-creation events, so `sqlite3.enable_load_extension` and `sqlite3.load_extension` were allowed, for any path. Loading a native library runs arbitrary code inside the sidecar, with the auth token in its memory and no process boundary. That is worse than starting a process, so the same hook now guards it.

Verified in the M3b spike (Python 3.11.9 on Windows, 3.11.16 in the `--network none` container): `sqlite3.load_extension` raises an audit event **before** the library is opened, with `args = (connection, path)`. `path` is the string the caller passed, unchanged: no normalization and no file suffix (`sqlite_vec.loadable_path()` returns `...\sqlite_vec\vec0`, and SQLite appends `.dll` or `.so` itself). `enable_load_extension` raises its own event `(connection, bool)`. This was checked on CPython 3.11 only; a different CPython minor version must repeat the check.

### Decision

- `install_process_guard(approved_extension)` takes the one allowed path. `__main__` passes `vec_extension_path()` (`repositories/sqlite_vec_repo.py`, the vendored `sqlite_vec.loadable_path()`). The hook compares `args[1]` to it and raises `ExtensionLoadBlockedError` (a `PermissionError`, like `SpawnBlockedError`, with a fixed message) for anything else. With no approved path, every extension load is vetoed.
- **Comparison.** Both sides go through `os.path.normcase(os.path.normpath(...))`: separators and `.`/`..` segments are collapsed, and case is folded on Windows only. The argument must also be absolute, and a non-string or missing argument is vetoed.
- **A normalization mismatch always fails closed.** The only way to pass is to equal the approved string after normalization, so two spellings of one file that this cannot reduce to the same string are DENIED, never wrongly allowed. Examples that are denied: a relative path to the same file, a Windows trailing dot (`vec0.`), a short 8.3 name, a symlink or junction spelling, a UNC form. The cost is that a caller must pass the exact path from `loadable_path()`, which is what `sqlite_vec.load()` does.
- `enable_load_extension` stays allowed. It is only a switch; the vetoed event is the load itself. The repository turns it on only around the one `sqlite_vec.load` call and off again immediately.
- Semgrep rules `grc-py-sqlite-outside-repositories` and `grc-py-extension-load-outside-repositories` keep every `sqlite3` import and every extension-load call site inside `grc_sidecar/repositories/`.

### Tests

All with the real guard, in child processes (an audit hook cannot be removed, so the pytest process never has it). `tests/test_process_guard.py`: the approved path loads; a **different existing file** (a byte-for-byte copy of the real library in another folder, which would load fine if allowed) is blocked, and the approved path still loads afterwards (`test_foreign_extension_load_is_blocked`); a **relative path** to the real library is blocked; with no approved path everything is blocked; on Windows the **approved path with different letter case is allowed** and the **trailing-dot spelling is blocked**. `tests/test_deps_no_spawn.py` starts the real sidecar and shows that, after the ready line, the vendored extension loads and a foreign copy is refused, so `__main__` really passes the path. `tests/test_extension_loading.py` calls the hook function directly for the argument shapes. The nine original process-event tests are unchanged and still pass.

### Scope statement and known gaps

The hook is **defence in depth, not a sandbox.** PEP 578 says audit hooks are not a security boundary, and native code can bypass them. Specifically:

- **`ctypes.dlopen` (and `ctypes.CDLL`, `WinDLL`).** Native code loaded through `ctypes` bypasses both the process check and the extension check. Blocking it is impractical because our own `security/stdio.py` calls `ctypes.WinDLL("kernel32")`, and `process_guard.py` already recorded that spawning through `ctypes` bypasses the guard. `test_ctypes_dlopen_is_a_known_unguarded_path` pins that the hook does not veto it, so a future change in behaviour is noticed and this ADR is updated on purpose.
- **Libraries loaded from inside an approved extension.** `vec0.dll` imports `LoadLibraryExW` (found by a string scan of the binary, which is supporting evidence and not a full import-table analysis). Native code inside the extension can therefore load further libraries without any event the Python audit hook can see. This is the same category as the `ctypes` gap: once native code runs in the process, the Python-level hook cannot constrain it. The `vec0.so` scan found no `dlopen`, socket or process-creation strings. The mitigation is supply-chain: the version is pinned, the wheel hash is recorded (ADR-001 addendum), and the library is not audited (ADR-001).
- The guard cannot stop a process that is already running native code with the sidecar's privileges. The host-side Job Object follow-up above is still the kernel-level answer.

### Consequences for later chunks

- **M7** extends this guard for `terraform` and `docker`. It must not widen the extension allowlist, and it must keep the single approved path.
- **M8 (PyInstaller)** must keep `loadable_path()` and the approved path the same string in the frozen app: `collect_data_files("sqlite_vec")` (or `collect_dynamic_libs`) puts the library under the frozen `_internal` directory, and `vec_extension_path()` must resolve there.
