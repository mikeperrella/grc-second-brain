"""Units, number words and frequencies, one table entry at a time (ADR-019 decisions 5, 6; F33-4a).

The expected values are written out here, not read from the module's tables, so a wrong entry in
the tables is a failing test.
"""

from fractions import Fraction

import pytest

from grc_sidecar.contradictions.numbers import find_numbers


def one(text: str) -> tuple[str, Fraction, str]:
    """The single number in `text` as (kind, value, text)."""
    found = find_numbers(text)
    assert len(found) == 1, found
    number = found[0][0]
    return number.kind, number.value, number.text


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("3 minutes", "days", Fraction(3, 1440)),
        ("1 minute", "days", Fraction(1, 1440)),
        ("48 hours", "days", Fraction(2)),
        ("1 hour", "days", Fraction(1, 24)),
        ("90 days", "days", Fraction(90)),
        ("1 day", "days", Fraction(1)),
        ("2 weeks", "days", Fraction(14)),
        ("1 week", "days", Fraction(7)),
        ("6 months", "months", Fraction(6)),
        ("1 month", "months", Fraction(1)),
        ("7 years", "months", Fraction(84)),
        ("1 year", "months", Fraction(12)),
        ("12 characters", "characters", Fraction(12)),
        ("1 character", "characters", Fraction(1)),
        ("5 attempts", "attempts", Fraction(5)),
        ("1 attempt", "attempts", Fraction(1)),
        ("95%", "percent", Fraction(95)),
        ("95 percent", "percent", Fraction(95)),
        ("99.9%", "percent", Fraction(999, 10)),
        ("1.5 hours", "days", Fraction(1, 16)),
        ("ten business days", "business_days", Fraction(10)),
        ("10 business days", "business_days", Fraction(10)),
        ("1 business day", "business_days", Fraction(1)),
        ("a 90-day period", "days", Fraction(90)),
    ],
)
def test_each_unit_converts_to_its_kind(text: str, kind: str, value: Fraction) -> None:
    assert one(text)[:2] == (kind, value)


@pytest.mark.parametrize(
    ("word", "value"),
    [
        ("one", 1),
        ("two", 2),
        ("three", 3),
        ("four", 4),
        ("five", 5),
        ("six", 6),
        ("seven", 7),
        ("eight", 8),
        ("nine", 9),
        ("ten", 10),
        ("eleven", 11),
        ("twelve", 12),
        ("thirteen", 13),
        ("fourteen", 14),
        ("fifteen", 15),
        ("sixteen", 16),
        ("seventeen", 17),
        ("eighteen", 18),
        ("nineteen", 19),
        ("twenty", 20),
    ],
)
def test_each_number_word_from_one_to_twenty(word: str, value: int) -> None:
    assert one(f"{word} days")[:2] == ("days", Fraction(value))
    assert one(f"{word.upper()} days")[1] == Fraction(value)


@pytest.mark.parametrize("text", ["thirty days", "twenty-one days", "a hundred days", "some days"])
def test_a_number_above_twenty_in_words_is_not_read(text: str) -> None:
    # The limit F33-4b states: the table stops at twenty, and nothing is invented above it.
    assert find_numbers(text) == []


@pytest.mark.parametrize(
    ("text", "per_year"),
    [
        ("once a year", 1),
        ("twice a year", 2),
        ("once per year", 1),
        ("once every year", 1),
        ("three times a month", 36),
        ("ten times a year", 10),
        ("twice a week", 104),
        ("once a day", 365),
        ("once each quarter", 4),
        ("4 times per year", 4),
    ],
)
def test_each_frequency_becomes_a_count_per_year(text: str, per_year: int) -> None:
    kind, value, written = one(text)
    assert (kind, value) == ("frequency", Fraction(per_year))
    assert written == text


def test_a_frequency_is_not_also_read_as_a_duration() -> None:
    # "twice a year" must not yield a "year" duration as well: one reading per phrase.
    assert [n.kind for n, _, _ in find_numbers("Reviews run twice a year.")] == ["frequency"]


def test_once_without_a_period_is_not_a_frequency() -> None:
    assert find_numbers("Once logged in, the user waits.") == []


def test_business_days_are_not_calendar_days() -> None:
    kind = one("within ten business days")[0]
    assert kind == "business_days"
    assert kind != one("within ten days")[0]


def test_days_and_months_stay_different_kinds() -> None:
    # A month is not a fixed number of days, so 3 months is never turned into 90 days.
    assert one("3 months")[0] == "months"
    assert one("90 days")[0] == "days"
    assert one("1 year")[1] == Fraction(12)


def test_attempts_may_have_words_between_the_number_and_the_noun() -> None:
    assert one("locked after three failed sign-in attempts")[:2] == ("attempts", Fraction(3))
    assert one("after 5 consecutive failed attempts")[:2] == ("attempts", Fraction(5))


def test_a_number_inside_a_word_or_a_version_is_not_read() -> None:
    assert find_numbers("TLS1.2 days") == []
    assert find_numbers("x90 days") == []


def test_several_numbers_are_returned_in_text_order() -> None:
    found = find_numbers("Logs are kept 90 days and tickets 6 months, reviewed twice a year.")
    assert [n.text for n, _, _ in found] == ["90 days", "6 months", "twice a year"]
