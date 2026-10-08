//! The health client must ignore proxy environment variables (CLAUDE.md 3.2: Rust clients use
//! `.no_proxy()`). This file is its own test binary, so changing the process environment here
//! cannot affect any other test.
//!
//! The test removes `NO_PROXY` and `no_proxy` first, so a bypass list inherited from the machine
//! cannot make it pass, then points every proxy variable at a loopback "proxy" that counts
//! connections. A client that honoured the environment would connect to the proxy instead of the
//! real server.

use std::error::Error;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;

use desktop_lib::health::{HealthClient, HealthReply, LoopbackEndpoint};
use desktop_lib::token::AuthToken;

/// A loopback listener that counts connections and answers each with a canned HTTP response.
struct Counter {
    port: u16,
    accepted: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Counter {
    fn start(response: &'static [u8]) -> Result<Self, Box<dyn Error>> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port();
        let accepted = Arc::new(AtomicUsize::new(0));
        let stop = Arc::new(AtomicBool::new(false));
        let (count, flag) = (accepted.clone(), stop.clone());
        let thread = std::thread::spawn(move || {
            while let Ok((mut stream, _)) = listener.accept() {
                if flag.load(Ordering::SeqCst) {
                    break;
                }
                count.fetch_add(1, Ordering::SeqCst);
                let mut buffer = [0u8; 2048];
                let _ = stream.read(&mut buffer);
                let _ = stream.write_all(response);
            }
        });
        Ok(Self {
            port,
            accepted,
            stop,
            thread: Some(thread),
        })
    }

    fn accepted(&self) -> usize {
        self.accepted.load(Ordering::SeqCst)
    }
}

impl Drop for Counter {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(("127.0.0.1", self.port));
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

const OK: &[u8] =
    b"HTTP/1.1 200 OK\r\nContent-Length: 15\r\nConnection: close\r\n\r\n{\"status\":\"ok\"}";
const PROXY_ANSWER: &[u8] =
    b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";

#[test]
fn health_client_ignores_proxy_env() -> Result<(), Box<dyn Error>> {
    let target = Counter::start(OK)?;
    let proxy = Counter::start(PROXY_ANSWER)?;

    for name in ["NO_PROXY", "no_proxy"] {
        std::env::remove_var(name);
    }
    let proxy_url = format!("http://127.0.0.1:{}", proxy.port);
    for name in [
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ] {
        std::env::set_var(name, &proxy_url);
    }

    let token = AuthToken::generate()?;
    let client = HealthClient::new(&token)?;
    let endpoint = LoopbackEndpoint::new(target.port).ok_or("port 0")?;
    let reply = tauri::async_runtime::block_on(client.check(&endpoint))?;

    assert_eq!(reply, HealthReply::Healthy);
    assert_eq!(
        target.accepted(),
        1,
        "the real server was not contacted directly"
    );
    assert_eq!(proxy.accepted(), 0, "the client sent traffic to the proxy");
    Ok(())
}
