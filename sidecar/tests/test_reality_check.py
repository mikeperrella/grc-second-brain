"""The reality check (ADR-016 decisions 5, 6, 7, 11): counting rules, each edge case, and the
refusals. Expected counts for the demo come from `expected_check`, an independent pass over the
file, not from numbers assumed in advance."""

import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest

from grc_sidecar.errors import CheckRefusedError, ExportRefusedError
from grc_sidecar.reality.check import (
    DUPLICATE_NOTE,
    MAX_LISTED_EXCEPTIONS,
    MAX_LOGIN_CHARS,
    STALENESS_NOTE,
    STATEMENT,
    CheckRequest,
    CheckResult,
    ClaimRef,
    run_check,
)
from grc_sidecar.reality.claims import find_claims
from grc_sidecar.reality.export_reader import Caps
from grc_sidecar.vault.clock import FixedClock

from .realitykit import (
    FIXTURE,
    HEADERS,
    as_check_counts,
    claim_ref,
    csv_bytes,
    expected_check,
    row,
    write,
    write_policy_vault,
)
from .vaultkit import DEMO_VAULT

TODAY = date(2026, 10, 13)
CLOCK = FixedClock(TODAY)
EXPORTED = "2026-10-01"
OKTA = "Okta Verify"


class Env:
    """A vault holding one claim, an import folder, and a request builder."""

    def __init__(self, tmp_path: Path, vault: Path | None = None) -> None:
        self.vault = vault if vault is not None else tmp_path / "vault"
        if vault is None:
            write_policy_vault(self.vault)
        self.imports = tmp_path / "imports"
        self.imports.mkdir()
        (candidate,) = find_claims(self.vault).candidates
        self.claim = claim_ref(candidate)

    def request(self, **fields: object) -> CheckRequest:
        body: dict[str, object] = {
            "file": "okta.csv",
            "claim": self.claim,
            "mfa_types": [OKTA],
            "phone_types": ["Phone"],
            "exported_at": EXPORTED,
        }
        body.update(fields)
        return CheckRequest.model_validate(body)

    def run(self, rows: list[list[str]] | None = None, **fields: object) -> CheckResult:
        if rows is not None:
            write(self.imports, csv_bytes(rows), "okta.csv")
        return run_check(self.request(**fields), self.imports, self.vault, CLOCK)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def demo_env(tmp_path: Path) -> Env:
    """A copy of the demo vault (never the original) and the synthetic export."""
    vault = tmp_path / "demo-vault"
    shutil.copytree(DEMO_VAULT, vault)
    made = Env(tmp_path, vault)
    write(made.imports, FIXTURE.read_bytes(), "okta.csv")
    # The demo has exactly one candidate: the MFA sentence at access-control-policy.md:23.
    assert made.claim["path"] == "access-control-policy.md" and made.claim["start_line"] == 23
    return made


# ----- the demo, end to end -----


def test_the_demo_without_phone_matches_an_independent_count(tmp_path: Path) -> None:
    made = demo_env(tmp_path)
    result = made.run(mfa_types=[OKTA], phone_types=["Phone"])
    assert as_check_counts(result.model_dump()) == expected_check(FIXTURE, [OKTA], ["Phone"])
    assert result.finding == "exceptions_found" and result.without_mfa > 0
    assert result.phone_only > 0  # phone-only users are exceptions under this set
    assert result.claim.path == "access-control-policy.md"
    assert result.claim.mapped_to_authentication_control is True


def test_the_demo_with_phone_in_the_set_matches_an_independent_count(tmp_path: Path) -> None:
    made = demo_env(tmp_path)
    result = made.run(mfa_types=[OKTA, "Phone"], phone_types=["Phone"])
    assert as_check_counts(result.model_dump()) == expected_check(
        FIXTURE, [OKTA, "Phone"], ["Phone"]
    )
    without_phone = made.run(mfa_types=[OKTA], phone_types=["Phone"])
    assert result.without_mfa < without_phone.without_mfa  # counting phone as MFA removes some
    assert result.phone_only == without_phone.phone_only  # phone-only does not depend on the set


