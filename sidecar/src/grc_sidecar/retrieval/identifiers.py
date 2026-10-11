"""Control identifiers and the ways they are written (ADR-010, M4d).

A questionnaire says `3.1.1`, the NIST 800-171 Rev 3 pack says `03.01.01`; one policy says `AC-2`,
another `AC-02`. The FTS5 tokenizer (`unicode61 tokenchars '-'`) keeps those apart (`3.1.1` is the
tokens `3 1 1`, `03.01.01` is `03 01 01`), so the query builder asks this module for every written
form of an identifier and searches for all of them.

This is FORMAT normalization only. It never says that two controls mean the same thing: 800-171
Rev 2 `3.1.1` and Rev 3 `03.01.01` share a written form and not a text, and the result of a search
carries the pack it came from.

The rules are a table (`IDENTIFIER_RULES`), tried in order, first match wins. Each row has a name,
a pattern matched against a whole token, and a function that returns the written forms. Adding a
framework is adding a row and a test row.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

# The 20 control families of NIST SP 800-53 Rev 5.
_FAMILIES_800_53: Final = "AC|AT|AU|CA|CM|CP|IA|IR|MA|MP|PE|PL|PM|PS|PT|RA|SA|SC|SI|SR"
_CSF_FUNCTIONS: Final = "GV|ID|PR|DE|RS|RC"


@dataclass(frozen=True)
class IdentifierRule:
    name: str
    pattern: re.Pattern[str]
    forms: Callable[[re.Match[str]], tuple[str, ...]]


@dataclass(frozen=True)
class Identifier:
    """An identifier found in a query: which rule matched and every written form to search for."""

    rule: str
    forms: tuple[str, ...]


def _distinct(*forms: str) -> tuple[str, ...]:
    """The forms in the order given, each once."""
    return tuple(dict.fromkeys(forms))


def _nist_800_171(match: re.Match[str]) -> tuple[str, ...]:
    parts = [int(part) for part in match.groups()]
    plain = ".".join(str(part) for part in parts)
    padded = ".".join(f"{part:02d}" for part in parts)
    return _distinct(plain, padded)


def _iso_27001(match: re.Match[str]) -> tuple[str, ...]:
    first, second = int(match.group(1)), int(match.group(2))
    return (f"{first}.{second}", f"A.{first}.{second}")


def _nist_800_53(match: re.Match[str]) -> tuple[str, ...]:
    family, number, enhancement = match.group(1).upper(), int(match.group(2)), match.group(3)
    suffix = f"({int(enhancement)})" if enhancement is not None else ""
    return _distinct(f"{family}-{number}{suffix}", f"{family}-{number:02d}{suffix}")


def _csf(match: re.Match[str]) -> tuple[str, ...]:
    function, category, number = match.group(1).upper(), match.group(2).upper(), match.group(3)
    base = f"{function}.{category}"
    if number is None:
        return (base,)
    return _distinct(f"{base}-{int(number):02d}", f"{base}-{int(number)}")


def _soc2(match: re.Match[str]) -> tuple[str, ...]:
    prefix, series, first, second = match.groups()
    bare = f"{series.upper()}{int(first)}.{int(second)}"
    return _distinct(bare, f"SOC2:{bare}") if prefix else (bare,)


def _as_written(match: re.Match[str]) -> tuple[str, ...]:
    return (match.group(0),)


IDENTIFIER_RULES: Final[tuple[IdentifierRule, ...]] = (
    IdentifierRule(
        "nist-800-171-r3",
        re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{1,2})"),
        _nist_800_171,
    ),
    IdentifierRule(
        "iso-27001-annex-a",
        re.compile(r"(?:A\.)?(\d{1,2})\.(\d{1,2})", re.IGNORECASE),
        _iso_27001,
    ),
    IdentifierRule(
        "nist-800-53",
        re.compile(rf"({_FAMILIES_800_53})-(\d{{1,2}})(?:\((\d{{1,2}})\))?", re.IGNORECASE),
        _nist_800_53,
    ),
    IdentifierRule(
        "nist-csf-2.0",
        re.compile(rf"({_CSF_FUNCTIONS})\.([A-Z]{{2}})(?:-(\d{{1,2}}))?", re.IGNORECASE),
        _csf,
    ),
    IdentifierRule(
        "soc2-tsc",
        re.compile(r"(SOC ?2:)?(CC|PI|A|C|P)(\d{1,2})\.(\d{1,2})", re.IGNORECASE),
        _soc2,
    ),
    # Anything else that looks like an ID: hyphenated and containing a digit (HR-POL-005, TLS-1).
    # "multi-factor" has no digit, so it stays an ordinary word.
    IdentifierRule(
        "generic-hyphenated",
        re.compile(r"(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+"),
        _as_written,
    ),
)


def identify(token: str) -> Identifier | None:
    """The identifier a whole token is, or None if it is an ordinary word."""
    for rule in IDENTIFIER_RULES:
        match = rule.pattern.fullmatch(token)
        if match is not None:
            return Identifier(rule=rule.name, forms=rule.forms(match))
    return None
