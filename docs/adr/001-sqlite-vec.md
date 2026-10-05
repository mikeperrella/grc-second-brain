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
