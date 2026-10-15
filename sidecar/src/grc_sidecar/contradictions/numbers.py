"""Numbers, units and frequencies, by fixed tables (ADR-019 decisions 5 and 6).

Everything here is deterministic code: the model never finds a claim. The tables are small on
purpose, and a number or unit outside them is simply not read (F33-4b: nothing shows what was
missed, so the tables are fixed, tested one entry at a time, and stated as a limit).

**Durations.** Minutes, hours, days and weeks become `days` (minutes / 1,440, hours / 24,
weeks * 7). Months and years become `months` (years * 12). A month is not a fixed number of days,
so the two kinds are never converted into each other; a days-versus-months pair is only ever a
candidate.
Business days are their own kind and are never converted to calendar days (owner decision N6b).

**Frequencies** ("once a year", "twice a year", "three times a month") are a separate kind. They
become a count per year, and are compared only with other frequencies.

**Number words** run from one through twenty. A spelled-out number above twenty is not read.
"""

import re
from fractions import Fraction
from typing import Final

from grc_sidecar.contradictions.models import Kind, Number

NUMBER_WORDS: Final[dict[str, int]] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

# unit word -> (kind, factor): the value in `kind` is the written number times the factor.
# Singular and plural are both listed so that no stemming is needed.
UNITS: Final[dict[str, tuple[Kind, Fraction]]] = {
    "minute": ("days", Fraction(1, 1440)),
    "minutes": ("days", Fraction(1, 1440)),
    "hour": ("days", Fraction(1, 24)),
    "hours": ("days", Fraction(1, 24)),
    "day": ("days", Fraction(1)),
    "days": ("days", Fraction(1)),
    "week": ("days", Fraction(7)),
    "weeks": ("days", Fraction(7)),
    "month": ("months", Fraction(1)),
    "months": ("months", Fraction(1)),
    "year": ("months", Fraction(12)),
    "years": ("months", Fraction(12)),
    "character": ("characters", Fraction(1)),
    "characters": ("characters", Fraction(1)),
}

# How many times per year one period is, for frequencies.
PER_YEAR: Final[dict[str, int]] = {"day": 365, "week": 52, "month": 12, "quarter": 4, "year": 1}

_SEPARATOR: Final = r"(?:\s+|-)"
_WORDS: Final = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_NUM: Final = rf"(?<![\w.-])(?P<num>\d+(?:\.\d+)?|{_WORDS})"
_UNIT_WORDS: Final = "|".join(sorted(UNITS, key=len, reverse=True))
_PERIODS: Final = "|".join(PER_YEAR)

# Order is priority: when two readings overlap, the earlier pattern wins (a frequency beats the
# duration inside it, a business day beats a day).
_FREQUENCY: Final = re.compile(
    rf"(?<![\w.])(?P<count>once|twice|(?:\d+|{_WORDS})\s+times?)\s+(?:a|an|per|each|every)\s+"
    rf"(?P<per>{_PERIODS})\b",
    re.IGNORECASE,
)
_BUSINESS_DAYS: Final = re.compile(rf"{_NUM}{_SEPARATOR}business\s+days?\b", re.IGNORECASE)
_PERCENT: Final = re.compile(rf"{_NUM}\s*(?:%|percent\b)", re.IGNORECASE)
_UNIT: Final = re.compile(rf"{_NUM}{_SEPARATOR}(?P<unit>{_UNIT_WORDS})\b", re.IGNORECASE)
_ATTEMPTS: Final = re.compile(
    rf"{_NUM}\s+(?:[A-Za-z][A-Za-z-]*\s+){{0,2}}attempts?\b",
    re.IGNORECASE,
)


def _value_of(text: str) -> Fraction:
    lowered = text.casefold()
    if lowered in NUMBER_WORDS:
        return Fraction(NUMBER_WORDS[lowered])
    return Fraction(text)


def _frequency(match: re.Match[str]) -> Number:
    count = match["count"].casefold()
    if count == "once":
        times = 1
    elif count == "twice":
        times = 2
    else:
        times = int(_value_of(count.split()[0]))
    value = Fraction(times * PER_YEAR[match["per"].casefold()])
    return Number(text=" ".join(match[0].split()), kind="frequency", value=value)


def _simple(match: re.Match[str], kind: Kind, factor: Fraction) -> Number:
    return Number(
        text=" ".join(match[0].split()), kind=kind, value=_value_of(match["num"]) * factor
    )


def _readings(text: str) -> list[tuple[int, int, int, Number]]:
    """Every reading as (priority, start, end, number), before overlaps are resolved."""
    found: list[tuple[int, int, int, Number]] = []
    for match in _FREQUENCY.finditer(text):
        found.append((0, match.start(), match.end(), _frequency(match)))
    for match in _BUSINESS_DAYS.finditer(text):
        found.append((1, match.start(), match.end(), _simple(match, "business_days", Fraction(1))))
    for match in _PERCENT.finditer(text):
        found.append((2, match.start(), match.end(), _simple(match, "percent", Fraction(1))))
    for match in _UNIT.finditer(text):
        kind, factor = UNITS[match["unit"].casefold()]
        found.append((3, match.start(), match.end(), _simple(match, kind, factor)))
    for match in _ATTEMPTS.finditer(text):
        found.append((4, match.start(), match.end(), _simple(match, "attempts", Fraction(1))))
    return found


def find_numbers(text: str) -> list[tuple[Number, int, int]]:
    """The numbers in `text` as (number, start, end), in text order. Overlapping readings of the
    same words are resolved by priority, so a phrase is read once."""
    accepted: list[tuple[int, int, Number]] = []
    for _priority, start, end, number in sorted(_readings(text), key=lambda r: (r[0], r[1])):
        if all(end <= a_start or start >= a_end for a_start, a_end, _ in accepted):
            accepted.append((start, end, number))
    return [(number, start, end) for start, end, number in sorted(accepted, key=lambda a: a[0])]
