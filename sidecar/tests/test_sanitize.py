"""Cleaning text before it goes into a cell (ADR-014): what is removed, what is not altered."""

import pytest
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils.exceptions import IllegalCharacterError

from grc_sidecar.questionnaire.sanitize import (
    FORMULA_TRIGGERS,
    MAX_CELL_CHARS,
    TRUNCATION_MARK,
    clean_cell_text,
    contains_direction_controls,
    needs_quote_prefix,
    remove_direction_controls,
)


def test_every_character_openpyxl_refuses_is_removed() -> None:  # [NEG]
    refused = [chr(code) for code in range(0x20) if ILLEGAL_CHARACTERS_RE.search(chr(code))]
    assert refused  # the comparison is not vacuous
    for character in refused:
        assert clean_cell_text(f"a{character}b") == "ab"
    sheet = Workbook().active
    assert sheet is not None
    for character in refused:
        with pytest.raises(IllegalCharacterError):
            sheet["A1"] = f"a{character}b"  # what would happen without the cleaning


def test_a_cleaned_text_is_accepted_by_openpyxl_for_every_control_character() -> None:
    sheet = Workbook().active
    assert sheet is not None
    everything = "".join(chr(code) for code in range(0x20))
    sheet["A1"] = clean_cell_text(f"x{everything}y")  # raises if anything unsafe is left


@pytest.mark.parametrize("text", ["a\ud800b", "a\udfffb", "a￾b", "a￿b"])
def test_lone_surrogates_and_non_characters_are_removed(text: str) -> None:  # [NEG]
    assert clean_cell_text(text) == "ab"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("a\r\nb", "a\nb"), ("a\rb", "a\nb"), ("a\nb", "a\nb"), ("\r\n\r\n", "\n\n")],
)
def test_line_endings_become_line_feeds(text: str, expected: str) -> None:
    # A parser turns CR into LF when the file is read back, and CRLF into two, so the text written
    # would not equal the text read. Normalizing first makes them equal.
    assert clean_cell_text(text) == expected


def test_a_tab_a_newline_and_ordinary_text_are_not_altered() -> None:
    text = "Line one\n\tindented, with “quotes”, Ünïcode, 日本語, emoji 😀, and - a dash"
    assert clean_cell_text(text) == text


def test_the_text_is_capped_below_the_excel_limit_with_a_marker() -> None:  # [NEG]
    long = "x" * (MAX_CELL_CHARS + 500)
    capped = clean_cell_text(long)
    assert len(capped) == MAX_CELL_CHARS
    assert capped.endswith(TRUNCATION_MARK)
    assert MAX_CELL_CHARS < 32_767  # Excel's own limit


def test_a_text_at_the_cap_is_not_marked() -> None:
    exact = "x" * MAX_CELL_CHARS
    assert clean_cell_text(exact) == exact


def test_cleaning_twice_changes_nothing() -> None:
    once = clean_cell_text("a\r\nb\x00" + "y" * (MAX_CELL_CHARS + 10))
    assert clean_cell_text(once) == once


DIRECTION_CONTROLS = [
    *(chr(code) for code in range(0x202A, 0x202F)),
    *(chr(code) for code in range(0x2066, 0x206A)),
    "‎",
    "‏",
    "؜",
]


@pytest.mark.parametrize("control", DIRECTION_CONTROLS, ids=lambda c: f"U+{ord(c):04X}")
def test_each_direction_control_is_removed_from_model_text(control: str) -> None:  # [NEG]
    assert remove_direction_controls(f"Yes{control}, encrypted") == "Yes, encrypted"
    assert contains_direction_controls(f"a{control}b")


def test_the_direction_control_set_is_exactly_the_twelve_named() -> None:
    assert len(DIRECTION_CONTROLS) == 12


@pytest.mark.parametrize(
    "text",
    ["plain", "عربي (Arabic letters)", "a‍b", "emoji 👨‍👩 joined", "a​b", ""],
)
def test_ordinary_right_to_left_letters_and_joiners_are_not_direction_controls(
    text: str,
) -> None:
    # The controls are removed; the letters of right-to-left languages and joiners are not.
    assert remove_direction_controls(text) == text
    assert not contains_direction_controls(text)


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_each_formula_trigger_needs_the_quote_prefix(trigger: str) -> None:  # [NEG]
    assert trigger in FORMULA_TRIGGERS
    assert needs_quote_prefix(clean_cell_text(f"{trigger}cmd|' /C calc'!A0"))


@pytest.mark.parametrize("text", ["ok = fine", "a+b", "email@example.invalid", " =x", "", "Yes."])
def test_text_that_does_not_start_with_a_trigger_does_not(text: str) -> None:
    assert not needs_quote_prefix(clean_cell_text(text))


def test_a_leading_carriage_return_is_still_caught_after_it_becomes_a_line_feed() -> None:
    cleaned = clean_cell_text("\r=1+1")
    assert cleaned.startswith("\n")
    assert needs_quote_prefix(cleaned)
