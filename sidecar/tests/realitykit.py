"""Builders for the reality-check tests (ADR-016). All data is synthetic: `example.test` addresses
and made-up names. Hostile and size variants are built here, in code, never committed."""

import csv
import hashlib
import io
from collections import Counter
from pathlib import Path

from grc_sidecar.reality.claims import ClaimCandidate

from .vaultkit import REPO_ROOT, doc_text

FIXTURE = REPO_ROOT / "fixtures" / "exports" / "okta-mfa-enrollment-synthetic.csv"
CANARY = "CANARY-7f3a9c-not-for-logs"
CHUNK_BYTES = 64 * 1024

HEADERS = [
    "user.id",
    "user.fullName",
    "user.email",
    "user.mobilePhone",
    "authenticators.type",
    "authenticators.count",
    "groups.name",
    "user.isAdmin",
    "user.status",
    "user.login",
    "user.primaryPhone",
    "user.created",
    "user.activated",
]
REQUIRED = ["user.login", "user.status", "authenticators.type", "user.isAdmin"]


def row(
    login: str = "a@example.test",
    status: str = "Active",
    types: str = "Password",
    admin: str = "false",
    full_name: str = "Synthetic User",
) -> list[str]:
    """One well-formed 13-cell row. The nine dropped columns carry harmless text."""
    return [
        "00uSYN",
        full_name,
        login,
        "+1-555-0100",
        types,
        "1",
        "Everyone",
        admin,
        status,
        login,
        "+1-555-0200",
        "2025-01-01T00:00:00.000Z",
        "2025-01-01T00:00:00.000Z",
    ]


def csv_text(
    rows: list[list[str]],
    headers: list[str] | None = None,
    eol: str = "\r\n",
    *,
    quote_all: bool = False,
) -> str:
    out = io.StringIO()
    writer = csv.writer(
        out, lineterminator=eol, quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL
    )
    writer.writerow(HEADERS if headers is None else headers)
    writer.writerows(rows)
    return out.getvalue()


def csv_bytes(
    rows: list[list[str]], headers: list[str] | None = None, *, quote_all: bool = False
) -> bytes:
    return csv_text(rows, headers, quote_all=quote_all).encode("utf-8")


def write(root: Path, data: bytes, name: str = "export.csv") -> str:
    (root / name).write_bytes(data)
    return name


def expected_counts(path: Path) -> dict[str, object]:
    """What inspecting this file must report, worked out here with a separate csv pass."""
    text = path.read_bytes().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    statuses: Counter[str] = Counter(r["user.status"].strip() for r in rows)
    types: Counter[str] = Counter()
    for r in rows:
        types.update({p.strip() for p in r["authenticators.type"].split(",") if p.strip()})
    logins = [r["user.login"].strip() for r in rows]
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": len(path.read_bytes()),
        "data_rows": len(rows),
        "malformed_rows": 0,
        "statuses": dict(statuses),
        "authenticator_types": dict(types),
        "duplicate_logins": len(logins) - len(set(logins)),
    }


def as_counts(found: dict[str, object]) -> dict[str, object]:
    """An inspection (as a dict) reduced to the shape `expected_counts` returns."""
    statuses = found["statuses"]
    types = found["authenticator_types"]
    assert isinstance(statuses, list) and isinstance(types, list)
    return {
        "sha256": found["sha256"],
        "size_bytes": found["size_bytes"],
        "data_rows": found["data_rows"],
        "malformed_rows": found["malformed_rows"],
        "statuses": {item["value"]: item["count"] for item in statuses},
        "authenticator_types": {item["value"]: item["count"] for item in types},
        "duplicate_logins": found["duplicate_logins"],
    }


