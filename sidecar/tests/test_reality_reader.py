"""The export reader and inspect (ADR-016 decisions 4, 5, 8, 9): one open, one pass, hash of the
bytes parsed, four fields kept, typed refusals, nothing that identifies a person in any output."""

import codecs
import hashlib
from pathlib import Path

import pytest

from grc_sidecar.errors import ExportRefusedError, PathNotAllowedError, PathNotFoundError
from grc_sidecar.reality.export_reader import (
    DEFAULT_CAPS,
    REQUIRED_COLUMNS,
    Caps,
    ExportRecord,
    read_export,
)
from grc_sidecar.reality.inspect import MAX_LISTED_VALUES, MAX_VALUE_CHARS, inspect_export

from .realitykit import (
    CANARY,
    CHUNK_BYTES,
    FIXTURE,
    HEADERS,
    as_counts,
    csv_bytes,
    csv_text,
    expected_counts,
    row,
    straddling_export,
    write,
)

SIMPLE = [
    row("a@example.test", "Active", "Password,Okta Verify", "false"),
    row("b@example.test", "Active", "Password,Email", "true"),
    row("c@example.test", "Suspended", "", "false"),
]


def refusal(root: Path, data: bytes, caps: Caps = DEFAULT_CAPS) -> ExportRefusedError:
    name = write(root, data)
    with pytest.raises(ExportRefusedError) as info:
        inspect_export(name, root, caps)
    return info.value


def records_of(root: Path, data: bytes, caps: Caps = DEFAULT_CAPS) -> list[ExportRecord]:
    name = write(root, data)
    with read_export(name, root, caps) as reader:
        return list(reader.records())


# ----- the path guard --------------------------------------------------------------------------


def test_a_name_outside_the_import_folder_is_refused(tmp_path: Path) -> None:  # [NEG]
    root = tmp_path / "imports"
    root.mkdir()
    (tmp_path / "outside.csv").write_bytes(csv_bytes(SIMPLE))
    with pytest.raises(PathNotAllowedError):
        inspect_export("../outside.csv", root)
    with pytest.raises(PathNotAllowedError):
        inspect_export(str(tmp_path / "outside.csv"), root)  # an absolute path
    with pytest.raises(PathNotAllowedError):
        inspect_export("", root)


def test_a_missing_file_inside_the_folder_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(PathNotFoundError):
        inspect_export("nothing.csv", tmp_path)


def test_a_folder_is_not_a_readable_export(tmp_path: Path) -> None:  # [NEG]
    (tmp_path / "folder.csv").mkdir()
    with pytest.raises(ExportRefusedError) as info:
        inspect_export("folder.csv", tmp_path)
    assert info.value.code == "unreadable"


# ----- encodings -------------------------------------------------------------------------------


def _encodings(text: str) -> dict[str, bytes]:
    return {
        "utf-8": text.encode("utf-8"),
        "utf-8-bom": codecs.BOM_UTF8 + text.encode("utf-8"),
        "utf-16-le-bom": codecs.BOM_UTF16_LE + text.encode("utf-16-le"),
        "utf-16-be-bom": codecs.BOM_UTF16_BE + text.encode("utf-16-be"),
    }


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-bom", "utf-16-le-bom", "utf-16-be-bom"])
def test_each_accepted_encoding_gives_the_same_counts(tmp_path: Path, encoding: str) -> None:
    text = csv_text([*SIMPLE, row("é@example.test", "Active", "Okta Verify")])
    data = _encodings(text)[encoding]
    found = inspect_export(write(tmp_path, data), tmp_path)
    assert found.data_rows == 4 and found.malformed_rows == 0
    assert {v.value: v.count for v in found.statuses} == {"Active": 3, "Suspended": 1}
    assert found.sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("latin-1 text", csv_text([row("é@example.test")]).encode("latin-1")),
        ("invalid utf-8", csv_bytes(SIMPLE) + b"\x80\x81"),
        ("cut-off multibyte tail", csv_bytes(SIMPLE) + b"\xe2\x82"),
        ("utf-32 le mark", codecs.BOM_UTF32_LE + csv_text(SIMPLE).encode("utf-32-le")),
        ("utf-32 be mark", codecs.BOM_UTF32_BE + csv_text(SIMPLE).encode("utf-32-be")),
        (
            "cut-off utf-16 tail",
            codecs.BOM_UTF16_LE + csv_text(SIMPLE).encode("utf-16-le") + b"\x41",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and len(value) < 40 else "data",
)
def test_an_encoding_outside_the_allowlist_is_refused(  # [NEG]
    tmp_path: Path, label: str, data: bytes
) -> None:
    assert refusal(tmp_path, data).code == "encoding_not_allowed", label


