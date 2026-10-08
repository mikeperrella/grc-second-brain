//! `cargo run` and the Tauri CLI must always start the app, never the test-only fake sidecar that
//! lives in the same package (M2 plan section 2, ADR-004). Cargo documents `default-run` as how
//! `cargo run` picks among several binaries; this test fails if that line is removed.

use std::error::Error;
use std::process::Command;

use serde_json::Value;

/// Ask Cargo itself what it knows about this package. `--offline --no-deps` makes it read only the
/// local manifest: no network, no dependency resolution. The arguments are an array and no shell
/// is involved.
fn package_metadata() -> Result<Value, Box<dyn Error>> {
    let output = Command::new(env!("CARGO"))
        .args([
            "metadata",
            "--offline",
            "--no-deps",
            "--format-version",
            "1",
        ])
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .output()?;
    if !output.status.success() {
        return Err(format!("cargo metadata failed with {}", output.status).into());
    }
    Ok(serde_json::from_slice(&output.stdout)?)
}

#[test]
fn the_package_declares_the_app_as_its_default_binary() -> Result<(), Box<dyn Error>> {
    let metadata = package_metadata()?;
    let packages = metadata["packages"]
        .as_array()
        .ok_or("cargo metadata has no packages array")?;
    let package = packages
        .iter()
        .find(|package| package["name"] == "desktop")
        .ok_or("the desktop package is not in the metadata")?;

    assert_eq!(
        package["default_run"], "desktop",
        "`default-run = \"desktop\"` is missing from Cargo.toml"
    );
    Ok(())
}

#[test]
fn the_fake_sidecar_is_a_second_binary_of_the_same_package() -> Result<(), Box<dyn Error>> {
    // Documents why `default-run` matters: there really are two binaries.
    let metadata = package_metadata()?;
    let packages = metadata["packages"]
        .as_array()
        .ok_or("cargo metadata has no packages array")?;
    let package = packages
        .iter()
        .find(|package| package["name"] == "desktop")
        .ok_or("the desktop package is not in the metadata")?;
    let targets = package["targets"]
        .as_array()
        .ok_or("the package has no targets")?;
    let binaries: Vec<&str> = targets
        .iter()
        .filter(|target| {
            target["kind"]
                .as_array()
                .is_some_and(|kinds| kinds.iter().any(|kind| kind == "bin"))
        })
        .filter_map(|target| target["name"].as_str())
        .collect();

    assert!(binaries.contains(&"desktop"), "{binaries:?}");
    assert!(binaries.contains(&"fake_sidecar"), "{binaries:?}");
    Ok(())
}
