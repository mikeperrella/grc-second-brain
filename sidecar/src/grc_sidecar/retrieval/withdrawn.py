"""Withdrawn framework controls (ADR-008, ADR-010, M4f).

NIST catalogs keep controls that were later withdrawn (merged elsewhere, moved or dropped) so old
references still resolve. The pack converter writes them as one-line stubs in a final
`## Withdrawn controls` section of each file: `- **03.01.13**: Withdrawn. Addressed by: 03.13.08.`
They must never read like current requirements, so retrieval leaves them out by default.

The one exception: a question that is ONLY identifiers (`03.01.13`, `AC-2`) and names a control
that has a stub in the chunk may get that chunk back, marked `withdrawn`, so the answer can say
"that control was withdrawn". Known limit: stubs are grouped into chunks, so the chunk returned
can hold other withdrawn controls' stubs too; every one of them is marked withdrawn and none
carries requirement text.
"""

import re
from typing import Final

SECTION_TITLE: Final = "Withdrawn controls"
_HEADING_SEPARATOR: Final = " > "
_STUB_ID: Final = re.compile(r"^- \*\*(\S+?)\*\*: Withdrawn\.", re.MULTILINE)


def is_withdrawn_section(heading_path: str) -> bool:
    """True for a chunk under the `Withdrawn controls` heading of a pack file."""
    return heading_path.split(_HEADING_SEPARATOR)[-1] == SECTION_TITLE


def stub_ids(text: str) -> tuple[str, ...]:
    """The control IDs of the withdrawn stubs in a chunk's text, in order."""
    return tuple(_STUB_ID.findall(text))


def names_a_stub(text: str, identifier_forms: tuple[tuple[str, ...], ...]) -> bool:
    """True if one of the written forms of a queried identifier is a stub ID in `text`."""
    wanted = {form.casefold() for forms in identifier_forms for form in forms}
    return any(stub.casefold() in wanted for stub in stub_ids(text))
