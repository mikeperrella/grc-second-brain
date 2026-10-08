//! Which program starts the sidecar (M2 plan section 5, ADR-004).
//!
//! The interpreter path is decided at COMPILE time and exists only in debug builds:
//! `CARGO_MANIFEST_DIR` joined with a fixed relative path to the repository's `sidecar/.venv`.
//! There is no environment variable, no `PATH` lookup and no frontend input, so nothing at runtime
//! can point the host at a different executable. Release builds contain none of this code and
//! return [`ResolveError::NotConfigured`] until M8 bundles the sidecar as a Tauri `externalBin`.

use std::path::PathBuf;

/// The arguments the sidecar is started with: isolated mode, run the package as a module.
pub(crate) const SIDECAR_ARGS: [&str; 3] = ["-I", "-m", "grc_sidecar"];

/// Why no program could be chosen. Unit variants: nothing here echoes a path.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum ResolveError {
    #[error("the sidecar interpreter is not installed (create sidecar/.venv)")]
    NotInstalled,
    #[error("this build has no sidecar configured (packaging arrives in M8)")]
    NotConfigured,
    #[error("the sidecar interpreter resolves to a location outside the sidecar directory")]
    OutsideSidecarDir,
}

/// The program to start. Debug builds: the repository's dev venv interpreter, checked to be a
/// file inside the repository's `sidecar/` directory. Release builds: always `NotConfigured`.
pub fn resolve_sidecar_program() -> Result<PathBuf, ResolveError> {
    #[cfg(debug_assertions)]
    {
        dev::resolve()
    }
    #[cfg(not(debug_assertions))]
    {
        Err(ResolveError::NotConfigured)
    }
}

/// Canonicalize `candidate` and `root`, then require that the candidate is a regular file whose
/// real path lies inside the root's real path. Canonicalizing first resolves `..` segments and
/// symlinks, so neither can be used to escape (CLAUDE.md 3.6, the same rule the sidecar applies).
/// A missing candidate, or one that is not a file, is `NotInstalled`.
#[cfg(debug_assertions)]
pub fn checked_inside(
    candidate: &std::path::Path,
    root: &std::path::Path,
) -> Result<PathBuf, ResolveError> {
    let root = root
        .canonicalize()
        .map_err(|_| ResolveError::NotInstalled)?;
    let resolved = candidate
        .canonicalize()
        .map_err(|_| ResolveError::NotInstalled)?;
    if !resolved.is_file() {
        return Err(ResolveError::NotInstalled);
    }
    if !resolved.starts_with(&root) {
        return Err(ResolveError::OutsideSidecarDir);
    }
    Ok(resolved)
}

/// Everything that names the dev interpreter. Compiled in debug builds only, so the path string
/// below cannot end up in a release binary.
#[cfg(debug_assertions)]
mod dev {
    use super::{checked_inside, ResolveError};
    use std::path::{Path, PathBuf};

    /// From `apps/desktop/src-tauri` up to the repository root, then into the sidecar.
    const SIDECAR_DIR: &str = "../../../sidecar";
    const INTERPRETER: &str = "../../../sidecar/.venv/Scripts/python.exe";

    pub(super) fn resolve() -> Result<PathBuf, ResolveError> {
        let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
        checked_inside(
            &manifest_dir.join(INTERPRETER),
            &manifest_dir.join(SIDECAR_DIR),
        )
    }
}

#[cfg(all(test, debug_assertions))]
mod tests {
    //! The tests that need real files (accepted file, missing file or root, directory, `..` escape,
    //! symlink escape) live in `tests/resolve_paths.rs`. They use Cargo's private test directory
    //! instead of the shared system temp directory, so nothing here touches the file system.
    use super::*;

    #[test]
    fn the_dev_resolver_never_names_a_path_outside_the_repository_sidecar() {
        // In a checkout without sidecar/.venv this is NotInstalled; with one it is a file inside
        // sidecar/. Either way it is never OutsideSidecarDir and never NotConfigured.
        match resolve_sidecar_program() {
            Ok(path) => assert!(path.is_file()),
            Err(error) => assert_eq!(error, ResolveError::NotInstalled),
        }
    }

    #[test]
    fn the_arguments_are_isolated_module_mode() {
        assert_eq!(SIDECAR_ARGS, ["-I", "-m", "grc_sidecar"]);
    }
}

/// Runs only in release test builds (`cargo test --release`), which CI does not do. The M2b report
/// records a local run; it stays a gate for M8 (M2 plan section 5, decision D2).
#[cfg(all(test, not(debug_assertions)))]
mod release_tests {
    use super::*;

    #[test]
    fn release_builds_have_no_sidecar_configured() {
        assert_eq!(resolve_sidecar_program(), Err(ResolveError::NotConfigured));
    }
}