def expected_check(
    path: Path,
    mfa_types: list[str],
    phone_types: list[str],
    active: tuple[str, ...] = ("Active",),
) -> dict[str, object]:
    """What a check of this (well-formed) export must report, worked out with a separate csv pass
    and a different formulation from the code under test (lists and `any`/`all`, no helper)."""
    text = path.read_bytes().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text, newline="")))

    def types_of(r: dict[str, str]) -> list[str]:
        return [p.strip() for p in r["authenticators.type"].split(",") if p.strip()]

    scoped = [r for r in rows if r["user.status"].strip() in active]
    without = [r for r in scoped if not any(t in mfa_types for t in types_of(r))]
    phone_only = [
        r
        for r in scoped
        if any(t in phone_types for t in types_of(r))
        and all(t not in mfa_types or t in phone_types for t in types_of(r))
    ]
    admin_values = [r["user.isAdmin"].strip().lower() for r in without]
    everything = {t for r in rows for t in types_of(r)}
    logins = [r["user.login"].strip() for r in rows]
    return {
        "in_scope": len(scoped),
        "with_mfa": len(scoped) - len(without),
        "without_mfa": len(without),
        "admins_without_mfa": admin_values.count("true"),
        "admin_unknown": len([v for v in admin_values if v not in ("true", "false")]),
        "phone_only": len(phone_only),
        "skipped": dict(Counter(r["user.status"].strip() for r in rows if r not in scoped)),
        "malformed_rows": 0,
        "duplicate_logins": len(logins) - len(set(logins)),
        "mfa_types_not_present": sorted(t for t in mfa_types if t not in everything),
        "exceptions": [r["user.login"].strip() for r in without],
    }


def as_check_counts(found: dict[str, object]) -> dict[str, object]:
    """A check result (as a dict) reduced to the shape `expected_check` returns."""
    skipped = found["skipped"]
    listed = found["exceptions"]
    assert isinstance(skipped, list) and isinstance(listed, list)
    assert found["skipped_not_listed"] == 0 and found["exceptions_not_listed"] == 0
    keys = (
        "in_scope",
        "with_mfa",
        "without_mfa",
        "admins_without_mfa",
        "admin_unknown",
        "phone_only",
        "malformed_rows",
        "duplicate_logins",
        "mfa_types_not_present",
    )
    reduced: dict[str, object] = {key: found[key] for key in keys}
    reduced["skipped"] = {item["value"]: item["count"] for item in skipped}
    reduced["exceptions"] = [item["login"] for item in listed]
    return reduced


CLAIM_SENTENCE = "MFA is required for all staff accounts."


def write_policy_vault(
    root: Path,
    sentence: str = CLAIM_SENTENCE,
    *,
    doc_type: str = "policy",
    mappings: str = '["NIST-800-53:IA-2"]',
) -> None:
    """A one-document synthetic vault whose policy holds `sentence` as its own paragraph."""
    root.mkdir(parents=True, exist_ok=True)
    frontmatter = (
        "doc_id: POL-1",
        f"type: {doc_type}",
        "owner: Test Owner",
        "review_by: 2030-01-01",
        f"control_mappings: {mappings}",
    )
    body = f"# Synthetic policy\n\n{sentence}\n"
    (root / "policy.md").write_text(doc_text(body, *frontmatter), encoding="utf-8")


def claim_ref(candidate: ClaimCandidate | dict[str, object]) -> dict[str, object]:
    """The request's `claim` for a candidate, as the claims route would have listed it."""
    shown = candidate if isinstance(candidate, dict) else candidate.model_dump()
    return {key: shown[key] for key in ("path", "start_line", "end_line", "quote")}


def straddling_export(special: str) -> tuple[bytes, str]:
    """An export whose last row has `special` inside a quoted `user.login`, with the first byte of
    `special` at byte index 65,535: the 64 KiB chunk boundary falls inside (or right after the
    first byte of) the sequence. Returns the bytes and the login the row must parse to."""
    head = ",".join(HEADERS) + "\n"
    prefix = '00uBIG,Name,e@example.test,p,Password,1,g,false,Active,"lead'
    filler_total = (CHUNK_BYTES - 1) - len(head) - len(prefix)

    def filler(n: int) -> str:
        return f"00uFIL,{'x' * n},e@example.test,p,Password,1,g,false,Active,f@example.test,p,c,a\n"

    base = len(filler(0))
    rows = ""
    remaining = filler_total
    while remaining >= 2 * (base + 3000):
        rows += filler(3000)
        remaining -= base + 3000
    first = (remaining - 2 * base) // 2
    rows += filler(first) + filler(remaining - 2 * base - first)
    login = f"lead{special}tail"
    last = f'{prefix[: -len("lead")]}{login}",p,c,a\n'
    data = (head + rows + last).encode("utf-8")
    assert data.index(special.encode("utf-8"), CHUNK_BYTES - 2) == CHUNK_BYTES - 1
    return data, login