def test_utf_16_without_a_mark_is_refused(tmp_path: Path) -> None:  # [NEG]
    data = csv_text(SIMPLE).encode("utf-16-le")
    assert refusal(tmp_path, data).code in {"encoding_not_allowed", "nul_character"}


@pytest.mark.parametrize("column", ["user.login", "user.fullName", "user.email"])
def test_a_nul_character_is_refused_wherever_it_is(tmp_path: Path, column: str) -> None:  # [NEG]
    cells = row()
    cells[HEADERS.index(column)] = "ab\x00cd"
    assert refusal(tmp_path, csv_bytes([cells])).code == "nul_character"


# ----- caps ------------------------------------------------------------------------------------


def test_the_file_size_cap_is_exact(tmp_path: Path) -> None:  # [NEG]
    data = csv_bytes(SIMPLE)
    assert inspect_export(write(tmp_path, data), tmp_path, Caps(max_file_bytes=len(data)))
    assert refusal(tmp_path, data, Caps(max_file_bytes=len(data) - 1)).code == "file_too_large"


def test_the_row_cap_is_exact(tmp_path: Path) -> None:  # [NEG]
    data = csv_bytes(SIMPLE)
    assert inspect_export(write(tmp_path, data), tmp_path, Caps(max_rows=3)).data_rows == 3
    assert refusal(tmp_path, data, Caps(max_rows=2)).code == "too_many_rows"


@pytest.mark.parametrize("column", ["user.login", "user.fullName"])
def test_the_field_cap_is_exact_and_covers_dropped_columns(  # [NEG]
    tmp_path: Path, column: str
) -> None:
    caps = Caps(max_field_chars=40)
    ok = row()
    ok[HEADERS.index(column)] = "x" * 40
    assert inspect_export(write(tmp_path, csv_bytes([ok])), tmp_path, caps).data_rows == 1
    bad = row()
    bad[HEADERS.index(column)] = "x" * 41
    assert refusal(tmp_path, csv_bytes([bad]), caps).code == "field_too_long"


def test_a_field_larger_than_the_csv_modules_own_limit_is_still_field_too_long(
    tmp_path: Path,
) -> None:  # [NEG]
    giant = row(full_name="x" * 200_000)
    assert refusal(tmp_path, csv_bytes([giant])).code == "field_too_long"


def test_the_default_caps_are_the_adr_values() -> None:
    assert Caps(10 * 1024 * 1024, 100_000, 4_096) == DEFAULT_CAPS


# ----- header ----------------------------------------------------------------------------------


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_a_missing_required_column_is_refused_and_named(  # [NEG]
    tmp_path: Path, column: str
) -> None:
    keep = [h for h in HEADERS if h != column]
    data = csv_text([[c for h, c in zip(HEADERS, row(), strict=True) if h != column]], keep)
    error = refusal(tmp_path, data.encode("utf-8"))
    assert (error.code, error.column) == ("missing_column", column)


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_a_repeated_required_column_is_refused_and_named(  # [NEG]
    tmp_path: Path, column: str
) -> None:
    headers = [*HEADERS, column]
    data = csv_text([[*row(), "extra"]], headers).encode("utf-8")
    error = refusal(tmp_path, data)
    assert (error.code, error.column) == ("duplicate_column", column)


def test_an_empty_file_is_refused_and_a_header_only_file_has_no_rows(tmp_path: Path) -> None:
    assert refusal(tmp_path, b"").code == "missing_column"
    found = inspect_export(write(tmp_path, csv_bytes([])), tmp_path)
    assert (found.data_rows, found.malformed_rows, found.statuses) == (0, 0, [])


def test_the_error_message_carries_a_fixed_word_only(tmp_path: Path) -> None:  # [NEG]
    cells = row(login=CANARY)
    cells[HEADERS.index("user.fullName")] = CANARY
    error = refusal(tmp_path, csv_bytes([cells]) + b"\x80")
    assert CANARY not in str(error) and CANARY not in repr(error)
    assert error.__cause__ is None and error.__context__ is None


# ----- the hash covers the bytes parsed ----------------------------------------------------------


