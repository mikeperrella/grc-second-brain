// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::ExitCode;

fn main() -> ExitCode {
    match desktop_lib::run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => {
            // One fixed line: the error itself is not printed, it could carry a path.
            eprintln!("the application failed to start");
            ExitCode::FAILURE
        }
    }
}
