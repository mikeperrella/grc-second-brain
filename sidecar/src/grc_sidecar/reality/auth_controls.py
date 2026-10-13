"""The authentication controls the reality check treats as "the MFA controls" (ADR-016 decision 13).

The list is a data file shipped inside the package (`auth_controls.json`). It is read with
`importlib.resources`, so no path comes from the user and nothing touches the network. The file is
untrusted until validated: strict Pydantic models, every ID must be one that
`retrieval.identifiers.identify` recognizes, and a duplicate or malformed file is a typed refusal
(`AuthControlsError`), never a partial list.

Whether each ID really exists in the shipped packs is proved by a test, not at runtime.
"""

import json
from importlib import resources
from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from grc_sidecar.errors import AuthControlsError
from grc_sidecar.retrieval.identifiers import identify

RESOURCE_PACKAGE: Final = "grc_sidecar.reality"
RESOURCE_NAME: Final = "auth_controls.json"
MAX_BYTES: Final = 64 * 1024

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class AuthControl(BaseModel):
    """One authentication control and where in the shipped packs it comes from."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: _Text
    framework: _Text
    pack: _Text  # the pack folder name under packs/
    file: _Text  # the Markdown file in that pack that holds the ID

    @field_validator("id")
    @classmethod
    def _is_an_identifier(cls, value: str) -> str:
        if identify(value) is None:
            raise ValueError("not a control identifier")
        return value


class AuthControlsFile(BaseModel):
    # Not strict: JSON has lists and the model wants a tuple. Field types stay exact (no int->str).
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    controls: tuple[AuthControl, ...] = Field(min_length=1)

    @field_validator("controls")
    @classmethod
    def _no_duplicates(cls, value: tuple[AuthControl, ...]) -> tuple[AuthControl, ...]:
        ids = [control.id for control in value]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate control id")
        return value


def parse_auth_controls(raw: bytes) -> tuple[AuthControl, ...]:
    """Validate the bytes of an `auth_controls.json`. Raises AuthControlsError, never partial."""
    parsed: AuthControlsFile | None = None
    if len(raw) <= MAX_BYTES:
        try:
            parsed = AuthControlsFile.model_validate(json.loads(raw))
        except (ValueError, ValidationError):  # JSON and UTF-8 decode errors are ValueErrors
            parsed = None
    # Raised outside the `except` so the parser's message (which quotes the input) is dropped.
    if parsed is None:
        raise AuthControlsError
    return parsed.controls


def load_auth_controls() -> tuple[AuthControl, ...]:
    """The packaged list, validated."""
    try:
        raw = resources.files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME).read_bytes()
    except OSError:
        raw = None
    if raw is None:
        raise AuthControlsError
    return parse_auth_controls(raw)


def auth_control_ids() -> tuple[str, ...]:
    """The control IDs, in file order."""
    return tuple(control.id for control in load_auth_controls())
