//! A one-thread HTTP server on 127.0.0.1 for the proxy's unit tests. It records every request head
//! it receives and answers each connection with a canned reply (the same shape as the one in
//! `health.rs`'s tests; the integration tests under `tests/` carry their own small copy).

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

use crate::sidecar_client::LoopbackEndpoint;

/// What the fake server does after reading a request.
pub(crate) enum Reply {
    /// Write these bytes and close.
    Bytes(Vec<u8>),
    /// Say nothing and keep the connection open until the client closes it.
    HoldOpen,
}

pub(crate) struct FakeServer {
    pub(crate) port: u16,
    requests: Arc<Mutex<Vec<String>>>,
    accepted: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl FakeServer {
    pub(crate) fn start(reply: Reply) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let requests = Arc::new(Mutex::new(Vec::new()));
        let accepted = Arc::new(AtomicUsize::new(0));
        let stop = Arc::new(AtomicBool::new(false));
        let (r, a, s) = (requests.clone(), accepted.clone(), stop.clone());
        let thread = std::thread::spawn(move || {
            while let Ok((stream, _)) = listener.accept() {
                if s.load(Ordering::SeqCst) {
                    break;
                }
                a.fetch_add(1, Ordering::SeqCst);
                serve(stream, &reply, &r);
            }
        });
        Self {
            port,
            requests,
            accepted,
            stop,
            thread: Some(thread),
        }
    }

    pub(crate) fn endpoint(&self) -> LoopbackEndpoint {
        LoopbackEndpoint::new(self.port).unwrap()
    }

    /// The raw request head and any body bytes read with it, one entry per connection.
    pub(crate) fn requests(&self) -> Vec<String> {
        self.requests.lock().unwrap().clone()
    }

    pub(crate) fn accepted(&self) -> usize {
        self.accepted.load(Ordering::SeqCst)
    }
}

impl Drop for FakeServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        // Wake the blocked `accept` so the thread can see the flag and end.
        let _ = TcpStream::connect(("127.0.0.1", self.port));
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn serve(mut stream: TcpStream, reply: &Reply, requests: &Mutex<Vec<String>>) {
    let mut request = Vec::new();
    let mut buffer = [0u8; 1024];
    // Read the head, then the body if Content-Length says there is one.
    while !request.windows(4).any(|w| w == b"\r\n\r\n") && request.len() < 64 * 1024 {
        match stream.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => request.extend_from_slice(&buffer[..count]),
        }
    }
    let head_end = request
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map_or(request.len(), |at| at + 4);
    let head = String::from_utf8_lossy(&request[..head_end]).to_ascii_lowercase();
    let wanted = head
        .lines()
        .find_map(|line| line.strip_prefix("content-length: "))
        .and_then(|value| value.trim().parse::<usize>().ok())
        .unwrap_or(0);
    while request.len() < head_end + wanted {
        match stream.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => request.extend_from_slice(&buffer[..count]),
        }
    }
    requests
        .lock()
        .unwrap()
        .push(String::from_utf8_lossy(&request).into_owned());
    match reply {
        Reply::Bytes(bytes) => {
            let _ = stream.write_all(bytes);
            let _ = stream.flush();
        }
        Reply::HoldOpen => while matches!(stream.read(&mut buffer), Ok(n) if n > 0) {},
    }
}

pub(crate) fn response(status_line: &str, extra_headers: &[&str], body: &[u8]) -> Reply {
    let mut bytes = format!("HTTP/1.1 {status_line}\r\n").into_bytes();
    for header in extra_headers {
        bytes.extend_from_slice(format!("{header}\r\n").as_bytes());
    }
    bytes.extend_from_slice(format!("Content-Length: {}\r\n\r\n", body.len()).as_bytes());
    bytes.extend_from_slice(body);
    Reply::Bytes(bytes)
}

pub(crate) fn chunked(body: &[u8]) -> Reply {
    let mut bytes = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n".to_vec();
    for piece in body.chunks(1000) {
        bytes.extend_from_slice(format!("{:x}\r\n", piece.len()).as_bytes());
        bytes.extend_from_slice(piece);
        bytes.extend_from_slice(b"\r\n");
    }
    bytes.extend_from_slice(b"0\r\n\r\n");
    Reply::Bytes(bytes)
}