def test_a_confirmed_type_that_is_not_in_the_file_is_reported_not_refused(tmp_path: Path) -> None:
    made = demo_env(tmp_path)
    result = made.run(mfa_types=[OKTA, "Security Key"], phone_types=["Phone"])
    assert result.mfa_types_not_present == ["Security Key"]
    assert as_check_counts(result.model_dump()) == expected_check(
        FIXTURE, [OKTA, "Security Key"], ["Phone"]
    )


def test_the_result_carries_the_hash_the_date_and_the_fixed_wording(tmp_path: Path) -> None:
    made = demo_env(tmp_path)
    result = made.run()
    assert result.sha256 == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert result.exported_at == date.fromisoformat(EXPORTED)
    assert result.statement == STATEMENT
    assert STATEMENT == "this export, this claim, this date; not evidence the control works"
    assert result.staleness_note == STALENESS_NOTE and result.duplicate_note == DUPLICATE_NOTE
    fixed = (STATEMENT + STALENESS_NOTE + DUPLICATE_NOTE).lower()
    assert "complian" not in fixed
    assert "complian" not in result.model_dump_json().lower()


# ----- what counts as MFA -----


@pytest.mark.parametrize(
    "cell",
    [
        "Password",
        "Password,Email",
        "Email",
        "",
        "Forgotten Password Question",
        "Password,Email,Forgotten Password Question",
        "okta verify",  # exact match: letter case differs
        "Okta Verify Plus",  # exact match: not a substring match
    ],
)
def test_only_a_confirmed_type_is_mfa(env: Env, cell: str) -> None:  # [NEG]
    result = env.run([row(types=cell)])
    assert (result.with_mfa, result.without_mfa) == (0, 1)


@pytest.mark.parametrize("cell", [OKTA, "Password,Okta Verify", "Password, Okta Verify ,Email"])
def test_a_confirmed_type_among_others_is_mfa(env: Env, cell: str) -> None:
    result = env.run([row(types=cell)])
    assert (result.with_mfa, result.without_mfa) == (1, 0)
    assert result.finding == "no_exceptions_in_export"


def test_two_or_more_types_are_not_mfa(env: Env) -> None:  # [NEG]
    result = env.run([row(types="Password,Email,Forgotten Password Question")])
    assert result.without_mfa == 1  # three types, none of them MFA


def test_the_count_column_is_never_used(env: Env) -> None:  # [NEG]
    cells = row(types="Password")
    cells[HEADERS.index("authenticators.count")] = "5"
    assert env.run([cells]).without_mfa == 1


# ----- scope -----


def test_only_the_active_status_is_in_scope_and_the_rest_are_counted_by_name(env: Env) -> None:
    rows = [
        row("a@e.test", "Active", OKTA),
        row("b@e.test", " Active ", "Password"),
        row("c@e.test", "Deprovisioned", "Password"),
        row("d@e.test", "Suspended", "Password"),
        row("e@e.test", "Suspended", OKTA),
        row("f@e.test", "active", "Password"),  # exact: letter case differs
    ]
    result = env.run(rows)
    assert (result.in_scope, result.with_mfa, result.without_mfa) == (2, 1, 1)
    skipped = {item.value: item.count for item in result.skipped}
    assert skipped == {"Suspended": 2, "Deprovisioned": 1, "active": 1}
    assert [item.login for item in result.exceptions] == ["b@e.test"]


def test_the_owner_can_choose_which_statuses_are_in_scope(env: Env) -> None:
    rows = [row("a@e.test", "Active", OKTA), row("b@e.test", "Suspended", "Password")]
    result = env.run(rows, active_statuses=["Active", "Suspended"])
    assert (result.in_scope, result.without_mfa, result.skipped) == (2, 1, [])


def test_a_file_with_nobody_in_scope_is_refused(env: Env) -> None:  # [NEG]
    with pytest.raises(CheckRefusedError) as raised:
        env.run([row(status="Deprovisioned")])
    assert raised.value.code == "no_users_in_scope"


# ----- administrators -----


