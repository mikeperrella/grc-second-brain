# ADR-001: sqlite-vec as the default vector store

Status: Accepted

## Context

The knowledge vault is Markdown and is the source of truth. The vector index is derived, disposable state that can always be rebuilt from the vault. We need a store for chunk embeddings (`nomic-embed-text`) with nearest-neighbour search that runs entirely on the user's machine and ships inside one desktop installer.

Constraints from CLAUDE.md: air-gapped, no telemetry (Section 3.2), no server process, and a swappable implementation so the codebase can evolve into a commercial product.

ChromaDB was the first candidate. It has optional telemetry that must be disabled explicitly in code and covered by a test (CLAUDE.md Section 3.2). With sqlite-vec that obligation does not arise.

## Decision

Use sqlite-vec (`vec0` virtual tables in a single SQLite file) as the default implementation behind a `VectorRepository` protocol.

- Nothing outside `repositories/` imports sqlite-vec or `sqlite3` directly.
- ChromaDB stays a possible second implementation of the same protocol. Swapping is a change in one module plus the dependency wiring.
- The index file lives in the app data directory, is gitignored, and is rebuilt from the vault when it is missing, corrupt, or the embedding model changes.
- Chunk IDs are deterministic (CLAUDE.md Section 8.1), so re-indexing is idempotent.

## Consequences

Positive:

- One file and no server process, so there is nothing to bind or supervise.
- A small dependency surface. The Windows wheel (version 0.1.9) contains one Python file with no network imports plus the native `vec0.dll`. The native library has not been audited.
- Dual-licensed MIT or Apache-2.0, per the package metadata, which suits a commercial product.
- Rebuildable by design, so a corrupt index is an inconvenience, not data loss.

Negative or risky:

- Pre-1.0 (version 0.1.9 when this ADR was written), so the API may change. The version is pinned and upgrades are reviewed.
- It is a SQLite loadable extension. The bundled Python's `sqlite3` must allow extension loading, and PyInstaller must ship the native library. Both are verified at M3 and M8, not assumed here.
- Search is exact (brute force), not an approximate index. That should be adequate for a single company's vault. Revisit if the corpus grows large.

Revisit if search latency becomes unacceptable, the project stalls, or extension loading proves unreliable once packaged.

## Addendum (M3b): verified on Windows and Linux

Checked in the M3b spike and by the M3b tests, with `sqlite-vec==0.1.9` pinned in `pyproject.toml` and `constraints.txt`.

- **Extension loading works on both platforms.** `enable_load_extension` exists and `sqlite_vec.load` succeeds on Windows (Python 3.11.9, SQLite 3.45.1) and in the `--network none` air-gap container (Python 3.11.16, SQLite 3.46.1). `vec_version()` returns `v0.1.9` in both. FTS5 is available in both (compile option `ENABLE_FTS5`).
- **vec0 behaves as the repository needs.** `DELETE` and `INSERT` inside `BEGIN IMMEDIATE` roll back completely together with the ordinary and FTS5 tables (including reusing a deleted rowid in the same transaction). `distance_metric=cosine` is supported and returns `1 - cos` (0.0 for the same direction, 1.0 for orthogonal). A vector of the wrong dimension is rejected. The default metric is L2, so cosine is declared explicitly in the table definition.
- **What the wheel contains.** One `__init__.py` (byte-identical in the Windows and Linux wheels), one native library (`vec0.dll` or `vec0.so`) and dist-info. No `subprocess`, `os.system`, `popen`, `ctypes`, `socket`, `urllib`, `eval` or `exec` appears in the Python (the only hit for "exec" is `db.execute` inside `register_numpy`, which we do not call). A string scan of the binaries found no `socket`, `getaddrinfo`, `popen`, `execve`, `fork`, `CreateProcess`, `curl` or `WinHttp`/`WinInet` names; `vec0.dll` references `LoadLibraryExW` (see the ADR-007 amendment). That scan is supporting evidence, not an audit of the native code, which remains unaudited.
- **Wheel hashes** (sha256 of the file as downloaded from PyPI, for the M8 hash lock):
  - `sqlite_vec-0.1.9-py3-none-win_amd64.whl`: `4a28dc12fa4b53d7b1dced22da2488fade444e96b5d16fd2d698cd670675cf32`
  - `sqlite_vec-0.1.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux1_x86_64.whl`: `1515727990b49e79bcaf75fdee2ffc7d461f8b66905013231251f1c8938e7786`
- **Only the repository package imports it.** `sqlite3` and `sqlite_vec` are imported only under `grc_sidecar/repositories/`; Semgrep rules enforce it. The ADR-007 amendment restricts the process to loading this one library.
- **mypy.** The package ships no type information, so `pyproject.toml` has a single `ignore_missing_imports` override for `sqlite_vec`. The calls into it are wrapped in typed functions.
- **PyInstaller (M8, recorded only).** The native library is a data file inside the `sqlite_vec` package. The spec needs `collect_data_files("sqlite_vec")` (or `collect_dynamic_libs`), and `loadable_path()` must resolve under the frozen `_internal` directory, with the ADR-007 approved path following it.
- **Hybrid retrieval** uses an FTS5 table beside the vectors in the same file (ADR-010).
