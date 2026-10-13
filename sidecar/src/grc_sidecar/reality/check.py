"""The reality check: one policy claim against one offline export (ADR-016 decisions 5, 6, 7, 11).

The result is a count of exceptions to the claim "MFA is required", for THIS export, THIS claim and
THIS date. It is never "compliant": it says what the export shows, and that it is not evidence the
control works.

How the count is made, in one pass through the reader:
- In scope: `user.status` (stripped) is exactly one of the owner's `active_statuses`. Every other
  status is counted and listed as skipped. Malformed rows (the reader's count) are never counted.
- With MFA: the stripped, comma-split `authenticators.type` set meets the owner's `mfa_types`
  (exact, case-sensitive). An empty cell means no MFA. `authenticators.count` is never read (the
  reader does not even keep it): it counts password and email.
- Admins without MFA: among in-scope users without MFA, `user.isAdmin` is "true" or "false" ignoring
  case and surrounding spaces; anything else is `admin_unknown`.
- Phone-only: an in-scope user with at least one type in `phone_types` and no type in `mfa_types`
  that is not also in `phone_types`. A user with no MFA-capable type at all is NOT phone-only.
- Duplicate logins: every well-formed row is its own row. Nothing is merged, so a login that appears
  twice can be an exception on one row and not on the other; `duplicate_logins` says how many
  repeats there are, found by hashing, never by keeping a login.

The claim is re-verified against the CURRENT vault before the export is read (see `claims.py`).
Nothing is logged except counts: no login, no quote, no file name.
"""

import hashlib
import logging
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from grc_sidecar.errors import CheckRefusedError
from grc_sidecar.reality.claims import ClaimCandidate, verify_claim
from grc_sidecar.reality.export_reader import DEFAULT_CAPS, Caps, read_export
from grc_sidecar.reality.inspect import ValueCount, listed_counts
from grc_sidecar.vault.clock import Clock
from grc_sidecar.vault.models import IsoDate

MAX_LISTED_EXCEPTIONS: Final = 200
MAX_LOGIN_CHARS: Final = 256
MAX_TYPE_NAMES: Final = 50
MAX_TYPE_CHARS: Final = 64
MAX_REQUEST_QUOTE_CHARS: Final = 2000
MAX_PATH_CHARS: Final = 512
MAX_FILE_NAME_CHARS: Final = 260

STATEMENT: Final = "this export, this claim, this date; not evidence the control works"
STALENESS_NOTE: Final = (
    "The date is the one the owner supplied. How old the export is, and whether it is the right "
    "export, is the owner's responsibility."
)
DUPLICATE_NOTE: Final = (
    "Every row is counted on its own. A login that appears on more than one row can be an "
    "exception on one row and not on another."
)

_log = logging.getLogger(__name__)

_TypeName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TYPE_CHARS)
]


class ClaimRef(BaseModel):
    """The claim the owner chose, exactly as `GET /reality/claims` listed it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=MAX_REQUEST_QUOTE_CHARS)

    @model_validator(mode="after")
    def _lines_in_order(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line is before start_line")
        return self


class CheckRequest(BaseModel):
    """What the owner confirms before a check. There is no URL, token, credential or host field:
    the check never connects to a live system."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str = Field(min_length=1, max_length=MAX_FILE_NAME_CHARS)
    claim: ClaimRef
    mfa_types: list[_TypeName] = Field(min_length=1, max_length=MAX_TYPE_NAMES)
    phone_types: list[_TypeName] = Field(default_factory=list, max_length=MAX_TYPE_NAMES)
    active_statuses: list[_TypeName] = Field(
        default_factory=lambda: ["Active"], min_length=1, max_length=MAX_TYPE_NAMES
    )
    exported_at: IsoDate


