//! The sidecar's ready line (CLAUDE.md 3.4, ADR-002, M2 plan sections 1 and 6).
//!
//! After binding, the sidecar prints exactly one line to stdout: `{"event":"ready","port":N}`.
//! On Windows Python translates `\n` to `\r\n` on stdout, so the raw bytes on the pipe end in
//! `\r\n` (verified from a binary pipe). This module accepts both endings.
//!
//! Two pieces, both pure (no processes, no I/O beyond the reader you pass in):
//! * [`read_ready_line`] reads ONE line from a buffered reader and never buffers more than
//!   [`MAX_READY_LINE`] bytes for it, so a broken or hostile child that streams bytes without a
//!   newline cannot exhaust memory.
//! * [`parse_ready_line`] parses it strictly: exactly the two fields, the event must be
//!   `"ready"`, the port must be an integer in 1..=65535, and nothing else may surround the JSON.
//!
//! The reader borrows a `BufRead` instead of owning the stream, on purpose: bytes the sidecar
//! printed after the ready line may already sit in the caller's buffer, and the caller (M2b) must
//! still see them, because any output after the ready line is a contract violation.

use std::io::{BufRead, Read};
use std::num::NonZeroU16;

use serde::Deserialize;

/// Largest accepted ready line, terminator included. The real line is about 32 bytes.
pub const MAX_READY_LINE: usize = 1024;

/// Why the ready line was rejected. Unit variants only: no raw sidecar output is ever carried.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum ReadyError {
    #[error("the sidecar closed stdout before sending a complete ready line")]
    EofBeforeReady,
    #[error("the ready line is longer than the size limit")]
    LineTooLong,
    #[error("the ready line is not a single valid JSON object")]
    NotJson,
    #[error("the ready line has unexpected fields or values")]
    WrongFields,
    #[error("the ready line port is outside 1 to 65535")]
    PortOutOfRange,
    #[error("reading the ready line failed")]
    ReadFailed,
}

/// The one event the sidecar may announce, compared exactly (case-sensitive).
const READY_EVENT: &str = "ready";

/// The ready line as JSON. `deny_unknown_fields` rejects any extra field; a missing field fails
/// too. `event` is read as a string (so a non-string value is a type error, `WrongFields`) and
/// compared with [`READY_EVENT`]. The port is read as a raw JSON number so range errors can be
/// told apart from type errors.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Ready {
    event: String,
    port: serde_json::Number,
}

/// Read one line (up to and including `\n`) and parse it. At most `MAX_READY_LINE + 1` bytes are
/// taken from `reader`; anything the reader buffered beyond the line stays in the reader.
pub fn read_ready_line<R: BufRead>(reader: &mut R) -> Result<NonZeroU16, ReadyError> {
    let limit = u64::try_from(MAX_READY_LINE + 1).unwrap_or(u64::MAX);
    let mut limited = reader.by_ref().take(limit);
    let mut line = Vec::new();
    limited
        .read_until(b'\n', &mut line)
        .map_err(|_| ReadyError::ReadFailed)?;

    if line.len() > MAX_READY_LINE {
        return Err(ReadyError::LineTooLong);
    }
    if line.last() != Some(&b'\n') {
        // End of stream before a newline: nothing, or only part of a line.
        return Err(ReadyError::EofBeforeReady);
    }
    parse_ready_line(&line)
}

/// Parse a ready line that still carries its terminator (`\n` or `\r\n`).
pub fn parse_ready_line(raw: &[u8]) -> Result<NonZeroU16, ReadyError> {
    let payload = raw
        .strip_suffix(b"\r\n")
        .or_else(|| raw.strip_suffix(b"\n"))
        .ok_or(ReadyError::EofBeforeReady)?;

    // serde_json tolerates whitespace around a value; this contract does not. The sidecar prints
    // compact JSON, so anything else (including a stray `\r`) is a violation.
    if payload.first() != Some(&b'{') || payload.last() != Some(&b'}') {
        return Err(ReadyError::NotJson);
    }

    let ready: Ready = serde_json::from_slice(payload).map_err(|error| match error.classify() {
        serde_json::error::Category::Data => ReadyError::WrongFields,
        _ => ReadyError::NotJson,
    })?;
    if ready.event != READY_EVENT {
        return Err(ReadyError::WrongFields);
    }

    port_from_number(&ready.port)
}

