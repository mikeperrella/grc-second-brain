"""Claim extraction: deterministic code, no model (ADR-019 decision 4).

A claim is a number with a unit and a subject, found in one sentence or in one Markdown table row.

**Table rows.** The demo's 90-day fact is the row `| Security logs | 90 days |`: the first cell is
the subject and the other cells carry the values, and the quote is the whole row. This is a hard
test.

**Sentences.** A paragraph or list item is cut into sentences. The subject of a sentence is the part
of the clause that holds the number which comes before its first verb word (`are`, `kept`, ...),
or, if that is empty, the next clause's. This is a heuristic and is tuned on the dev split only; a
wrong subject shows up as a weaker overlap, which can only make a finding a candidate.

**Headings** are read like any line. A claim in a heading is dropped later, by verification, with
the reason `quote_is_heading`, so the count shows it.
"""

import bisect
import re
from dataclasses import dataclass
from typing import Final

from grc_sidecar.contradictions.models import ChunkText, Claim
from grc_sidecar.contradictions.numbers import NUMBER_WORDS, UNITS, find_numbers

_HEADING: Final = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.*\S)\s*$")
_LIST_MARKER: Final = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_SEPARATOR: Final = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_FENCE: Final = re.compile(r"^\s*(?:```|~~~)")
_SENTENCE_GAP: Final = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_GAP: Final = re.compile(r"[,;:]")
_WORD: Final = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")
_MARKUP: Final = re.compile(r"[*_`\[\]]")
MAX_SUBJECT_CHARS: Final = 120

