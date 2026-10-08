//! TEST-ONLY stand-in for the Python sidecar (M2 plan section 12, ADR-004).
//!
//! It speaks the ready-line protocol and misbehaves on request, so the supervisor, the Job Object
//! and the spawn path can be tested without Python. It must never be part of an installer: the M8
//! checklist in CLAUDE.md says so, and `default-run = "desktop"` keeps `cargo run` and the Tauri CLI
//! from starting it by mistake.
//!
//! Usage: `fake_sidecar <mode> [file]`. All data is synthetic. It never reads or prints the auth
//! token. The sleeps below only simulate a slow sidecar; the host code and the tests never sleep
//! (they wait on events and handles).
//!
//! | Mode                | Behaviour                                                          |
//! |---------------------|--------------------------------------------------------------------|
//! | `silent`            | prints nothing, holds                                              |
//! | `garbage`           | prints a line that is not JSON, holds                              |
//! | `exit2`             | one line on stderr, exits with code 2 (a rejected startup)         |
//! | `exit3`             | exits with code 3                                                  |
//! | `late-ready`        | waits 1.5 s, then prints the ready line, holds                     |
//! | `ready`             | prints the ready line, holds                                       |
//! | `ready-then-output` | prints the ready line, then more stdout, holds                     |
//! | `oversized`         | prints 2 KiB without a newline, holds                              |
//! | `two-objects`       | prints two ready objects on one line, holds                        |
//! | `env-report <file>` | writes its environment variable NAMES to `<file>`, then ready      |
//! | `grandchild <file>` | starts a second copy that holds, writes its PID to `<file>`, ready |
//! | `hold`              | holds forever (the grandchild's mode)                              |

use std::env;
use std::fs;
use std::io::{self, Write};
use std::process::{Command, ExitCode, Stdio};
use std::thread;
use std::time::Duration;

/// Any non-zero port: the supervisor only checks the shape, nothing connects to it.
const PORT: u16 = 40123;

/// The real sidecar's line on Windows ends in `\r\n`, so the fake's does too.
fn ready_line() -> String {
    format!("{{\"event\":\"ready\",\"port\":{PORT}}}\r\n")
}

fn write_stdout(text: &str) -> io::Result<()> {
    let mut stdout = io::stdout().lock();
    stdout.write_all(text.as_bytes())?;
    stdout.flush()
}

/// Never returns normally: parks the thread forever. The host ends the process through the job.
fn hold() -> ! {
    loop {
        thread::park();
    }
}

fn run(mode: &str, file: Option<&str>) -> io::Result<ExitCode> {
    match mode {
        "silent" | "hold" => hold(),
        "garbage" => {
            write_stdout("this is not json\r\n")?;
            hold()
        }
        "exit2" => {
            eprintln!("fake sidecar: startup rejected");
            Ok(ExitCode::from(2))
        }
        "exit3" => Ok(ExitCode::from(3)),
        "late-ready" => {
            thread::sleep(Duration::from_millis(1500));
            write_stdout(&ready_line())?;
            hold()
        }
        "ready" => {
            write_stdout(&ready_line())?;
            hold()
        }
        "ready-then-output" => {
            write_stdout(&ready_line())?;
            write_stdout("unexpected output after ready\r\n")?;
            hold()
        }
        "oversized" => {
            write_stdout(&"a".repeat(2048))?;
            hold()
        }
        "two-objects" => {
            let one = "{\"event\":\"ready\",\"port\":1}";
            write_stdout(&format!("{one}{one}\r\n"))?;
            hold()
        }
        "env-report" => {
            let path = file.ok_or_else(|| io::Error::other("env-report needs a file"))?;
            let mut names: Vec<String> = env::vars_os()
                .map(|(name, _value)| name.to_string_lossy().into_owned())
                .collect();
            names.sort();
            fs::write(path, names.join("\n"))?;
            write_stdout(&ready_line())?;
            hold()
        }
        "grandchild" => {
            let path = file.ok_or_else(|| io::Error::other("grandchild needs a file"))?;
            let me = env::current_exe()?; // nosemgrep: rust.lang.security.current-exe.current-exe -- test-only binary, never shipped (M8 installer check, ADR-004); re-launches itself as the grandchild
                                          // The grandchild joins the job automatically (it is started by a member of it). Its
                                          // stdio is null so it does not keep the host's stdout pipe open.
            let mut command = Command::new(me);
            command.env_clear();
            command
                .arg("hold")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            let grandchild = command.spawn()?;
            // The PID is written BEFORE the ready line, so a test that has seen `Ready` can read
            // the file without polling.
            fs::write(path, grandchild.id().to_string())?;
            write_stdout(&ready_line())?;
            hold()
        }
        _ => Ok(ExitCode::from(64)),
    }
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1); // nosemgrep: rust.lang.security.args.args -- test-only binary, never shipped (M8 installer check, ADR-004); parses its own mode argument
    let Some(mode) = args.next() else {
        return ExitCode::from(64);
    };
    let file = args.next();
    run(&mode, file.as_deref()).unwrap_or(ExitCode::from(70))
}
