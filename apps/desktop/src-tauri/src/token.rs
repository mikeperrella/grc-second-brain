//! The authentication token shared by the host and the sidecar (CLAUDE.md 3.4, ADR-002).
//!
//! The host generates 32 random bytes from the operating system's CSPRNG and hex-encodes them
//! as exactly 64 LOWERCASE characters, which is the only spelling the sidecar accepts. The value
//! is wrapped in [`AuthToken`] so that it cannot be printed or serialized by accident: `Debug`
//! and `Display` print a fixed placeholder, there is no `Clone`, `PartialEq` or `Serialize`, and
//! the only way to read the value is [`AuthToken::expose`], which is `pub(crate)` and has exactly
//! two intended callers (the child environment builder and, in M2c, the request header builder).
//!
//! The token never reaches the frontend: no command returns it and no event carries it.

use std::fmt;

/// Number of random bytes in a token.
pub(crate) const TOKEN_BYTES: usize = 32;
/// Length of the hex encoding: two characters per byte.
pub(crate) const TOKEN_HEX_LEN: usize = TOKEN_BYTES * 2;

/// Lowercase hex digits. Writing the table by hand (instead of `format!("{:02x}")` or a crate)
/// makes "lowercase only" true by construction and keeps the dependency list short.
const HEX_DIGITS: &[u8; 16] = b"0123456789abcdef";

/// Why a token could not be generated. A unit variant: no detail that could carry a secret.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum TokenError {
    #[error("the operating system could not provide random bytes")]
    RandomUnavailable,
}

/// A 64-character lowercase-hex authentication token. See the module documentation.
pub struct AuthToken(String);

impl AuthToken {
    /// Generate a fresh token from the OS CSPRNG (`ProcessPrng` on Windows).
    pub fn generate() -> Result<Self, TokenError> {
        let mut bytes = [0u8; TOKEN_BYTES];
        // The underlying error is dropped on purpose: it carries nothing we may show.
        getrandom::fill(&mut bytes).map_err(|_| TokenError::RandomUnavailable)?;
        Ok(Self::from_bytes(&bytes))
    }

    /// Hex-encode 32 bytes (lowercase). Separate from `generate` so tests can feed known bytes.
    fn from_bytes(bytes: &[u8; TOKEN_BYTES]) -> Self {
        let mut hex = String::with_capacity(TOKEN_HEX_LEN);
        for byte in bytes {
            hex.push(char::from(HEX_DIGITS[usize::from(byte >> 4)]));
            hex.push(char::from(HEX_DIGITS[usize::from(byte & 0x0f)]));
        }
        Self(hex)
    }

    /// The token text. Only code that must hand the token to the sidecar (its environment) or
    /// back to it (the `X-GRC-Auth` header) may call this. Never log or format the result.
    pub(crate) fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for AuthToken {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("AuthToken(<redacted>)")
    }
}

impl fmt::Display for AuthToken {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("<redacted>")
    }
}

