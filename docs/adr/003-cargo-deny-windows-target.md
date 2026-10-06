# ADR-003: Scope cargo-deny to the Windows target

Status: Accepted

## Context

CI runs `cargo deny check` (RustSec advisories, licenses, bans, sources) on the Tauri crate in `apps/desktop/src-tauri/`. By default it checks the dependency graph for every platform Tauri supports. On Linux, Tauri v2 uses the GTK3 stack through the `gtk-rs` 0.18 bindings, and that stack pulls in two crates with open advisories:

- **RUSTSEC-2024-0429** (`GHSA-wrw7-89jp-8q8g`): `glib 0.18.5` has unsound `Iterator` and `DoubleEndedIterator` implementations for `glib::VariantStrIter`. The fixed version is `glib 0.20.0`. `gtk 0.18` pins `glib 0.18`, so we cannot upgrade it ourselves; it moves only when Tauri moves off GTK3.
- **RUSTSEC-2024-0370**: `proc-macro-error 1.0.4` is unmaintained. It arrives through `glib-macros` and `gtk3-macros`, so it is also Linux-only.

GitHub's Dependabot alert #1 on this repository is the glib advisory.

Windows is the only platform we ship at this stage: the project is built and verified on Windows, and the M1.5 plan runs the Rust checks on Windows. There is no Linux or macOS build or installer planned. `cargo deny` hides these two advisories with its defaults, because `unsound` defaults to the `workspace` scope. They only appear with `unsound = "all"`, which we set so transitive problems cannot hide.

## Decision

- `deny.toml` sets `[graph] targets = ["x86_64-pc-windows-msvc"]`, so `cargo deny` checks only what is built for Windows.
- `[advisories]` sets `unsound = "all"` and `unmaintained = "all"`, and has **no** `ignore` entries.
- We do not ignore RUSTSEC-2024-0429 or RUSTSEC-2024-0370. The advisories do not apply to a Windows build, so scoping is more accurate than an ignore: an ignore would also hide a future advisory on the same crate if it ever reached a shipped build.

### Evidence (cargo 1.99.0, cargo-deny 0.20.2, run from `apps/desktop/src-tauri/`)

`cargo tree -i glib --target x86_64-pc-windows-msvc`:

```
warning: nothing to print.
```

`cargo tree -i glib --target x86_64-unknown-linux-gnu` (abridged):

```
glib v0.18.5
├── atk v0.18.2
│   └── gtk v0.18.2
│       ├── muda v0.20.0 -> tauri v2.12.1 -> desktop
│       ├── tao v0.37.1 -> tauri-runtime-wry -> tauri
│       ├── webkit2gtk v2.0.2 -> tauri / tauri-runtime / wry
│       └── wry v0.57.0 -> tauri-runtime-wry -> tauri
├── cairo-rs v0.18.5 -> gdk, gtk, ...
└── ...
```

`cargo deny check advisories` with `unsound = "all"` and `unmaintained = "all"`:

| Scope | Result |
|---|---|
| All targets | 2 errors: RUSTSEC-2024-0429 (`glib 0.18.5`, unsound) and RUSTSEC-2024-0370 (`proc-macro-error 1.0.4`, unmaintained) |
| `x86_64-pc-windows-msvc` | none |

## Consequences

Positive:

- The advisory check reports only what can reach a shipped binary, so a failure always needs action.
- No advisory-specific ignores to maintain or forget to remove.

Negative or risky:

- **Linux and macOS are not checked.** An advisory in a crate that exists only on those platforms (including a new one in the GTK stack) is invisible to CI. That is acceptable only while Windows is the only shipped platform.
- Dependabot's alert for glib stays open on GitHub, because Dependabot reads `Cargo.lock` and does not know the Windows scope. Dismissing it, with a reason that points at this ADR, is a repository action for the owner.
- `cargo tree` and `cargo deny` agree today. If Tauri changes how it selects its Windows dependencies, the evidence above should be re-run.

## Revisit trigger

Revisit this ADR **as soon as any non-Windows build is planned** (a Linux or macOS installer, a Linux CI build that ships, or a CI job that builds Tauri on Linux). At that point remove the `targets` restriction, then either upgrade `gtk`/`glib` through Tauri or record a reasoned, expiring ignore for each advisory in `deny.toml`.
