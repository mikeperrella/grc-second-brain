import pytest

from grc_sidecar.errors import FileTooLargeError, InvalidEncodingError
from grc_sidecar.vault.normalize import (
    MAX_FILE_BYTES,
    decode_and_normalize,
    normalize_text,
    sha256_hex,
    split_lines,
)

from .leakcheck import assert_no_leak

LS, NEL = chr(0x2028), chr(0x85)


def test_line_endings_and_bom_are_normalized() -> None:
    assert normalize_text("a\r\nb\rc\n") == "a\nb\nc\n"
    assert decode_and_normalize(b"\xef\xbb\xbfa\r\nb") == "a\nb"


def test_identical_content_gives_identical_hash_across_platform_encodings() -> None:
    variants = [b"a\nb\n", b"a\r\nb\r\n", b"\xef\xbb\xbfa\r\nb\r\n", b"a\rb\r"]
    hashes = {sha256_hex(decode_and_normalize(raw)) for raw in variants}
    assert len(hashes) == 1


def test_lines_split_on_newline_only_never_on_unicode_separators() -> None:
    one = f"one{LS}still one{NEL}also one\x0bstill\x0cone"
    text = one + "\ntwo\n"
    assert split_lines(text) == (one, "two")


def test_a_trailing_newline_adds_no_empty_line() -> None:
    assert split_lines("a\n") == ("a",)
    assert split_lines("a\n\n") == ("a", "")
    assert split_lines("") == ()


def test_invalid_utf8_is_an_error_without_leaking_bytes() -> None:  # [NEG]
    secret = "marker-" + "value"  # built at run time so this file never contains it whole
    with pytest.raises(InvalidEncodingError) as caught:
        decode_and_normalize(b"caf\xe9 " + secret.encode(), "a.md")
    assert caught.value.source_path == "a.md"
    assert caught.value.code == "invalid_encoding"
    assert_no_leak(caught.value, secret)


def test_a_nul_byte_is_an_error() -> None:  # [NEG]
    with pytest.raises(InvalidEncodingError):
        decode_and_normalize(b"abc\x00def", "a.md")


def test_an_oversized_file_is_rejected_before_decoding() -> None:  # [NEG]
    with pytest.raises(FileTooLargeError) as caught:
        decode_and_normalize(b"a" * (MAX_FILE_BYTES + 1), "big.md")
    assert caught.value.code == "file_too_large"


def test_a_file_exactly_at_the_cap_is_accepted() -> None:
    assert len(decode_and_normalize(b"a" * MAX_FILE_BYTES)) == MAX_FILE_BYTES


def test_text_is_never_unicode_normalized() -> None:
    combining = "e" + chr(0x301)  # e + combining acute: must stay two code points
    assert normalize_text(combining) == combining