def test_the_sha256_equals_hashlib_over_the_file_bytes(tmp_path: Path) -> None:
    rows = [row(f"user{i}@example.test", full_name="x" * 100) for i in range(1500)]
    data = csv_bytes(rows)
    assert len(data) > 2 * CHUNK_BYTES  # several chunks
    found = inspect_export(write(tmp_path, data), tmp_path)
    assert found.sha256 == hashlib.sha256(data).hexdigest()
    assert found.size_bytes == len(data) and found.data_rows == 1500


def test_the_hash_and_size_are_not_available_before_the_pass_ends(tmp_path: Path) -> None:
    name = write(tmp_path, csv_bytes(SIMPLE))
    with read_export(name, tmp_path) as reader:
        with pytest.raises(RuntimeError):
            _ = reader.sha256
        list(reader.records())
        assert reader.sha256 == hashlib.sha256(csv_bytes(SIMPLE)).hexdigest()
        with pytest.raises(RuntimeError):
            list(reader.records())  # one pass only


# ----- malformed rows are counted, never reshaped -------------------------------------------------


def _malformed_file() -> bytes:
    good = csv_text([row("good1@example.test"), row("good2@example.test", "Suspended")])
    short = ",".join(row(login=f"{CANARY}-short")[:12])  # one cell missing
    long_row = ",".join([*row(login=f"{CANARY}-long"), "extra"])  # one cell too many
    # An unquoted comma inside a name shifts every later cell one place to the right.
    shifted = ",".join(row(login=f"{CANARY}-shift", full_name="Smith, Pat"))
    return (good + "\r\n".join([short, long_row, shifted]) + "\r\n").encode("utf-8")


def test_a_short_a_long_and_a_shifted_row_are_counted_and_left_out(tmp_path: Path) -> None:  # [NEG]
    found = inspect_export(write(tmp_path, _malformed_file()), tmp_path)
    assert found.malformed_rows == 3
    assert found.data_rows == 2
    assert {v.value: v.count for v in found.statuses} == {"Active": 1, "Suspended": 1}
    assert found.duplicate_logins == 0
    assert CANARY not in found.model_dump_json()


def test_a_malformed_row_yields_no_record(tmp_path: Path) -> None:  # [NEG]
    records = records_of(tmp_path, _malformed_file())
    assert [r.login for r in records] == ["good1@example.test", "good2@example.test"]


def test_a_malformed_row_still_counts_toward_the_row_cap(tmp_path: Path) -> None:  # [NEG]
    assert refusal(tmp_path, _malformed_file(), Caps(max_rows=4)).code == "too_many_rows"
    assert inspect_export("export.csv", tmp_path, Caps(max_rows=5)).malformed_rows == 3


# ----- lines are split on "\n" only --------------------------------------------------------------

SPECIALS = [
    "\r\n",
    "\n",
    "\N{LINE SEPARATOR}",
    "\N{PARAGRAPH SEPARATOR}",
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
]


@pytest.mark.parametrize("special", SPECIALS)
def test_a_quoted_field_with_a_line_break_character_is_one_field(
    tmp_path: Path, special: str
) -> None:
    login = f"lead{special}tail"
    data = csv_bytes([row("a@example.test"), row(login)], quote_all=True)
    records = records_of(tmp_path, data)
    assert [r.login for r in records] == ["a@example.test", login]


@pytest.mark.parametrize("special", SPECIALS)
def test_the_same_field_across_a_64_kib_chunk_boundary(tmp_path: Path, special: str) -> None:
    data, login = straddling_export(special)
    assert len(data) > CHUNK_BYTES
    name = write(tmp_path, data)
    with read_export(name, tmp_path) as reader:
        records = list(reader.records())
        assert reader.malformed_rows == 0
        assert reader.sha256 == hashlib.sha256(data).hexdigest()
    assert records[-1].login == login
    assert all(r.login == "f@example.test" for r in records[:-1])


@pytest.mark.parametrize(
    "special", ["\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}", "\x0b", "\x0c", "\x85"]
)
def test_an_unquoted_line_break_character_does_not_start_a_new_row(
    tmp_path: Path, special: str
) -> None:  # [NEG]
    login = f"a{special}b@example.test"
    text = ",".join(row(login)) + "\n"
    data = (",".join(HEADERS) + "\n" + text).encode("utf-8")
    records = records_of(tmp_path, data)
    assert [r.login for r in records] == [login]