fn port_from_number(number: &serde_json::Number) -> Result<NonZeroU16, ReadyError> {
    if let Some(signed) = number.as_i64() {
        let port = u16::try_from(signed).map_err(|_| ReadyError::PortOutOfRange)?;
        NonZeroU16::new(port).ok_or(ReadyError::PortOutOfRange)
    } else if number.as_u64().is_some() {
        // An integer above i64::MAX but within u64: certainly not a port.
        Err(ReadyError::PortOutOfRange)
    } else {
        // Anything that is not an exact integer: a fraction such as 80.5, an exponent such as
        // 1e3, or an integer above u64::MAX (serde_json stores those as floats). Wrong type,
        // not just wrong range.
        Err(ReadyError::WrongFields)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{self, BufReader, Cursor};

    fn port(value: u16) -> NonZeroU16 {
        NonZeroU16::new(value).unwrap()
    }

    fn parse(text: &str) -> Result<NonZeroU16, ReadyError> {
        parse_ready_line(text.as_bytes())
    }

    #[test]
    fn valid_line_with_lf() {
        assert_eq!(
            parse("{\"event\":\"ready\",\"port\":62273}\n"),
            Ok(port(62273))
        );
    }

    #[test]
    fn ready_rejects_a_json_array() {
        // serde fills a struct from a JSON array too (`["ready",5000]` as event and port), so
        // nothing about the derived struct alone rejects it. The leading `{` and trailing `}`
        // check in `parse_ready_line` does, and this test pins that.
        for line in [
            "[\"ready\",5000]\n",
            "[\"ready\",5000]\r\n",
            "[\"ready\",5000,1]\n",
            "[\"ready\"]\n",
            "[]\n",
        ] {
            assert_eq!(parse(line), Err(ReadyError::NotJson), "{line:?}");
        }
    }

    #[test]
    fn ready_accepts_crlf() {
        // The real bytes on a Windows pipe (verified): `...}\r\n`.
        assert_eq!(
            parse("{\"event\":\"ready\",\"port\":62273}\r\n"),
            Ok(port(62273))
        );
    }

    #[test]
    fn port_boundaries() {
        assert_eq!(parse("{\"event\":\"ready\",\"port\":1}\n"), Ok(port(1)));
        assert_eq!(
            parse("{\"event\":\"ready\",\"port\":65535}\n"),
            Ok(port(65535))
        );
    }

    #[test]
    fn ready_rejects_port_zero() {
        assert_eq!(
            parse("{\"event\":\"ready\",\"port\":0}\n"),
            Err(ReadyError::PortOutOfRange)
        );
    }

    #[test]
    fn ports_outside_the_range_are_rejected() {
        for text in [
            "{\"event\":\"ready\",\"port\":65536}\n",
            "{\"event\":\"ready\",\"port\":-1}\n",
            "{\"event\":\"ready\",\"port\":99999999999}\n",
            "{\"event\":\"ready\",\"port\":18446744073709551615}\n",
        ] {
            assert_eq!(parse(text), Err(ReadyError::PortOutOfRange), "{text}");
        }
    }

    #[test]
    fn numbers_that_are_not_exact_integers_are_wrong_fields() {
        for text in [
            "{\"event\":\"ready\",\"port\":5000.0}\n",
            "{\"event\":\"ready\",\"port\":5e3}\n",
            "{\"event\":\"ready\",\"port\":99999999999999999999999}\n",
        ] {
            assert_eq!(parse(text), Err(ReadyError::WrongFields), "{text}");
        }
    }

    #[test]
    fn ready_rejects_extra_field() {
        assert_eq!(
            parse("{\"event\":\"ready\",\"port\":5000,\"token\":\"x\"}\n"),
            Err(ReadyError::WrongFields)
        );
    }

    #[test]
    fn wrong_event_missing_fields_and_wrong_types_are_rejected() {
        for text in [
            "{\"event\":\"READY\",\"port\":5000}\n",
            "{\"event\":\"started\",\"port\":5000}\n",
            "{\"event\":\"ready\"}\n",
            "{\"port\":5000}\n",
            "{\"event\":\"ready\",\"port\":\"5000\"}\n",
            "{\"event\":\"ready\",\"port\":80.5}\n",
            "{\"event\":\"ready\",\"port\":null}\n",
            "{\"event\":1,\"port\":5000}\n",
            "{}\n",
        ] {
            assert_eq!(parse(text), Err(ReadyError::WrongFields), "{text}");
        }
    }

    #[test]
    fn non_json_and_empty_lines_are_rejected() {
        for text in [
            "ready\n",
            "\n",
            "\r\n",
            "{\"event\":\"ready\",\"port\":5000\n",
            "[1,2,3]\n",
            "null\n",
            "\"ready\"\n",
            "5000\n",
        ] {
            assert_eq!(parse(text), Err(ReadyError::NotJson), "{text:?}");
        }
    }

    #[test]
    fn two_json_objects_on_one_line_are_rejected() {
        assert_eq!(
            parse("{\"event\":\"ready\",\"port\":1}{\"event\":\"ready\",\"port\":2}\n"),
            Err(ReadyError::NotJson)
        );
    }

    #[test]
    fn surrounding_whitespace_is_rejected() {
        for text in [
            " {\"event\":\"ready\",\"port\":5000}\n",
            "{\"event\":\"ready\",\"port\":5000} \n",
            "\t{\"event\":\"ready\",\"port\":5000}\n",
            // A doubled carriage return leaves one `\r` behind after the terminator is removed.
            "{\"event\":\"ready\",\"port\":5000}\r\r\n",
        ] {
            assert_eq!(parse(text), Err(ReadyError::NotJson), "{text:?}");
        }
    }

    #[test]
    fn a_line_without_a_terminator_is_not_complete() {
        assert_eq!(
            parse("{\"event\":\"ready\",\"port\":5000}"),
            Err(ReadyError::EofBeforeReady)
        );
    }

    #[test]
    fn reader_returns_the_port_and_leaves_later_bytes_in_the_reader() {
        let data = b"{\"event\":\"ready\",\"port\":4242}\r\nextra output\n";
        let mut reader = BufReader::new(Cursor::new(&data[..]));
        assert_eq!(read_ready_line(&mut reader), Ok(port(4242)));
        // The bytes after the ready line were NOT consumed: M2b needs them to detect a violation.
        let mut rest = String::new();
        reader.read_line(&mut rest).unwrap();
        assert_eq!(rest, "extra output\n");
    }

    #[test]
    fn empty_stream_is_eof_before_ready() {
        let mut reader = BufReader::new(Cursor::new(Vec::<u8>::new()));
        assert_eq!(
            read_ready_line(&mut reader),
            Err(ReadyError::EofBeforeReady)
        );
    }

    #[test]
    fn partial_line_then_eof_is_eof_before_ready() {
        let mut reader = BufReader::new(Cursor::new(b"{\"event\":\"ready\"".to_vec()));
        assert_eq!(
            read_ready_line(&mut reader),
            Err(ReadyError::EofBeforeReady)
        );
    }

    #[test]
    fn a_line_of_exactly_the_maximum_length_is_accepted_and_one_more_is_not() {
        // Build `{"event":"ready","port":5000}` and pad it to the exact limit with a JSON-legal
        // field? Padding would add a field, so instead test the size limit through length alone:
        // MAX bytes ending in a newline pass the length check (and then fail parsing as NotJson),
        // MAX + 1 bytes fail the length check first.
        let mut at_limit = vec![b'a'; MAX_READY_LINE - 1];
        at_limit.push(b'\n');
        let mut reader = BufReader::new(Cursor::new(at_limit));
        assert_eq!(read_ready_line(&mut reader), Err(ReadyError::NotJson));

        let mut over_limit = vec![b'a'; MAX_READY_LINE];
        over_limit.push(b'\n');
        let mut reader = BufReader::new(Cursor::new(over_limit));
        assert_eq!(read_ready_line(&mut reader), Err(ReadyError::LineTooLong));
    }

    /// A reader that hands out `b'a'` forever and counts what it served. Once it has served more
    /// than `fail_after` bytes it returns an error instead, so a missing size cap makes a test
    /// FAIL quickly and deterministically rather than hang or exhaust memory.
    struct EndlessReader {
        served: usize,
        fail_after: usize,
    }

    impl Read for EndlessReader {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if self.served > self.fail_after {
                return Err(io::Error::other(
                    "test reader: the code under test read too much",
                ));
            }
            buffer.fill(b'a');
            self.served += buffer.len();
            Ok(buffer.len())
        }
    }

    #[test]
    fn oversized_line_is_rejected_within_cap() {
        const BUFFER: usize = 64;
        let source = EndlessReader {
            served: 0,
            fail_after: 16 * MAX_READY_LINE,
        };
        let mut reader = BufReader::with_capacity(BUFFER, source);

        let result = read_ready_line(&mut reader);

        assert_eq!(
            result,
            Err(ReadyError::LineTooLong),
            "the cap must produce LineTooLong"
        );
        let served = reader.get_ref().served;
        // The cap is MAX + 1 bytes; the BufReader may have pulled at most one extra buffer.
        assert!(
            served <= MAX_READY_LINE + 1 + BUFFER,
            "read {served} bytes for one line; the cap is {MAX_READY_LINE}"
        );
    }

    #[test]
    fn read_errors_are_reported_without_detail() {
        struct Broken;
        impl Read for Broken {
            fn read(&mut self, _buffer: &mut [u8]) -> io::Result<usize> {
                Err(io::Error::other("secret detail that must not be forwarded"))
            }
        }
        let mut reader = BufReader::new(Broken);
        let error = read_ready_line(&mut reader).unwrap_err();
        assert_eq!(error, ReadyError::ReadFailed);
        assert!(!error.to_string().contains("secret"));
    }

    #[test]
    fn port_inputs_map_to_the_documented_variants() {
        // (port as written in the JSON, expected result). One row per input class.
        let cases: [(&str, Result<NonZeroU16, ReadyError>); 9] = [
            ("0", Err(ReadyError::PortOutOfRange)),
            ("65536", Err(ReadyError::PortOutOfRange)),
            ("-1", Err(ReadyError::PortOutOfRange)),
            ("1.5", Err(ReadyError::WrongFields)),
            // u64::MAX still fits an integer type, so it is an out-of-range port.
            ("18446744073709551615", Err(ReadyError::PortOutOfRange)),
            // u64::MAX + 1: serde_json stores it as a float, so it is a wrong-type value.
            ("18446744073709551616", Err(ReadyError::WrongFields)),
            ("99999999999999999999999", Err(ReadyError::WrongFields)),
            ("1", Ok(port(1))),
            ("65535", Ok(port(65535))),
        ];
        for (written, expected) in cases {
            let line = format!("{{\"event\":\"ready\",\"port\":{written}}}\n");
            assert_eq!(
                parse_ready_line(line.as_bytes()),
                expected,
                "port {written}"
            );
        }
    }

    /// Every variant of `ReadyError`. The inner match has no wildcard, so adding a variant breaks
    /// compilation here until it is listed.
    fn every_ready_error() -> Vec<ReadyError> {
        fn exhaustive(error: ReadyError) {
            match error {
                ReadyError::EofBeforeReady
                | ReadyError::LineTooLong
                | ReadyError::NotJson
                | ReadyError::WrongFields
                | ReadyError::PortOutOfRange
                | ReadyError::ReadFailed => {}
            }
        }
        let all = vec![
            ReadyError::EofBeforeReady,
            ReadyError::LineTooLong,
            ReadyError::NotJson,
            ReadyError::WrongFields,
            ReadyError::PortOutOfRange,
            ReadyError::ReadFailed,
        ];
        for error in &all {
            exhaustive(*error);
        }
        all
    }

    #[test]
    fn every_error_variant_is_reachable_through_the_public_api() {
        // An input that really produces each variant. A variant that no input can produce is dead
        // code and must be removed; a new variant needs a producing input added here.
        struct Broken;
        impl Read for Broken {
            fn read(&mut self, _buffer: &mut [u8]) -> io::Result<usize> {
                Err(io::Error::other("unused"))
            }
        }
        let mut too_long = vec![b'a'; MAX_READY_LINE];
        too_long.push(b'\n');

        let produced = [
            parse_ready_line(b"{\"event\":\"ready\",\"port\":5000}").unwrap_err(),
            read_ready_line(&mut BufReader::new(Cursor::new(too_long))).unwrap_err(),
            parse_ready_line(b"ready\n").unwrap_err(),
            parse_ready_line(b"{\"event\":\"ready\",\"port\":5000,\"x\":1}\n").unwrap_err(),
            parse_ready_line(b"{\"event\":\"ready\",\"port\":0}\n").unwrap_err(),
            read_ready_line(&mut BufReader::new(Broken)).unwrap_err(),
        ];
        for variant in every_ready_error() {
            assert!(
                produced.contains(&variant),
                "no input produces {variant:?}: it is dead code"
            );
        }
    }

    #[test]
    fn every_error_variant_formats_without_sidecar_output_or_a_token() {
        // `ReadyError` does not implement `Serialize`. If that ever changes, add a
        // `serde_json::to_string` of every variant to the loop below.
        let token = crate::token::AuthToken::generate().unwrap();
        for error in every_ready_error() {
            for text in [
                format!("{error}"),
                format!("{error:?}"),
                format!("{error:#?}"),
            ] {
                assert!(!text.is_empty());
                assert!(!text.contains(token.expose()), "token leaked into: {text}");
                assert!(!text.contains("event"), "line content leaked into: {text}");
            }
        }
    }

    #[test]
    fn error_type_has_no_payload_so_it_cannot_carry_sidecar_output() {
        // Unit variants only, so the enum fits in one byte. A variant holding the offending line
        // (a `String` or `Vec<u8>`) would make it larger and this test goes red.
        assert!(std::mem::size_of::<ReadyError>() <= 1);
    }
}
