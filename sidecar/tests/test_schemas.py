"""The model's answer: the schema sent in `format` and the strict parse of the reply.

Parsing is only structural. Whether the quote is real, current company text is the verifier's job
(M5e), and nothing here can raise a status.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from grc_sidecar.llm import limits
from grc_sidecar.llm.schemas import (
    MAX_CONFLICTING_SOURCES,
    ModelAnswer,
    check_source_ids,
    output_schema,
    parse_model_answer,
    render_validation_error,
)

IDS = ["S1", "S2", "S3"]


def good(**overrides: object) -> dict[str, object]:
    reply: dict[str, object] = {
        "answer": "Screens lock after 10 minutes.",
        "source_id": "S2",
        "exact_quote": "lock their screen after 10 minutes of inactivity",
        "model_confidence": 0.92,
        "coverage": "full",
        "conflicting_source_ids": [],
        "no_evidence": False,
    }
    reply.update(overrides)
    return reply


def parse(reply: object, ids: list[str] = IDS) -> ModelAnswer:
    return parse_model_answer(json.dumps(reply), ids)


# ----- the schema -----------------------------------------------------------------------------


def test_the_schema_is_exactly_this() -> None:
    assert output_schema(["S1", "S2"]) == {
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
            "answer": {"type": "string", "maxLength": 1200},
            "source_id": {"enum": ["S1", "S2", None]},
            "exact_quote": {"type": "string", "maxLength": 400},
            "model_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "coverage": {"enum": ["full", "partial", "none"]},
            "conflicting_source_ids": {
                "type": "array",
                "maxItems": 5,
                "items": {"enum": ["S1", "S2"]},
            },
            "no_evidence": {"type": "boolean"},
        },
    }


def test_the_schema_is_plain_json_with_no_references() -> None:
    text = json.dumps(output_schema(IDS))
    for banned in ("$defs", "$ref", "anyOf", "title"):
        assert banned not in text


def test_the_schema_and_the_model_have_the_same_fields() -> None:
    """The hand-written schema and the Pydantic model must not drift apart."""
    schema = output_schema(IDS)
    properties: dict[str, Any] = schema["properties"]  # type: ignore[assignment]
    assert set(properties) == set(ModelAnswer.model_fields) == set(schema["required"])  # type: ignore[arg-type]
    assert properties["answer"]["maxLength"] == limits.ANSWER_MAX_CHARS
    assert properties["exact_quote"]["maxLength"] == limits.QUOTE_MAX_CHARS
    assert properties["conflicting_source_ids"]["maxItems"] == MAX_CONFLICTING_SOURCES


def test_the_output_caps_add_up_to_the_num_predict_the_client_sends() -> None:
    assert limits.ANSWER_MAX_CHARS + limits.QUOTE_MAX_CHARS + limits.OTHER_OUTPUT_CHARS == (
        limits.NUM_PREDICT
    )


def test_the_schema_offers_only_this_requests_ids() -> None:
    one = output_schema(["S1"])
    assert one["properties"]["source_id"] == {"enum": ["S1", None]}  # type: ignore[index]
    assert one["properties"]["conflicting_source_ids"]["items"] == {"enum": ["S1"]}  # type: ignore[index]


@pytest.mark.parametrize(
    "ids",
    [[], ["S2"], ["S1", "S3"], ["S1", "S1"], ["S2", "S1"], ["s1"], ["S01"], ["S0"], ["X1"]],
)
def test_source_ids_must_be_s1_to_sn_in_order(ids: list[str]) -> None:  # [NEG]
    with pytest.raises(ValueError, match="source"):
        check_source_ids(ids)
    with pytest.raises(ValueError, match="source"):
        output_schema(ids)
    with pytest.raises(ValueError, match="source"):
        parse_model_answer("{}", ids)


def test_up_to_five_ids_are_accepted() -> None:
    ids = [f"S{n}" for n in range(1, 6)]
    assert check_source_ids(ids) == tuple(ids)


# ----- the strict parse: what is accepted ------------------------------------------------------


def test_a_good_reply_parses() -> None:
    answer = parse(good())
    assert (answer.source_id, answer.coverage, answer.model_confidence) == ("S2", "full", 0.92)
    assert answer.conflicting_source_ids == [] and answer.no_evidence is False


def test_a_no_evidence_reply_parses_with_a_null_source() -> None:
    answer = parse(
        good(
            answer="",
            source_id=None,
            exact_quote="",
            model_confidence=0.0,
            coverage="none",
            no_evidence=True,
        )
    )
    assert answer.source_id is None and answer.no_evidence is True


def test_an_integer_confidence_is_accepted_as_a_number() -> None:
    assert parse(good(model_confidence=1)).model_confidence == 1.0
    assert parse(good(model_confidence=0)).model_confidence == 0.0


def test_a_reply_that_contradicts_itself_still_parses_because_status_is_not_decided_here() -> None:
    """`no_evidence: true` with a quote is nonsense, but ruling on it is the verifier's job
    (ADR-013, schema precedence: any inconsistent combination is treated as no evidence)."""
    assert parse(good(no_evidence=True)).no_evidence is True


def test_a_conflict_may_name_any_other_source_of_this_request() -> None:
    assert parse(good(conflicting_source_ids=["S1", "S3"])).conflicting_source_ids == ["S1", "S3"]


# ----- the strict parse: what is refused --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "not json", "[]", "null", '"text"', "3", '{"answer": ', "{} trailing"],
)
def test_text_that_is_not_one_json_object_is_refused(text: str) -> None:  # [NEG]
    with pytest.raises(ValidationError):
        parse_model_answer(text, IDS)


@pytest.mark.parametrize("missing", sorted(good()))
def test_every_field_is_required(missing: str) -> None:  # [NEG]
    reply = good()
    del reply[missing]
    with pytest.raises(ValidationError):
        parse(reply)


def test_an_unknown_field_is_refused() -> None:  # [NEG] extra keys could smuggle a status in
    with pytest.raises(ValidationError):
        parse(good(status="SUPPORTED"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_confidence": "0.9"},  # a string is not a number (strict)
        {"no_evidence": "true"},  # a string is not a boolean (strict)
        {"no_evidence": 1},
        {"answer": 5},
        {"exact_quote": None},
        {"coverage": "mostly"},
        {"coverage": "FULL"},
        {"model_confidence": 1.5},
        {"model_confidence": -0.1},
        {"model_confidence": None},
        {"source_id": 1},
        {"conflicting_source_ids": "S1"},
        {"conflicting_source_ids": [1]},
    ],
)
def test_a_wrong_type_or_value_is_refused(overrides: dict[str, object]) -> None:  # [NEG]
    with pytest.raises(ValidationError):
        parse(good(**overrides))


def test_a_nan_confidence_is_refused() -> None:  # [NEG]
    text = json.dumps(good()).replace("0.92", "NaN")
    assert "NaN" in text
    with pytest.raises(ValidationError):
        parse_model_answer(text, IDS)


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_id": "S9"},  # an id this request never offered
        {"source_id": "s2"},
        {"source_id": "S2 "},
        {"source_id": ""},
        {"source_id": "vault/secret.md"},  # a path is never an id
        {"conflicting_source_ids": ["S9"]},
        {"conflicting_source_ids": ["S1", "nope"]},
    ],
)
def test_an_id_that_is_not_in_this_request_is_refused(
    overrides: dict[str, object],
) -> None:  # [NEG]
    with pytest.raises(ValidationError):
        parse(good(**overrides))


def test_the_ids_of_a_request_decide_what_is_valid() -> None:  # [NEG]
    assert parse(good(source_id="S3"), ["S1", "S2", "S3"]).source_id == "S3"
    with pytest.raises(ValidationError):
        parse(good(source_id="S3"), ["S1", "S2"])


def test_too_many_conflicts_are_refused() -> None:  # [NEG]
    ids = [f"S{n}" for n in range(1, 8)]
    with pytest.raises(ValidationError):
        parse(good(conflicting_source_ids=ids[:6]), ids)
    assert parse(good(conflicting_source_ids=ids[:5], source_id="S6"), ids)


def test_the_length_caps_are_enforced_at_the_limit() -> None:  # [NEG]
    assert parse(good(answer="a" * limits.ANSWER_MAX_CHARS))
    with pytest.raises(ValidationError):
        parse(good(answer="a" * (limits.ANSWER_MAX_CHARS + 1)))
    assert parse(good(exact_quote="q" * limits.QUOTE_MAX_CHARS))
    with pytest.raises(ValidationError):
        parse(good(exact_quote="q" * (limits.QUOTE_MAX_CHARS + 1)))


def test_parsing_without_the_request_ids_is_a_programming_error_not_a_pass() -> None:  # [NEG]
    with pytest.raises(TypeError):
        ModelAnswer.model_validate_json(json.dumps(good()), strict=True)


def test_the_parsed_answer_cannot_be_changed() -> None:  # [NEG] it is frozen
    answer = parse(good())
    with pytest.raises(ValidationError):
        answer.model_confidence = 1.0  # type: ignore[misc]


# ----- the error text shown on attempt 2 ----------------------------------------------------------


def test_the_error_text_is_pydantics_own_minus_the_documentation_url() -> None:
    with pytest.raises(ValidationError) as caught:
        parse(good(model_confidence=2))
    full = str(caught.value)
    assert "errors.pydantic.dev" in full  # pydantic does add it...
    rendered = render_validation_error(caught.value)
    assert "errors.pydantic.dev" not in rendered  # ...and we never show it
    kept = [line for line in full.splitlines() if "errors.pydantic.dev" not in line]
    assert rendered == "\n".join(kept).strip()
    assert "model_confidence" in rendered and "validation error" in rendered


def test_the_error_text_names_the_field_and_the_problem_without_guessing() -> None:
    with pytest.raises(ValidationError) as caught:
        parse(good(coverage="mostly", source_id="S9"))
    rendered = render_validation_error(caught.value)
    assert "coverage" in rendered and "source_id" in rendered
