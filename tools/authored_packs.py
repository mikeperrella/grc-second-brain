"""Generate the authored SOC 2 and ISO 27001 ID packs (ADR-008, M3e).

Development-only, standard library only, no network. SOC 2 and ISO 27001 are copyrighted, so these
packs hold control IDs and nothing else, except for the few controls the sample vault maps to,
where we add our own title and one or two sentence summary written in our own words.

The ID lists were written from the author's knowledge of the frameworks and have NOT been checked
against the published documents, so `id_list_verified_against` is "unverified" until a human does
that. Do not change that value without saying what was actually compared.

Usage (from the repo root):  python -I tools/authored_packs.py --out packs
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Final, NamedTuple

CONVERTER_VERSION: Final = "authored-1"
RETRIEVED_ON: Final = "2026-10-09"  # the date the lists were written


class Summary(NamedTuple):
    title: str  # our own wording, not the framework's control name
    text: str  # one or two sentences, our own words


def _ids(prefix: str, counts: dict[int, int]) -> list[str]:
    """`_ids("CC", {1: 5})` -> CC1.1 .. CC1.5 (criterion number -> how many points)."""
    return [
        f"{prefix}{major}.{minor}"
        for major, count in counts.items()
        for minor in range(1, count + 1)
    ]


# SOC 2 Trust Services Criteria. 33 common criteria + 3 + 2 + 5 + 18 = 61 IDs.
SOC2_FILES: Final[dict[str, tuple[str, list[str]]]] = {
    "cc": (
        "Common Criteria (CC)",
        _ids("CC", {1: 5, 2: 3, 3: 4, 4: 2, 5: 3, 6: 8, 7: 5, 8: 1, 9: 2}),
    ),
    "a": ("Availability (A)", _ids("A", {1: 3})),
    "c": ("Confidentiality (C)", _ids("C", {1: 2})),
    "pi": ("Processing Integrity (PI)", _ids("PI", {1: 5})),
    "p": (
        "Privacy (P)",
        _ids("P", {1: 1, 2: 1, 3: 2, 4: 3, 5: 2, 6: 7, 7: 1, 8: 1}),
    ),
}

SOC2_SUMMARIES: Final[dict[str, Summary]] = {
    "CC6.1": Summary(
        "Logical access safeguards",
        "The organization restricts who and what can reach its systems and data, using controls "
        "such as authentication, authorization and encryption.",
    ),
    "CC6.5": Summary(
        "Secure disposal",
        "Physical assets that hold data stay protected until that data has been destroyed or "
        "made unrecoverable and is no longer needed.",
    ),
    "CC7.4": Summary(
        "Carrying out incident response",
        "The organization follows its incident response process to contain, fix and communicate "
        "security incidents.",
    ),
    "CC9.2": Summary(
        "Vendor and partner risk",
        "Risks introduced by third parties, such as vendors and business partners, are "
        "identified, evaluated and kept under control.",
    ),
}

# ISO/IEC 27001:2022 Annex A. 37 + 8 + 14 + 34 = 93 IDs.
ISO_FILES: Final[dict[str, tuple[str, list[str]]]] = {
    "a5-organizational": ("Annex A theme 5, organizational", [f"A.5.{n}" for n in range(1, 38)]),
    "a6-people": ("Annex A theme 6, people", [f"A.6.{n}" for n in range(1, 9)]),
    "a7-physical": ("Annex A theme 7, physical", [f"A.7.{n}" for n in range(1, 15)]),
    "a8-technological": ("Annex A theme 8, technological", [f"A.8.{n}" for n in range(1, 35)]),
}

ISO_SUMMARIES: Final[dict[str, Summary]] = {
    "A.5.15": Summary(
        "Access rules",
        "The organization decides, documents and enforces who can get to information and related "
        "assets, both in systems and in physical spaces, in line with its business and security "
        "goals.",
    ),
    "A.5.19": Summary(
        "Supplier security",
        "The organization has a defined way to identify and control the security risk that comes "
        "with relying on outside suppliers.",
    ),
    "A.5.24": Summary(
        "Incident planning",
        "Before anything goes wrong, the organization decides who handles security incidents and "
        "how, and makes sure those people know their roles.",
    ),
    "A.5.26": Summary(
        "Incident response",
        "When a security incident happens, staff handle it using the written procedures prepared "
        "in advance.",
    ),
    "A.8.5": Summary(
        "Sign-in controls",
        "Authentication methods follow the organization's access restrictions and access control "
        "policy, with stronger methods expected where access is more sensitive.",
    ),
    "A.8.10": Summary(
        "Deleting information",
        "Data that is no longer needed is removed from systems, devices and storage media rather "
        "than kept indefinitely.",
    ),
    "A.8.24": Summary(
        "Cryptography rules",
        "The organization decides how encryption and keys are used and managed, and makes sure "
        "those decisions are followed.",
    ),
}


def render_file(
    pack_id: str, key: str, title: str, ids: list[str], summaries: dict[str, Summary]
) -> str:
    lines = [
        "---",
        f"doc_id: {pack_id}-{key}",
        "type: framework",
        'owner: "BizAI Solutions"',
        "---",
        f"# {title}",
        "",
        "> Control IDs only. Titles and summaries appear for a few controls and are the project's",
        "> own words; no framework text is reproduced here.",
        "",
    ]
    for identifier in ids:
        summary = summaries.get(identifier)
        if summary is None:
            lines.append(f"- **{identifier}**")
        else:
            lines.append(f"- **{identifier}** {summary.title}. {summary.text}")
    return "\n".join(lines) + "\n"


def content_sha256(files: dict[str, bytes]) -> str:
    """Same formula as the pack loader: sha256 over sorted (relative path, file sha256)."""
    pairs = [[name, hashlib.sha256(files[name]).hexdigest()] for name in sorted(files)]
    return hashlib.sha256(
        json.dumps(pairs, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_pack(
    *,
    pack_id: str,
    title: str,
    version: str,
    upstream_license: str,
    source_url: str,
    file_specs: dict[str, tuple[str, list[str]]],
    summaries: dict[str, Summary],
) -> dict[str, bytes]:
    all_ids = [identifier for _, ids in file_specs.values() for identifier in ids]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("duplicate control ID")
    if not set(summaries) <= set(all_ids):
        raise ValueError("a summary refers to an ID that is not in the list")
    files = {
        f"{key}.md": render_file(pack_id, key, heading, ids, summaries).encode("utf-8")
        for key, (heading, ids) in file_specs.items()
    }
    digest = content_sha256(files)
    metadata = {
        "pack_id": pack_id,
        "title": title,
        "version": version,
        "license": "BizAI-original (control IDs and original summaries)",
        "upstream_license": upstream_license,
        "source_url": source_url,
        "retrieved_on": RETRIEVED_ON,
        "source_sha256": digest,  # an authored pack has no download; this hashes the file set
        "content_sha256": digest,
        "origin": "original_authored",
        "content_kind": "ids_and_original_summaries",
        "redistributable": True,
        "converter_version": CONVERTER_VERSION,
        "id_list_verified_against": "unverified",
    }
    files["pack.json"] = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return files


def build_all() -> dict[str, dict[str, bytes]]:
    return {
        "soc2-tsc-ids": build_pack(
            pack_id="soc2-tsc-ids",
            title="SOC 2 Trust Services Criteria: control IDs",
            version="2017 TSC (ID list unverified)",
            upstream_license="AICPA copyright; text not bundled",
            source_url="https://www.aicpa-cima.com/",
            file_specs=SOC2_FILES,
            summaries=SOC2_SUMMARIES,
        ),
        "iso27001-annex-a-ids": build_pack(
            pack_id="iso27001-annex-a-ids",
            title="ISO/IEC 27001:2022 Annex A: control IDs",
            version="2022",
            upstream_license="ISO/IEC copyright; text not bundled",
            source_url="https://www.iso.org/standard/27001",
            file_specs=ISO_FILES,
            summaries=ISO_SUMMARIES,
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    for pack_id, files in build_all().items():
        target = args.out / pack_id
        if target.exists() and any(target.iterdir()):
            print(f"{pack_id}: output directory must be empty or absent")
            return 2
        target.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (target / name).write_bytes(data)
        print(f"{pack_id}: {len(files) - 1} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