def test_the_admin_count_among_users_without_mfa(env: Env) -> None:
    rows = [
        row("a@e.test", types="Password", admin="true"),
        row("b@e.test", types="Password", admin="TRUE"),
        row("c@e.test", types="Password", admin=" True "),
        row("d@e.test", types="Password", admin="false"),
        row("e@e.test", types="Password", admin="False"),
        row("f@e.test", types="Password", admin="yes"),
        row("g@e.test", types="Password", admin=""),
        row("h@e.test", types=OKTA, admin="true"),  # has MFA: not counted
        row("i@e.test", status="Suspended", types="Password", admin="true"),  # not in scope
    ]
    result = env.run(rows)
    assert (result.without_mfa, result.admins_without_mfa, result.admin_unknown) == (7, 3, 2)


# ----- phone-only -----


@pytest.mark.parametrize(
    ("cell", "mfa", "phone", "phone_only", "with_mfa"),
    [
        ("Phone", [OKTA], ["Phone"], 1, 0),
        ("Password,Phone", [OKTA], ["Phone"], 1, 0),
        ("Phone", [OKTA, "Phone"], ["Phone"], 1, 1),  # counted as MFA and still phone-only
        ("Phone,Okta Verify", [OKTA], ["Phone"], 0, 1),
        ("Phone,Okta Verify", [OKTA, "Phone"], ["Phone"], 0, 1),
        ("Okta Verify", [OKTA], ["Phone"], 0, 1),
        ("Password,Email", [OKTA], ["Phone"], 0, 0),  # no MFA-capable type: not phone-only
        ("", [OKTA], ["Phone"], 0, 0),
        ("Phone", [OKTA], [], 0, 0),  # no phone types named
        ("Phone,SMS", [OKTA, "SMS", "Phone"], ["Phone", "SMS"], 1, 1),
        ("Phone,Okta Verify", [OKTA, "Phone"], ["Phone", "SMS"], 0, 1),
    ],
)
def test_phone_only_truth_table(
    env: Env,
    cell: str,
    mfa: list[str],
    phone: list[str],
    phone_only: int,
    with_mfa: int,
) -> None:
    result = env.run([row(types=cell)], mfa_types=mfa, phone_types=phone)
    assert (result.phone_only, result.with_mfa) == (phone_only, with_mfa)
    assert result.phone_types_named is bool(phone)


# ----- the exception list, duplicates, malformed rows -----


def test_the_exception_list_is_capped_and_the_rest_counted(env: Env) -> None:
    exact = [row(f"u{n:03}@e.test", types="Password") for n in range(MAX_LISTED_EXCEPTIONS)]
    result = env.run(exact)
    assert (len(result.exceptions), result.exceptions_not_listed) == (MAX_LISTED_EXCEPTIONS, 0)
    over = env.run([*exact, row("last@e.test", types="Password")])
    assert (len(over.exceptions), over.exceptions_not_listed) == (MAX_LISTED_EXCEPTIONS, 1)
    assert over.without_mfa == MAX_LISTED_EXCEPTIONS + 1
    assert over.exceptions[-1].login == f"u{MAX_LISTED_EXCEPTIONS - 1:03}@e.test"  # file order


def test_a_long_login_is_shortened_with_a_flag(env: Env) -> None:
    long_login = "x" * (MAX_LOGIN_CHARS + 50)
    result = env.run([row(long_login, types="Password"), row("s@e.test", types="Password")])
    first, second = result.exceptions
    assert (len(first.login), first.truncated) == (MAX_LOGIN_CHARS, True)
    assert (second.login, second.truncated) == ("s@e.test", False)


def test_a_duplicate_login_is_two_rows_and_is_reported(env: Env) -> None:
    rows = [row("dup@e.test", types=OKTA), row("dup@e.test", types="Password"), row("o@e.test")]
    result = env.run(rows)
    assert result.duplicate_logins == 1
    assert (result.in_scope, result.with_mfa, result.without_mfa) == (3, 1, 2)
    assert [item.login for item in result.exceptions] == ["dup@e.test", "o@e.test"]


