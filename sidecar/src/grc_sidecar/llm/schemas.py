"""The model's answer: the JSON schema sent in `format`, and the strict parse of the reply.

CLAUDE.md 8.3 and 8.4, ADR-013. The model never names a path: it cites an opaque id (`S1`, `S2`,
...) that this request gave it, and code maps the id back to a source. So the schema is built per
request, with that request's ids as the only allowed values.

The JSON schema is written out by hand instead of generated from the Pydantic model: a generated
schema carries `$defs`, `title` and `anyOf` forms that a constrained decoder may not accept, and
the owner must be able to read exactly what is sent. A test keeps the two in step.

Parsing here is only structural. What the answer MEANS (is the quote real, is the source company
policy, is the review current) is decided by the verifier from facts, never from this object, and
this object can only lower a status (ADR-013, decision 1).
"""

import re
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from grc_sidecar.llm.limits import ANSWER_MAX_CHARS, QUOTE_MAX_CHARS

MAX_CONFLICTING_SOURCES = 5
SOURCE_ID_PATTERN = re.compile(r"^S[1-9][0-9]?$")
_DOC_URL_MARKER = "errors.pydantic.dev"

Coverage = Literal["full", "partial", "none"]


class ModelAnswer(BaseModel):
    """One reply from the model, after the strict parse. Every field is required."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: Annotated[str, Field(max_length=ANSWER_MAX_CHARS)]
    source_id: str | None
    exact_quote: Annotated[str, Field(max_length=QUOTE_MAX_CHARS)]
    model_confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    coverage: Coverage
    conflicting_source_ids: Annotated[list[str], Field(max_length=MAX_CONFLICTING_SOURCES)]
    no_evidence: bool

    @field_validator("source_id")
    @classmethod
    def _source_must_be_in_this_request(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and value not in _allowed_ids(info):
            raise ValueError("source_id is not one of this request's source ids")
        return value

    @field_validator("conflicting_source_ids")
    @classmethod
    def _conflicts_must_be_in_this_request(
        cls, value: list[str], info: ValidationInfo
    ) -> list[str]:
        allowed = _allowed_ids(info)
        if any(item not in allowed for item in value):
            raise ValueError("conflicting_source_ids holds an id that is not in this request")
        return value


def _allowed_ids(info: ValidationInfo) -> frozenset[str]:
    """The ids of this request. Parsing without them is a programming error, not a model error."""
    context = info.context
    if not isinstance(context, dict) or not isinstance(context.get("source_ids"), frozenset):
        raise TypeError("parse_model_answer needs the request's source ids")
    ids: frozenset[str] = context["source_ids"]
    return ids


def check_source_ids(source_ids: Sequence[str]) -> tuple[str, ...]:
    """The ids of a request: S1, S2, ... in order, no gaps, no repeats. Else a ValueError."""
    ids = tuple(source_ids)
    if not ids:
        raise ValueError("a request needs at least one source")
    expected = tuple(f"S{number}" for number in range(1, len(ids) + 1))
    if ids != expected or not all(SOURCE_ID_PATTERN.match(i) for i in ids):
        raise ValueError("source ids must be S1, S2, ... in order")
    return ids


def output_schema(source_ids: Sequence[str]) -> dict[str, object]:
    """The JSON schema for `format`: this request's ids are the only values a source may take."""
    ids = list(check_source_ids(source_ids))
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "answer",
            "source_id",
            "exact_quote",
            "model_confidence",
            "coverage",
            "conflicting_source_ids",
            "no_evidence",
        ],
        "properties": {
            "answer": {"type": "string", "maxLength": ANSWER_MAX_CHARS},
            "source_id": {"enum": [*ids, None]},
            "exact_quote": {"type": "string", "maxLength": QUOTE_MAX_CHARS},
            "model_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "coverage": {"enum": ["full", "partial", "none"]},
            "conflicting_source_ids": {
                "type": "array",
                "maxItems": MAX_CONFLICTING_SOURCES,
                "items": {"enum": ids},
            },
            "no_evidence": {"type": "boolean"},
        },
    }


def parse_model_answer(content: str, source_ids: Sequence[str]) -> ModelAnswer:
    """Strictly parse the model's reply. Raises pydantic's ValidationError on any deviation.

    Strict: JSON types must match exactly (a string is not a number or a boolean), unknown keys
    are refused, and an id that is not in `source_ids` is refused.
    """
    ids = frozenset(check_source_ids(source_ids))
    return ModelAnswer.model_validate_json(content, strict=True, context={"source_ids": ids})


def render_validation_error(error: ValidationError) -> str:
    """The exact validation error text, minus the pydantic documentation URL lines.

    This is what the model sees on attempt 2 (CLAUDE.md 8.3). The text can quote the model's own
    earlier output (pydantic shows the offending value), which is why it travels inside a data
    block and is never an instruction.
    """
    lines = [line for line in str(error).splitlines() if _DOC_URL_MARKER not in line]
    return "\n".join(lines).strip()
