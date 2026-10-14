"""The Review vocabulary (ADR-017 decision 4): what counts as `approved`, and what never does.

Every invisible or look-alike character is built with chr(), so no such character sits in this
file's source (a hygiene test checks that).
"""

import datetime

import pytest

from grc_sidecar.ledger.review import MAX_OTHER_CHARS, classify_review, shorten_other

NBSP = chr(0xA0)
ZERO_WIDTH_SPACE = chr(0x200B)
ZERO_WIDTH_JOINER = chr(0x200D)
IDEOGRAPHIC_SPACE = chr(0x3000)
CYRILLIC_A = chr(0x430)  # looks like the Latin "a"
CYRILLIC_E = chr(0x435)  # looks like the Latin "e"
FULLWIDTH_A = chr(0xFF41)
SHARP_S = chr(0xDF)


@pytest.mark.parametrize(
    "value",
    [
        "approved",
        "Approved",
        "APPROVED",
        "aPpRoVeD",
        "approved ",  # a trailing space is accepted (the owner's rule, ADR-017 decision 4)
        " approved",
        "approved\t",  # and so is a trailing tab
        "\tapproved \t ",
        "  APPROVED  ",
    ],
)
def test_ascii_case_and_ascii_spaces_and_tabs_around_the_word_are_accepted(value: str) -> None:
    assert classify_review(value) == "approved"


@pytest.mark.parametrize(
    "value",
    [
        "appr" + "o" + "ved".replace("e", CYRILLIC_E),  # a Cyrillic "e" inside the word
        CYRILLIC_A + "pproved",  # a Cyrillic "a" at the start
        FULLWIDTH_A + "pproved",
        "approved" + NBSP,  # a non-breaking space is not an ASCII space
        NBSP + "approved",
        "approved" + ZERO_WIDTH_SPACE,
        "appro" + ZERO_WIDTH_JOINER + "ved",
        "approved" + IDEOGRAPHIC_SPACE,
        "approved yes",  # an extra word
        "approved\nyes",
        "approved\n",  # a newline is not an ASCII space or tab, so it is not forgiven
        "approved\r",
        "approved\x0b",
        "app roved",
        "approved.",
        "approve",
        "yes",
        "ok",
        "x",
        "approved\x00",
    ],
)
def test_a_look_alike_an_invisible_character_or_an_extra_word_is_never_approved(  # [NEG]
    value: str,
) -> None:
    assert classify_review(value) == "other"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("rejected", "rejected"),
        (" REJECTED\t", "rejected"),
        ("unreviewed", "unreviewed"),
        ("Unreviewed ", "unreviewed"),
        ("rejected" + NBSP, "other"),
    ],
)
def test_rejected_and_unreviewed_are_their_own_marks(value: str, expected: str) -> None:
    assert classify_review(value) == expected


@pytest.mark.parametrize("value", [None, "", "   ", "\t", " \t "])
def test_a_blank_cell_says_nothing_was_decided(value: str | None) -> None:
    assert classify_review(value) == "unreviewed"


@pytest.mark.parametrize(
    "value", [1, 0, 1.5, True, False, datetime.date(2026, 10, 10), b"approved"]
)
def test_a_cell_that_is_not_text_is_other_never_approved(value: object) -> None:  # [NEG]
    assert classify_review(value) == "other"


def test_a_value_longer_than_the_limit_is_cut_and_flagged() -> None:
    short, cut = shorten_other("x" * MAX_OTHER_CHARS)
    assert (short, cut) == ("x" * MAX_OTHER_CHARS, False)
    long_text = "y" * (MAX_OTHER_CHARS + 30)
    short, cut = shorten_other(long_text)
    assert short == "y" * MAX_OTHER_CHARS and cut is True


def test_a_value_that_is_not_text_is_shown_as_its_text_form() -> None:
    assert shorten_other(42) == ("42", False)