def test_a_malformed_row_is_reported_and_never_counted(env: Env) -> None:
    good = row("a@e.test", types="Password")
    short = good[:-1]  # one cell short
    long = [*good, "extra"]
    result = env.run([good, short, long, row("b@e.test", types=OKTA)])
    assert result.malformed_rows == 2
    assert (result.in_scope, result.without_mfa) == (2, 1)


def test_a_file_cap_is_a_typed_refusal(tmp_path: Path) -> None:  # [NEG]
    made = Env(tmp_path)
    write(made.imports, csv_bytes([row(), row()]), "okta.csv")
    with pytest.raises(ExportRefusedError) as raised:
        run_check(made.request(), made.imports, made.vault, CLOCK, Caps(max_rows=1))
    assert raised.value.code == "too_many_rows"


# ----- the owner's date -----


def test_an_export_date_after_today_is_refused_before_anything_is_read(env: Env) -> None:  # [NEG]
    # The file does not exist: the refusal must come first, not a "file not found".
    with pytest.raises(CheckRefusedError) as raised:
        run_check(
            env.request(exported_at="2026-10-14", file="missing.csv"), env.imports, env.vault, CLOCK
        )
    assert raised.value.code == "exported_at_in_future"


def test_an_export_date_of_today_or_earlier_is_accepted(env: Env) -> None:
    for day in ("2026-10-13", "2020-01-01"):
        assert env.run([row()], exported_at=day).exported_at == date.fromisoformat(day)


@pytest.mark.parametrize(
    "text", ["2026-13-01", "2026-02-30", "13/10/2026", "2026-10-13T10:00:00", ""]
)
def test_a_malformed_export_date_is_not_a_request(env: Env, text: str) -> None:  # [NEG]
    with pytest.raises(ValueError):
        env.request(exported_at=text)


# ----- the claim is re-verified against the current vault -----------------------------------------


def test_a_fragment_of_the_claim_that_drops_its_qualifier_words_is_refused(  # [NEG]
    tmp_path: Path,
) -> None:
    made = demo_env(tmp_path)
    quote = str(made.claim["quote"])
    fragment = quote.split(",")[0]  # "... customer data", without ", and for all remote access."
    assert fragment != quote
    with pytest.raises(CheckRefusedError) as raised:
        made.run(claim={**made.claim, "quote": fragment})
    assert raised.value.code == "claim_not_verbatim"


def test_a_claim_edited_in_the_vault_after_it_was_listed_is_refused(  # [NEG]
    tmp_path: Path,
) -> None:
    made = demo_env(tmp_path)
    policy = made.vault / "access-control-policy.md"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("is required for", "is encouraged for"),
        encoding="utf-8",
    )
    with pytest.raises(CheckRefusedError) as raised:
        made.run()
    assert raised.value.code == "claim_not_verbatim"


def test_a_claim_whose_lines_moved_is_refused(tmp_path: Path) -> None:  # [NEG]
    made = demo_env(tmp_path)
    policy = made.vault / "access-control-policy.md"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("## Purpose", "## Purpose\n\nAdded line.", 1),
        encoding="utf-8",
    )
    with pytest.raises(CheckRefusedError):
        made.run()


def test_a_claim_path_outside_the_vault_is_refused(tmp_path: Path) -> None:  # [NEG]
    made = Env(tmp_path)
    write_policy_vault(tmp_path / "outside")
    for path in ("../outside/policy.md", str(tmp_path / "outside" / "policy.md")):
        with pytest.raises(CheckRefusedError):
            made.run([row()], claim={**made.claim, "path": path})


# ----- the request has no way to name a live system -----


def test_the_request_models_have_no_url_token_credential_or_host_field() -> None:  # [NEG]
    names = set(CheckRequest.model_fields) | set(ClaimRef.model_fields)
    assert names == {
        "file",
        "claim",
        "mfa_types",
        "phone_types",
        "active_statuses",
        "exported_at",
        "path",
        "start_line",
        "end_line",
        "quote",
    }


@pytest.mark.parametrize("field", ["url", "token", "host", "password", "api_key"])
def test_an_unknown_request_field_is_rejected(env: Env, field: str) -> None:  # [NEG]
    with pytest.raises(ValueError):
        env.request(**{field: "x"})
