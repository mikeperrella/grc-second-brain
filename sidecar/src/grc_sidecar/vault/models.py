"""Typed models for the vault indexer (CLAUDE.md 7: Pydantic at every boundary).

Everything here is immutable data. The functions that produce these models live in the
sibling modules (frontmatter, chunker, links, scan).
"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)
from pydantic_core import PydanticCustomError

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _strict_iso_date(value: object) -> object:
    """Accept only `YYYY-MM-DD` text (or an existing date), never a looser date format.

    Pydantic's own date parsing also accepts datetimes and other shapes. The frontmatter
    contract is one exact format, so anything else is rejected with a fixed message.
    """
    if isinstance(value, str):
        if not _ISO_DATE.fullmatch(value):
            raise ValueError("date must be written as YYYY-MM-DD")
        return date.fromisoformat(value)  # also rejects impossible dates such as 2026-02-30
    if isinstance(value, date) and type(value) is date:
        return value
    raise ValueError("date must be written as YYYY-MM-DD")


IsoDate = Annotated[date, BeforeValidator(_strict_iso_date)]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

DocType = Literal["policy", "procedure", "standard", "guideline", "evidence", "framework", "note"]
# Generated framework text and free-form notes have no review cycle, so review_by is optional.
REVIEW_OPTIONAL_TYPES: frozenset[str] = frozenset({"framework", "note"})

IssueSeverity = Literal["error", "warning", "info"]
LinkOrigin = Literal["body", "frontmatter"]
LinkStatus = Literal["resolved", "unresolved", "ambiguous", "rejected"]


def _strip_wikilink_brackets(value: str) -> str:
    """`[[target]]` and `target` mean the same thing in frontmatter `links`."""
    text = value.strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2].strip()
    return text


class Frontmatter(BaseModel):
    """The YAML block at the top of a vault file (CLAUDE.md 8.1)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    doc_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    type: DocType
    owner: NonEmptyStr
    # validate_default makes the validator below run when the key is absent.
    review_by: IsoDate | None = Field(default=None, validate_default=True)
    control_mappings: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    ] = Field(default_factory=list)
    links: list[Annotated[str, AfterValidator(_strip_wikilink_brackets)]] = Field(
        default_factory=list
    )

    @field_validator("review_by", mode="after")
    @classmethod
    def _review_by_required_for_reviewed_types(
        cls, value: date | None, info: ValidationInfo
    ) -> date | None:
        doc_type = info.data.get("type")
        if value is None and doc_type is not None and doc_type not in REVIEW_OPTIONAL_TYPES:
            raise PydanticCustomError("missing_required", "review_by is required for this type")
        return value

    @field_validator("links", mode="after")
    @classmethod
    def _links_not_empty(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("links entries must not be empty")
        return value


class IndexIssue(BaseModel):
    """One problem or note found while indexing. Never contains document text or input values."""

    model_config = ConfigDict(frozen=True)

    severity: IssueSeverity
    code: str
    source_path: str | None = None
    field: str | None = None
    line: int | None = None


class ParsedDocument(BaseModel):
    """A vault file after decoding, normalization and frontmatter parsing."""

    model_config = ConfigDict(frozen=True)

    source_path: str  # vault-relative, forward slashes
    frontmatter: Frontmatter
    file_sha256: str  # of the normalized text (LF line endings, no BOM)
    lines: tuple[str, ...]  # the whole normalized file, frontmatter included
    body_start_line: int  # 1-based line number of the first line after the closing fence
    frontmatter_line_of_links: int  # 1-based line of the `links:` key, or 1
    notes: tuple[IndexIssue, ...] = ()


class Chunk(BaseModel):
    """A contiguous run of lines of one document, ready to embed and cite."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    source_path: str
    doc_id: str
    start_line: int  # 1-based, inclusive, counted in the original file (frontmatter included)
    end_line: int  # 1-based, inclusive
    heading_path: str
    text: str  # exactly the file's lines start_line..end_line joined with "\n"
    text_sha256: str
    flags: tuple[str, ...] = ()


class RawLink(BaseModel):
    """A link as written in the file, before any resolution."""

    model_config = ConfigDict(frozen=True)

    source_path: str
    source_line: int
    target: str
    alias: str | None = None
    fragment: str | None = None
    is_embed: bool = False
    origin: LinkOrigin = "body"


class ResolvedLink(BaseModel):
    """A RawLink plus what it resolved to. Nothing is invented: failures say why."""

    model_config = ConfigDict(frozen=True)

    link: RawLink
    status: LinkStatus
    target_doc_id: str | None = None
    code: str | None = None


class ScanResult(BaseModel):
    """The outcome of scanning a vault: valid documents plus every problem found."""

    model_config = ConfigDict(frozen=True)

    documents: tuple[ParsedDocument, ...]
    issues: tuple[IndexIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)