/// True if `text` has the exact shape the sidecar accepts: 64 characters, each `0-9` or `a-f`.
/// This mirrors `_TOKEN_PATTERN = [0-9a-f]{64}` in `sidecar/src/grc_sidecar/security/auth.py`.
#[cfg(test)]
pub(crate) fn is_valid_token_shape(text: &str) -> bool {
    text.len() == TOKEN_HEX_LEN && text.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn generated_token_has_the_exact_shape() {
        let token = AuthToken::generate().unwrap();
        assert_eq!(token.expose().len(), 64);
        assert!(is_valid_token_shape(token.expose()));
    }

    #[test]
    fn token_is_lowercase_hex() {
        // Known bytes with both nibbles above 9, so an uppercase table would show immediately.
        let token = AuthToken::from_bytes(&[0xAB; TOKEN_BYTES]);
        assert_eq!(token.expose(), "ab".repeat(TOKEN_BYTES));
        // And the real generator: 1000 tokens, none may contain an uppercase letter.
        for _ in 0..1000 {
            let generated = AuthToken::generate().unwrap();
            assert!(
                !generated.expose().bytes().any(|b| b.is_ascii_uppercase()),
                "a generated token contained an uppercase character"
            );
        }
    }

    #[test]
    fn hex_encoding_matches_known_vectors() {
        let mut bytes = [0u8; TOKEN_BYTES];
        for (index, slot) in bytes.iter_mut().enumerate() {
            *slot = u8::try_from(index).unwrap();
        }
        let token = AuthToken::from_bytes(&bytes);
        assert_eq!(
            token.expose(),
            "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
        );
    }

    #[test]
    fn a_thousand_tokens_are_distinct_and_valid() {
        let mut seen = HashSet::new();
        for _ in 0..1000 {
            let token = AuthToken::generate().unwrap();
            assert!(is_valid_token_shape(token.expose()));
            assert!(
                seen.insert(token.expose().to_owned()),
                "duplicate token generated"
            );
        }
        assert_eq!(seen.len(), 1000);
    }

    #[test]
    fn shape_validator_rejects_everything_the_sidecar_rejects() {
        let good = "0123456789abcdef".repeat(4);
        assert!(is_valid_token_shape(&good));
        assert!(
            !is_valid_token_shape(&good.to_uppercase()),
            "uppercase hex must be rejected"
        );
        assert!(!is_valid_token_shape(&good[..63]), "63 characters");
        assert!(!is_valid_token_shape(&format!("{good}0")), "65 characters");
        assert!(
            !is_valid_token_shape(&format!("{}g", &good[..63])),
            "non-hex character"
        );
        assert!(
            !is_valid_token_shape(&format!(" {}", &good[..63])),
            "leading space"
        );
        assert!(!is_valid_token_shape(""), "empty");
    }

    #[test]
    fn debug_and_display_are_redacted() {
        #[derive(Debug)]
        struct Holder {
            token: AuthToken,
        }
        let token = AuthToken::generate().unwrap();
        let secret = token.expose().to_owned();
        let holder = Holder { token };

        let outputs = [
            format!("{:?}", holder.token),
            format!("{:#?}", holder.token),
            format!("{}", holder.token),
            format!("{holder:?}"),
            format!("{holder:#?}"),
        ];
        for output in outputs {
            assert!(
                !output.contains(&secret),
                "token value leaked into: {output}"
            );
            assert!(
                output.contains("redacted"),
                "no redaction marker in: {output}"
            );
        }
    }

    /// Every variant of `TokenError`. The inner match has no wildcard, so adding a variant breaks
    /// compilation here until it is listed and therefore covered by the test below.
    fn every_token_error() -> Vec<TokenError> {
        fn exhaustive(error: TokenError) {
            match error {
                TokenError::RandomUnavailable => {}
            }
        }
        exhaustive(TokenError::RandomUnavailable);
        vec![TokenError::RandomUnavailable]
    }

    #[test]
    fn every_error_variant_formats_without_a_token() {
        // `TokenError` does not implement `Serialize`. If that ever changes, add a
        // `serde_json::to_string` of every variant to the loop below.
        let token = AuthToken::generate().unwrap();
        for error in every_token_error() {
            for text in [
                format!("{error}"),
                format!("{error:?}"),
                format!("{error:#?}"),
            ] {
                assert!(!text.is_empty());
                assert!(!text.contains(token.expose()), "token leaked into: {text}");
            }
        }
    }

    #[test]
    fn error_type_has_no_payload_so_it_cannot_carry_a_token() {
        // A one-variant enum without fields is zero-sized. Adding any field (for example a
        // `detail: String`) makes it larger, and this test goes red. That is the property that
        // matters: the error value is structurally unable to hold a secret.
        assert_eq!(std::mem::size_of::<TokenError>(), 0);
        let message = TokenError::RandomUnavailable.to_string();
        assert_eq!(
            message,
            "the operating system could not provide random bytes"
        );
    }
}
