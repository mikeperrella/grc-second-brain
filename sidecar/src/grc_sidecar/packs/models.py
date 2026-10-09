"""Metadata for a bundled framework data pack (ADR-008).

A pack is a directory of Markdown plus one `pack.json`. Every field below is required, with no
defaults, and unknown fields are rejected, so a half-filled or hand-edited file fails loudly
instead of being treated as "probably fine". The rules tying fields together encode the
licensing policy: licensed text is never bundled, only IDs and our own summaries.
"""

import json
import re
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from grc_sidecar.errors import PackMetadataError
from grc_sidecar.vault.models import IsoDate, NonEmptyStr

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")

Origin = Literal["converted_public_domain", "original_authored"]
ContentKind = Literal["full_text", "ids_and_original_summaries"]


class PackMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pack_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]*$", max_length=64)]
    title: NonEmptyStr
    version: NonEmptyStr  # the upstream version, for example "2.0" or "rev5"
    license: NonEmptyStr  # the licence of what we bundle
    upstream_license: NonEmptyStr  # the licence of the original work
    source_url: Annotated[str, StringConstraints(pattern=r"^https://\S+$")]
    retrieved_on: IsoDate
    source_sha256: str  # sha256 of the downloaded source file (or of the authored file set)
    content_sha256: str  # sha256 over the sorted (relative path, file sha256) of the .md files
    origin: Origin
    content_kind: ContentKind
    redistributable: bool
    converter_version: NonEmptyStr
    # What the ID list was actually checked against, in words. "unverified" is allowed and must
    # stay visible until a real check happens. Never name a source that was not actually seen.
    id_list_verified_against: NonEmptyStr

    @field_validator("source_sha256", "content_sha256")
    @classmethod
    def _is_sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("must be 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def _licensing_rules(self) -> Self:
        # Full text may only ship if we are allowed to redistribute it.
        if not self.redistributable and self.content_kind != "ids_and_original_summaries":
            raise ValueError("non-redistributable packs may only hold IDs and original summaries")
        if self.origin == "converted_public_domain" and self.content_kind != "full_text":
            raise ValueError("a converted public-domain pack holds full text")
        if self.origin == "original_authored" and self.content_kind != "ids_and_original_summaries":
            raise ValueError("an authored pack holds IDs and original summaries only")
        return self


def parse_pack_metadata(text: str) -> PackMetadata:
    """Parse pack.json text. Raises PackMetadataError naming the bad fields (never their values)."""
    fields: tuple[str, ...] | None = None
    metadata: PackMetadata | None = None
    try:
        metadata = PackMetadata.model_validate(json.loads(text))
    except json.JSONDecodeError:
        fields = ("pack.json",)
    except ValidationError as exc:
        names = {str(item["loc"][0]) if item["loc"] else "pack.json" for item in exc.errors()}
        fields = tuple(sorted(names))
    if metadata is None:
        raise PackMetadataError(fields or ("pack.json",))
    return metadata