class ListedLogin(BaseModel):
    """A `user.login` from the file: untrusted text, shortened for display."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    login: str
    truncated: bool


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: ClaimCandidate
    sha256: str  # of the export bytes that were counted
    exported_at: date  # the owner's date, not read from the file
    finding: Literal["exceptions_found", "no_exceptions_in_export"]
    statement: str
    staleness_note: str
    duplicate_note: str
    in_scope: int
    with_mfa: int
    without_mfa: int
    admins_without_mfa: int
    admin_unknown: int
    phone_only: int
    phone_types_named: bool
    skipped: list[ValueCount]  # in-scope status values are not here; every other one is
    skipped_not_listed: int
    malformed_rows: int
    duplicate_logins: int
    mfa_types_not_present: list[str]
    exceptions: list[ListedLogin]  # users in scope without MFA, in file order, at most 200
    exceptions_not_listed: int


def _split_types(cell: str) -> set[str]:
    return {part.strip() for part in cell.split(",") if part.strip()}


def run_check(
    request: CheckRequest,
    import_root: Path,
    vault_root: Path,
    clock: Clock,
    caps: Caps = DEFAULT_CAPS,
) -> CheckResult:
    """Verify the claim, read the export once, count. Writes nothing.

    Raises:
        CheckRefusedError: `exported_at_in_future`, `claim_not_verbatim` or `no_users_in_scope`.
        PathNotAllowedError, PathNotFoundError, ConfigurationError: from the path guards.
        ExportRefusedError: the file was refused (a fixed code, never text from the file).
        ControlMapTooLargeError, AuthControlsError: from the claim cross-check.
    """
    if request.exported_at > clock.today():
        raise CheckRefusedError("exported_at_in_future")
    claim = verify_claim(
        vault_root,
        path=request.claim.path,
        start_line=request.claim.start_line,
        end_line=request.claim.end_line,
        quote=request.claim.quote,
    )
    mfa_set = set(request.mfa_types)
    phone_set = set(request.phone_types)
    non_phone_mfa = mfa_set - phone_set
    active = set(request.active_statuses)

    skipped: Counter[str] = Counter()
    seen_types: set[str] = set()
    seen_logins: set[bytes] = set()
    exceptions: list[ListedLogin] = []
    duplicates = in_scope = with_mfa = without_mfa = 0
    admins = admin_unknown = phone_only = exceptions_not_listed = 0
    with read_export(request.file, import_root, caps) as reader:
        for record in reader.records():
            login = record.login.strip()
            digest = hashlib.sha256(login.encode("utf-8")).digest()
            if digest in seen_logins:
                duplicates += 1
            else:
                seen_logins.add(digest)
            types = _split_types(record.authenticator_types)
            seen_types |= types
            status = record.status.strip()
            if status not in active:
                skipped[status] += 1
                continue
            in_scope += 1
            if types & phone_set and not types & non_phone_mfa:
                phone_only += 1
            if types & mfa_set:
                with_mfa += 1
                continue
            without_mfa += 1
            admin = record.is_admin.strip().lower()
            if admin == "true":
                admins += 1
            elif admin != "false":
                admin_unknown += 1
            if len(exceptions) < MAX_LISTED_EXCEPTIONS:
                shown = login[:MAX_LOGIN_CHARS]
                exceptions.append(ListedLogin(login=shown, truncated=len(shown) < len(login)))
            else:
                exceptions_not_listed += 1
        sha256 = reader.sha256
        malformed = reader.malformed_rows
    if in_scope == 0:
        raise CheckRefusedError("no_users_in_scope")
    listed_skipped, skipped_not_listed = listed_counts(skipped)
    _log.info(
        "reality check: in_scope=%d without_mfa=%d malformed=%d duplicates=%d",
        in_scope,
        without_mfa,
        malformed,
        duplicates,
    )
    return CheckResult(
        claim=claim,
        sha256=sha256,
        exported_at=request.exported_at,
        finding="exceptions_found" if without_mfa > 0 else "no_exceptions_in_export",
        statement=STATEMENT,
        staleness_note=STALENESS_NOTE,
        duplicate_note=DUPLICATE_NOTE,
        in_scope=in_scope,
        with_mfa=with_mfa,
        without_mfa=without_mfa,
        admins_without_mfa=admins,
        admin_unknown=admin_unknown,
        phone_only=phone_only,
        phone_types_named=bool(phone_set),
        skipped=listed_skipped,
        skipped_not_listed=skipped_not_listed,
        malformed_rows=malformed,
        duplicate_logins=duplicates,
        mfa_types_not_present=sorted(mfa_set - seen_types),
        exceptions=exceptions,
        exceptions_not_listed=exceptions_not_listed,
    )
