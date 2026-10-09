"""The committed NIST SP 800-53 Rev 5 pack (M3f).

Expected counts are the counts in the OSCAL source (usnistgov/oscal-content, pinned commit):
1196 controls and enhancements, of which 1014 are active and 182 withdrawn, in 20 families.
"""

import re
from pathlib import Path

from grc_sidecar.packs.loader import verify_pack

PACK = Path(__file__).resolve().parents[2] / "packs" / "nist-sp800-53-r5"
SOURCE_ACTIVE = 1014
SOURCE_WITHDRAWN = 182
SOURCE_FAMILIES = 20
HEADING = re.compile(r"^#{2,6} (\S+)", re.MULTILINE)
STUB = re.compile(r"^- \*\*(\S+)\*\*: Withdrawn\.", re.MULTILINE)


def collect() -> tuple[list[str], list[str]]:
    active: list[str] = []
    withdrawn: list[str] = []
    for path in sorted(PACK.glob("*.md")):
        head, _, tail = path.read_text(encoding="utf-8").partition("## Withdrawn controls")
        active += HEADING.findall(head)
        withdrawn += STUB.findall(tail)
    return active, withdrawn


def test_pack_matches_its_manifest_and_is_full_text_public_domain() -> None:
    metadata = verify_pack(PACK)
    assert metadata.version == "rev5" and metadata.redistributable
    assert metadata.origin == "converted_public_domain"


def test_one_markdown_file_per_control_family() -> None:
    assert len(list(PACK.glob("*.md"))) == SOURCE_FAMILIES


def test_control_counts_equal_the_source_counts() -> None:
    active, withdrawn = collect()
    assert (len(active), len(withdrawn)) == (SOURCE_ACTIVE, SOURCE_WITHDRAWN)


def test_control_ids_are_unique_across_the_pack() -> None:
    active, withdrawn = collect()
    everything = active + withdrawn
    assert len(set(everything)) == len(everything) == SOURCE_ACTIVE + SOURCE_WITHDRAWN


def test_known_controls_land_in_the_right_section() -> None:
    active, withdrawn = collect()
    assert "AC-2" in active and "AC-2(1)" in active
    assert "AC-2(10)" in withdrawn and "AC-2(10)" not in active
