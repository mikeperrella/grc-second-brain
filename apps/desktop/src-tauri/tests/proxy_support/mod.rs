//! A small loopback HTTP server for the proxy's integration tests. It records every request (head
//! and body) and answers each connection with the same canned bytes. Each test binary includes it
//! with `mod proxy_support;`. (The unit tests in `src/proxy/` have a fuller copy in `test_support.rs`;
//! a `#[cfg(test)]` module of the library is not visible to files under `tests/`.)

#![allow(
    dead_code,
    reason = "each integration test crate uses a different subset of these helpers"
)]

use std::error::Error;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

pub struct Server {
    pub port: u16,
    requests: Arc<Mutex<Vec<String>>>,
    accepted: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Server {
    pub fn start(answer: Vec<u8>) -> Result<Self, Box<dyn Error>> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port();
        let requests = Arc::new(Mutex::new(Vec::new()));
        let accepted = Arc::new(AtomicUsize::new(0));
        let stop = Arc::new(AtomicBool::new(false));
        let (log, count, flag) = (requests.clone(), accepted.clone(), stop.clone());
        let thread = std::thread::spawn(move || {
            while let Ok((stream, _)) = listener.accept() {
                if flag.load(Ordering::SeqCst) {
                    break;
                }
                count.fetch_add(1, Ordering::SeqCst);
                serve(stream, &answer, &log);
            }
        });
        Ok(Self {
            port,
            requests,
            accepted,
            stop,
            thread: Some(thread),
        })
    }

    /// The raw text of every request received, one entry per connection.
    pub fn requests(&self) -> Vec<String> {
        self.requests
            .lock()
            .map(|requests| requests.clone())
            .unwrap_or_default()
    }

    pub fn accepted(&self) -> usize {
        self.accepted.load(Ordering::SeqCst)
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(("127.0.0.1", self.port));
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn serve(mut stream: TcpStream, answer: &[u8], requests: &Mutex<Vec<String>>) {
    let mut request = Vec::new();
    let mut buffer = [0u8; 1024];
    while !request.windows(4).any(|window| window == b"\r\n\r\n") && request.len() < 64 * 1024 {
        match stream.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => request.extend_from_slice(&buffer[..count]),
        }
    }
    // Read the body too when Content-Length says there is one, so a test can see what was sent.
    let head_end = request
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map_or(request.len(), |at| at + 4);
    let wanted = String::from_utf8_lossy(&request[..head_end])
        .to_ascii_lowercase()
        .lines()
        .find_map(|line| {
            line.strip_prefix("content-length: ")?
                .trim()
                .parse::<usize>()
                .ok()
        })
        .unwrap_or(0);
    while request.len() < head_end + wanted {
        match stream.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => request.extend_from_slice(&buffer[..count]),
        }
    }
    if let Ok(mut log) = requests.lock() {
        log.push(String::from_utf8_lossy(&request).into_owned());
    }
    let _ = stream.write_all(answer);
    let _ = stream.flush();
}

/// A complete `Connection: close` response with a JSON body.
pub fn json_response(status_line: &str, body: &str) -> Vec<u8> {
    format!(
        "HTTP/1.1 {status_line}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
    .into_bytes()
}