# ----- what is kept ------------------------------------------------------------------------------


def test_a_record_keeps_four_fields_and_shows_none_of_them(tmp_path: Path) -> None:  # [NEG]
    cells = row(login=CANARY, types="Okta Verify", admin="true")
    cells[HEADERS.index("user.fullName")] = f"{CANARY}-name"
    cells[HEADERS.index("user.email")] = f"{CANARY}-email"
    cells[HEADERS.index("user.mobilePhone")] = f"{CANARY}-phone"
    (record,) = records_of(tmp_path, csv_bytes([cells]))
    assert (record.login, record.status, record.authenticator_types, record.is_admin) == (
        CANARY,
        "Active",
        "Okta Verify",
        "true",
    )
    assert not hasattr(record, "__dict__")  # slots: no place to hold a fifth value
    assert CANARY not in repr(record) and CANARY not in str(record)
    fields = set(ExportRecord.__dataclass_fields__)
    assert fields == {"login", "status", "authenticator_types", "is_admin"}


def test_a_canary_in_a_dropped_column_or_the_login_is_not_in_the_inspection(
    tmp_path: Path,
) -> None:  # [NEG]
    cells = row(login=f"{CANARY}-login", status="Active", types="Password")
    for column in ("user.fullName", "user.email", "user.mobilePhone", "user.primaryPhone"):
        cells[HEADERS.index(column)] = f"{CANARY}-{column}"
    found = inspect_export(write(tmp_path, csv_bytes([cells, cells])), tmp_path)
    assert found.duplicate_logins == 1
    assert CANARY not in found.model_dump_json()


def test_cells_are_text_and_formulas_are_never_evaluated(tmp_path: Path) -> None:
    formulas = ['=HYPERLINK("http://example.test","x")', "+1+1", "-2+3", "@SUM(A1:A2)"]
    rows = [row(login=f, types=f, admin=f) for f in formulas]
    records = records_of(tmp_path, csv_bytes(rows))
    assert [r.login for r in records] == formulas
    assert [r.authenticator_types for r in records] == formulas


def test_duplicate_logins_are_counted_after_trimming(tmp_path: Path) -> None:
    rows = [row("a@example.test"), row("a@example.test"), row(" a@example.test "), row("b@x.test")]
    assert inspect_export(write(tmp_path, csv_bytes(rows)), tmp_path).duplicate_logins == 2


def test_types_are_split_on_commas_trimmed_and_counted_once_per_user(tmp_path: Path) -> None:
    rows = [
        row(types="Password, Email ,Okta Verify"),
        row(types="Password,Password,,Phone"),
        row(types=""),
    ]
    found = inspect_export(write(tmp_path, csv_bytes(rows)), tmp_path)
    assert {v.value: v.count for v in found.authenticator_types} == {
        "Password": 2,
        "Email": 1,
        "Okta Verify": 1,
        "Phone": 1,
    }


def test_untrusted_values_are_shortened_and_the_list_is_bounded(tmp_path: Path) -> None:  # [NEG]
    rows = [row(status="S" * (MAX_VALUE_CHARS + 30))]
    rows += [row(status=f"status-{i}") for i in range(MAX_LISTED_VALUES + 5)]
    found = inspect_export(write(tmp_path, csv_bytes(rows)), tmp_path)
    assert len(found.statuses) == MAX_LISTED_VALUES
    assert found.statuses_not_listed == 6
    assert all(len(v.value) <= MAX_VALUE_CHARS for v in found.statuses)
    long_one = [v for v in found.statuses if v.value.startswith("S" * 10)]
    assert long_one and long_one[0].truncated


# ----- the synthetic fixture ---------------------------------------------------------------------


def test_the_fixture_inspects_to_the_counts_worked_out_from_the_file(tmp_path: Path) -> None:
    write(tmp_path, FIXTURE.read_bytes(), "okta.csv")
    found = inspect_export("okta.csv", tmp_path)
    assert as_counts(found.model_dump()) == expected_counts(FIXTURE)
    assert found.data_rows == 40 and found.duplicate_logins == 1
    types = {v.value for v in found.authenticator_types}
    assert "Okta Verify" in types and "Phone" in types
    assert not any("key" in t.lower() for t in types)  # no security key anywhere in this file
    assert CANARY not in found.model_dump_json()
    assert "@example.test" not in found.model_dump_json()
