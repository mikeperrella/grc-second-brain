import hashlib
import json
from typing import Any

import pytest

from grc_sidecar.errors import PackMetadataError
from grc_sidecar.packs.models import PackMetadata, parse_pack_metadata

# Hashes are derived, not typed in: gitleaks flags long hex literals that look like secrets.
SOURCE_SHA = hashlib.sha256(b"source file").hexdigest()
CONTENT_SHA = hashlib.sha256(b"converted content").hexdigest()


def valid() -> dict[str, Any]:
    return {
        "pack_id": "nist-csf-2.0",
        "title": "NIST Cybersecurity Framework 2.0",
        "version": "2.0",
        "license": "US-Gov-PD (17 USC 105)",
        "upstream_license": "US Government work, public domain in the US",
        "source_url": "https://example.invalid/NIST_CSF_v2.0_catalog.json",
        "retrieved_on": "2026-10-09",
        "source_sha256": SOURCE_SHA,
        "content_sha256": CONTENT_SHA,
        "origin": "converted_public_domain",
        "content_kind": "full_text",
        "redistributable": True,
        "converter_version": "1",
        "id_list_verified_against": "source file hash only",
    }


def authored() -> dict[str, Any]:
    pack = valid()
    pack.update(
        pack_id="soc2-tsc-ids",
        origin="original_authored",
        content_kind="ids_and_original_summaries",
        upstream_license="AICPA copyright; text not bundled",
        id_list_verified_against="unverified",
    )
    return pack


def _error_fields(data: dict[str, Any]) -> tuple[str, ...]:
    with pytest.raises(PackMetadataError) as caught:
        parse_pack_metadata(json.dumps(data))
    return caught.value.fields


def test_a_complete_public_domain_pack_parses() -> None:
    pack = parse_pack_metadata(json.dumps(valid()))
    assert pack.pack_id == "nist-csf-2.0" and pack.redistributable
    assert str(pack.retrieved_on) == "2026-10-09"


def test_an_authored_ids_and_summaries_pack_parses() -> None:
    pack = parse_pack_metadata(json.dumps(authored()))
    assert pack.content_kind == "ids_and_original_summaries"
    assert pack.id_list_verified_against == "unverified"


@pytest.mark.parametrize("field", sorted(valid()))
def test_every_field_is_required(field: str) -> None:  # [NEG]
    data = valid()
    del data[field]
    assert _error_fields(data) == (field,)


def test_unknown_fields_are_rejected() -> None:  # [NEG]
    data = valid()
    data["surprise"] = "x"
    assert _error_fields(data) == ("surprise",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sha256", "ABC"),
        ("source_sha256", SOURCE_SHA.upper()),
        ("content_sha256", SOURCE_SHA[:-1]),
        ("pack_id", "Has Space"),
        ("pack_id", "../escape"),
        ("source_url", "http://example.invalid/plain-http"),
        ("source_url", "file:///c:/x"),
        ("retrieved_on", "2026-10-9"),
        ("retrieved_on", "09/10/2026"),
        ("retrieved_on", "2026-02-30"),
        ("title", "   "),
        ("id_list_verified_against", ""),
        ("origin", "scraped"),
        ("redistributable", "maybe"),
    ],
)
def test_invalid_values_are_rejected_naming_the_field(field: str, value: object) -> None:  # [NEG]
    data = valid()
    data[field] = value
    assert _error_fields(data) == (field,)


def test_licensed_text_can_never_be_marked_as_bundled_full_text() -> None:  # [NEG]
    data = authored()
    data["redistributable"] = False
    data["content_kind"] = "full_text"
    assert _error_fields(data) == ("pack.json",)


def test_a_non_redistributable_pack_may_still_hold_ids_and_original_summaries() -> None:
    data = authored()
    data["redistributable"] = False
    assert parse_pack_metadata(json.dumps(data)).redistributable is False


@pytest.mark.parametrize(
    ("origin", "kind"),
    [
        ("converted_public_domain", "ids_and_original_summaries"),
        ("original_authored", "full_text"),
    ],
)
def test_origin_and_content_kind_must_agree(origin: str, kind: str) -> None:  # [NEG]
    data = valid()
    data.update(origin=origin, content_kind=kind)
    assert _error_fields(data) == ("pack.json",)


def test_broken_json_and_non_objects_are_reported() -> None:  # [NEG]
    for text in ["{not json", "[]", "null", '"text"', ""]:
        with pytest.raises(PackMetadataError) as caught:
            parse_pack_metadata(text)
        assert caught.value.fields == ("pack.json",)


def test_errors_name_fields_but_never_echo_values() -> None:  # [NEG]
    secret = "marker-" + "value"
    data = valid()
    data["source_sha256"] = secret
    with pytest.raises(PackMetadataError) as caught:
        parse_pack_metadata(json.dumps(data))
    error = caught.value
    assert error.fields == ("source_sha256",)
    assert secret not in str(error) and secret not in repr(error)
    assert error.__cause__ is None and error.__context__ is None


def test_metadata_is_immutable() -> None:
    pack = PackMetadata.model_validate(valid())
    with pytest.raises(ValueError, match="frozen"):
        pack.__setattr__("version", "3.0")