# Words that end the subject of a clause: the verb or modal that follows it.
PREDICATE_WORDS: Final = frozenset(
    {"is", "are", "was", "were", "be", "been", "being", "must", "shall", "will", "should", "may"}
    | {"can", "cannot", "could", "would", "has", "have", "had", "do", "does", "need", "needs"}
    | {"kept", "keep", "keeps", "retained", "retain", "retains", "stored", "store", "stores"}
    | {"deleted", "delete", "deletes", "overwritten", "overwrite", "overwrites", "backed"}
    | {"rotated", "rotate", "expire", "expires", "expired", "locked", "lock", "locks"}
    | {"reviewed", "performed", "completed", "tested", "conducted", "held", "required"}
    | {"require", "requires", "maintained", "maintain", "preserved", "archived", "purged"}
    | {"removed", "remove", "disabled", "applied", "applies", "applying", "includes"}
)
# Words that say nothing about what a statement is about.
STOP_WORDS: Final = frozenset(
    {"a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "with", "by", "at", "from"}
    | {"that", "this", "these", "those", "so", "as", "all", "any", "each", "every", "our"}
    | {"their", "its", "we", "it", "not", "no", "than", "then", "after", "before", "within"}
    | {"during", "until", "per", "least", "most", "more", "less", "up", "only", "also", "still"}
    | {"which", "who", "if", "when", "while", "into", "over", "under", "such", "both", "other"}
    | {"some", "same", "percent", "attempts", "attempt", "business", "times", "time", "once"}
    | {"twice", "quarter", "but", "there", "they", "them", "you", "your", "us"}
)
_IGNORED: Final = STOP_WORDS | PREDICATE_WORDS | frozenset(NUMBER_WORDS) | frozenset(UNITS)


def stem(word: str) -> str:
    """A light singular form, so `logs` and `log` overlap. Not a real stemmer, on purpose."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def content_words(text: str) -> frozenset[str]:
    """The words of `text` that carry its subject: no stop words, verbs, numbers or units."""
    words = (w.casefold() for w in _WORD.findall(text))
    return frozenset(stem(w) for w in words if w not in _IGNORED)


def _clean(text: str) -> str:
    cleaned = " ".join(_MARKUP.sub("", text).split()).strip(" -")
    return cleaned[:MAX_SUBJECT_CHARS]


def _clauses(sentence: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for gap in _CLAUSE_GAP.finditer(sentence):
        spans.append((start, gap.start()))
        start = gap.end()
    spans.append((start, len(sentence)))
    return spans


def _subject_of_clause(clause: str) -> tuple[str, frozenset[str]]:
    """What the clause is about: its words before the first verb word, else before a number."""
    cut = len(clause)
    for word in _WORD.finditer(clause):
        if word[0].casefold() in PREDICATE_WORDS:
            cut = word.start()
            break
    else:
        first_number = re.search(r"\d", clause)
        if first_number is not None:
            cut = first_number.start()
    prefix = clause[:cut]
    return _clean(prefix), content_words(prefix)


def subject_of(sentence: str, number_start: int) -> tuple[str, frozenset[str]]:
    """The subject of the sentence for the number at `number_start`: the number's own clause, then
    the clauses after it, then the ones before it; failing all of them, every content word."""
    spans = _clauses(sentence)
    own = next(i for i, (start, end) in enumerate(spans) if start <= number_start <= end)
    for index in [own, *range(own + 1, len(spans)), *range(own - 1, -1, -1)]:
        start, end = spans[index]
        shown, words = _subject_of_clause(sentence[start:end])
        if words:
            return shown, words
    return _clean(sentence), content_words(sentence)


@dataclass(frozen=True)
class _Text:
    """Consecutive lines of one paragraph, list item or heading, with their file line numbers."""

    lines: tuple[tuple[int, str], ...]


def _units(chunk: ChunkText) -> list[_Text | tuple[int, str]]:
    """Cut a chunk into text units and table rows (a table row is (line number, raw line))."""
    units: list[_Text | tuple[int, str]] = []
    current: list[tuple[int, str]] = []

    def flush() -> None:
        if current:
            units.append(_Text(tuple(current)))
            current.clear()

    in_fence = False
    for offset, raw in enumerate(chunk.text.split("\n")):
        number = chunk.start_line + offset
        if _FENCE.match(raw):
            flush()
            in_fence = not in_fence
        elif in_fence:
            continue
        elif not raw.strip():
            flush()
        elif raw.lstrip().startswith("|"):
            flush()
            if not _TABLE_SEPARATOR.match(raw):
                units.append((number, raw))
        elif (heading := _HEADING.match(raw)) is not None:
            flush()
            units.append(_Text(((number, heading["text"]),)))
        elif (marker := _LIST_MARKER.match(raw)) is not None:
            flush()
            current.append((number, raw[marker.end() :]))
        else:
            current.append((number, raw))
    flush()
    return units


def _table_claims(chunk: ChunkText, number: int, raw: str, start_index: int) -> list[Claim]:
    cells = [cell.strip() for cell in raw.strip().strip("|").split("|")]
    shown, words = _clean(cells[0]), content_words(cells[0])
    quote = " ".join(raw.split())
    claims: list[Claim] = []
    for cell in cells[1:]:
        for found, _start, _end in find_numbers(cell):
            claims.append(
                Claim(
                    claim_id=f"{chunk.chunk_id}:{start_index + len(claims)}",
                    chunk_id=chunk.chunk_id,
                    source_path=chunk.source_path,
                    start_line=number,
                    end_line=number,
                    quote=quote,
                    subject=shown,
                    subject_words=words,
                    number=found,
                )
            )
    return claims


def _sentence_claims(chunk: ChunkText, unit: _Text, start_index: int) -> list[Claim]:
    text = "\n".join(line for _, line in unit.lines)
    offsets = [0]
    for _, line in unit.lines[:-1]:
        offsets.append(offsets[-1] + len(line) + 1)

    def line_of(position: int) -> int:
        return unit.lines[bisect.bisect_right(offsets, position) - 1][0]

    claims: list[Claim] = []
    start = 0
    bounds = [(gap.start(), gap.end()) for gap in _SENTENCE_GAP.finditer(text)]
    for end, next_start in [*bounds, (len(text), len(text))]:
        sentence = text[start:end]
        lead = len(sentence) - len(sentence.lstrip())
        stripped = sentence.strip()
        if stripped:
            first, last = line_of(start + lead), line_of(end - 1)
            quote = " ".join(stripped.split())
            for found, number_start, _ in find_numbers(stripped):
                shown, words = subject_of(stripped, number_start)
                claims.append(
                    Claim(
                        claim_id=f"{chunk.chunk_id}:{start_index + len(claims)}",
                        chunk_id=chunk.chunk_id,
                        source_path=chunk.source_path,
                        start_line=first,
                        end_line=last,
                        quote=quote,
                        subject=shown,
                        subject_words=words,
                        number=found,
                    )
                )
        start = next_start
    return claims


def extract_claims(chunk: ChunkText) -> list[Claim]:
    """Every claim in one chunk, in text order. Pure: the chunk text in, claims out."""
    claims: list[Claim] = []
    for unit in _units(chunk):
        if isinstance(unit, _Text):
            claims.extend(_sentence_claims(chunk, unit, len(claims)))
        else:
            claims.extend(_table_claims(chunk, unit[0], unit[1], len(claims)))
    return claims
