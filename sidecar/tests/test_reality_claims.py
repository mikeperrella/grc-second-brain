"""The claim finder (ADR-016 decision 10): code, not the model. Every match listed, none hidden,
read-only, and a submitted claim is accepted only if it equals a current candidate."""

import hashlib
import os
from pathlib import Path

import pytest

from grc_sidecar.errors import CheckRefusedError
from grc_sidecar.reality.claims import (
    MAX_LISTED_CANDIDATES,
    MAX_QUOTE_CHARS,
    UNMAPPED_NOTE,
    find_claims,
    verify_claim,
)

from .realitykit import write_policy_vault
from .vaultkit import DEMO_VAULT, doc_text

DEMO_POLICY = DEMO_VAULT / "access-control-policy.md"


def candidates(vault: Path) -> list[tuple[int, int, str]]:
    return [(c.start_line, c.end_line, c.quote) for c in find_claims(vault).candidates]


def write_doc(root: Path, name: str, body: str, *, doc_type: str = "policy", n: int = 1) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = (f"doc_id: D-{n}", f"type: {doc_type}", "owner: O", "review_by: 2030-01-01")
    (root / name).write_text(doc_text(body, *lines), encoding="utf-8")


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ----- the hard test -----------------------------------------------------------------------------


def test_the_demo_sentence_is_found() -> None:
    report = find_claims(DEMO_VAULT)
    found = [c for c in report.candidates if c.path == "access-control-policy.md"]
    assert len(found) == 1
    claim = found[0]
    assert (claim.start_line, claim.end_line) == (23, 23)
    assert claim.quote.startswith("Multi-factor authentication (MFA) is required for all staff")
    assert claim.mapped_to_authentication_control is True
    assert "NIST-800-53:IA-2" in claim.matched_control_ids
    # The quote really is what the file says on that line (whitespace aside).
    file_line = DEMO_POLICY.read_text(encoding="utf-8").split("\n")[22]
    assert claim.quote in file_line
    # Shown, not interpreted: the scope words are named, and nothing is dropped for having them.
    assert {"staff", "remote"} <= set(claim.qualifier_words)
    # The administrators' hardware-key sentence is a later claim and is not a candidate.
    assert not any("hardware security key" in c.quote for c in report.candidates)
    assert report.too_long == 0 and report.candidates_not_listed == 0


def test_the_finder_changes_nothing_in_the_vault() -> None:  # [NEG]
    before = tree_hashes(DEMO_VAULT)
    find_claims(DEMO_VAULT)
    assert tree_hashes(DEMO_VAULT) == before


# ----- what matches ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "MFA is required for everyone.",
        "mfa MUST be enabled.",
        "Two-factor authentication shall be used.",
        "Everyone must have 2FA at sign-in.",
        "Multifactor sign-in is required.",
        "multi-factor authentication is required.",
        "Staff require MFA on all laptops.",
    ],
)
def test_a_sentence_with_both_term_kinds_is_a_candidate(tmp_path: Path, sentence: str) -> None:
    write_policy_vault(tmp_path / "v", sentence)
    assert [q for _, _, q in candidates(tmp_path / "v")] == [sentence]


@pytest.mark.parametrize(
    "sentence",
    [
        "MFA is a good idea.",  # no requirement term
        "Passwords must be long.",  # no MFA term
        "MFAs are required.",  # not the whole word
        "The demanded form is required for 2FAX.",  # substrings only
        "Staff enroll in [[mfa-enrollment-procedure|MFA enrollment]].",
        "Requirements for MFA are described elsewhere.",  # "Requirements" is not a listed term
    ],
)
def test_a_sentence_missing_a_term_kind_is_not_a_candidate(  # [NEG]
    tmp_path: Path, sentence: str
) -> None:
    write_policy_vault(tmp_path / "v", sentence)
    assert candidates(tmp_path / "v") == []


def test_a_framework_document_is_never_a_claim(tmp_path: Path) -> None:  # [NEG]
    write_doc(tmp_path, "fw.md", "MFA is required by the framework.\n", doc_type="framework")
    report = find_claims(tmp_path)
    assert report.candidates == ()
    assert report.excluded_framework == 1 and report.scanned_documents == 1


def test_a_document_mapped_to_no_authentication_control_is_listed_with_a_note(
    tmp_path: Path,
) -> None:
    write_policy_vault(tmp_path / "v", mappings='["NIST-800-53:AC-2"]')
    (claim,) = find_claims(tmp_path / "v").candidates
    assert claim.mapped_to_authentication_control is False
    assert claim.control_note == UNMAPPED_NOTE
    assert claim.matched_control_ids == ()


def test_mapped_claims_are_ranked_first_but_nothing_is_hidden(tmp_path: Path) -> None:
    write_doc(tmp_path, "a.md", "MFA is required here.\n", n=1)  # no mappings
    (tmp_path / "b.md").write_text(
        doc_text(
            "MFA is required there.\n",
            "doc_id: D-2",
            "type: policy",
            "owner: O",
            "review_by: 2030-01-01",
            'control_mappings: ["NIST-800-53:IA-2"]',
        ),
        encoding="utf-8",
    )
    paths = [(c.path, c.mapped_to_authentication_control) for c in find_claims(tmp_path).candidates]
    assert paths == [("b.md", True), ("a.md", False)]


# ----- lines and shapes --------------------------------------------------------------------------


def test_a_sentence_across_lines_names_its_first_and_last_line(tmp_path: Path) -> None:
    write_doc(tmp_path, "p.md", "Intro.\n\nAll staff\nmust use MFA\nat all times. Next one.\n")
    ((start, end, quote),) = candidates(tmp_path)
    assert quote == "All staff must use MFA at all times."
    assert end - start == 2
    assert (tmp_path / "p.md").read_text(encoding="utf-8").split("\n")[start - 1] == "All staff"


def test_two_matching_sentences_on_one_line_are_two_candidates(tmp_path: Path) -> None:
    write_doc(tmp_path, "p.md", "MFA is required. Two-factor must stay on.\n")
    found = candidates(tmp_path)
    assert [q for _, _, q in found] == ["MFA is required.", "Two-factor must stay on."]
    assert found[0][:2] == found[1][:2]


def test_a_list_item_is_its_own_unit_without_its_marker(tmp_path: Path) -> None:
    write_doc(tmp_path, "p.md", "- MFA is required for admins.\n- Passwords are long.\n")
    ((start, end, quote),) = candidates(tmp_path)
    assert start == end and quote == "MFA is required for admins."
    assert (
        (tmp_path / "p.md").read_text(encoding="utf-8").split("\n")[start - 1].startswith("- MFA")
    )


def test_a_table_row_is_one_unit(tmp_path: Path) -> None:
    write_doc(tmp_path, "p.md", "| Control | Rule |\n| MFA | Required. Always. |\n")
    ((_, _, quote),) = candidates(tmp_path)
    assert "| MFA |" in quote and quote.endswith("Always. |")


def test_e_g_does_not_cut_a_sentence(tmp_path: Path) -> None:
    write_doc(tmp_path, "p.md", "MFA is required, e.g. for staff accounts.\n")
    assert [q for _, _, q in candidates(tmp_path)] == ["MFA is required, e.g. for staff accounts."]


def test_fenced_code_is_skipped(tmp_path: Path) -> None:  # [NEG]
    write_doc(tmp_path, "p.md", "```\nMFA is required in this snippet.\n```\n")
    assert candidates(tmp_path) == []


def test_the_frontmatter_is_not_searched(tmp_path: Path) -> None:  # [NEG]
    (tmp_path / "p.md").write_text(
        doc_text(
            "Nothing here.\n",
            "doc_id: D-1",
            "type: policy",
            "owner: MFA is required owner",
            "review_by: 2030-01-01",
        ),
        encoding="utf-8",
    )
    assert candidates(tmp_path) == []


# ----- caps -----


def test_a_sentence_over_the_length_cap_is_counted_not_listed(tmp_path: Path) -> None:
    long_sentence = "MFA is required " + "word " * (MAX_QUOTE_CHARS // 5) + "end."
    write_doc(tmp_path, "p.md", f"{long_sentence}\n\nMFA is required here.\n")
    report = find_claims(tmp_path)
    assert report.too_long == 1
    assert [c.quote for c in report.candidates] == ["MFA is required here."]


def test_the_candidate_list_is_capped_and_the_rest_counted(tmp_path: Path) -> None:
    for n in range(MAX_LISTED_CANDIDATES + 1):
        write_doc(tmp_path, f"d{n:03}.md", "MFA is required.\n", n=n)
    report = find_claims(tmp_path)
    assert len(report.candidates) == MAX_LISTED_CANDIDATES
    assert report.candidates_not_listed == 1


# ----- verify_claim: only an exact current candidate is accepted --------------------------------


def claim_of(vault: Path) -> dict[str, object]:
    (candidate,) = find_claims(vault).candidates
    return {
        "path": candidate.path,
        "start_line": candidate.start_line,
        "end_line": candidate.end_line,
        "quote": candidate.quote,
    }


def verify(vault: Path, claim: dict[str, object]) -> object:
    return verify_claim(
        vault,
        path=str(claim["path"]),
        start_line=int(str(claim["start_line"])),
        end_line=int(str(claim["end_line"])),
        quote=str(claim["quote"]),
    )


def test_the_exact_candidate_is_accepted_and_whitespace_is_the_only_leniency(
    tmp_path: Path,
) -> None:
    write_policy_vault(tmp_path / "v")
    claim = claim_of(tmp_path / "v")
    assert verify(tmp_path / "v", claim) is not None
    spaced = {**claim, "quote": "  " + str(claim["quote"]).replace(" ", "  \n ") + " "}
    assert verify(tmp_path / "v", spaced) is not None


def test_a_fragment_of_a_candidate_is_refused(tmp_path: Path) -> None:  # [NEG]
    vault = tmp_path / "v"
    write_policy_vault(vault, "MFA is required for all staff, except contractors.")
    claim = claim_of(vault)
    fragment = {**claim, "quote": "MFA is required for all staff"}  # qualifier words cut off
    with pytest.raises(CheckRefusedError) as raised:
        verify(vault, fragment)
    assert raised.value.code == "claim_not_verbatim"


def test_a_quote_with_extra_text_or_a_changed_letter_case_is_refused(  # [NEG]
    tmp_path: Path,
) -> None:
    write_policy_vault(tmp_path / "v")
    claim = claim_of(tmp_path / "v")
    for quote in (f"{claim['quote']} Always.", str(claim["quote"]).lower()):
        with pytest.raises(CheckRefusedError):
            verify(tmp_path / "v", {**claim, "quote": quote})


@pytest.mark.parametrize("field", ["start_line", "end_line"])
def test_moved_lines_are_refused(tmp_path: Path, field: str) -> None:  # [NEG]
    write_policy_vault(tmp_path / "v")
    claim = claim_of(tmp_path / "v")
    with pytest.raises(CheckRefusedError):
        verify(tmp_path / "v", {**claim, field: int(str(claim[field])) + 1})


def test_a_claim_whose_text_changed_in_the_vault_is_refused(tmp_path: Path) -> None:  # [NEG]
    write_policy_vault(tmp_path / "v")
    claim = claim_of(tmp_path / "v")
    policy = tmp_path / "v" / "policy.md"
    policy.write_text(policy.read_text(encoding="utf-8").replace("is required", "is advised"))
    with pytest.raises(CheckRefusedError):
        verify(tmp_path / "v", claim)


def test_a_claim_whose_document_became_framework_or_vanished_is_refused(  # [NEG]
    tmp_path: Path,
) -> None:
    write_policy_vault(tmp_path / "v")
    claim = claim_of(tmp_path / "v")
    policy = tmp_path / "v" / "policy.md"
    original = policy.read_text(encoding="utf-8")
    policy.write_text(original.replace("type: policy", "type: framework"), encoding="utf-8")
    with pytest.raises(CheckRefusedError):
        verify(tmp_path / "v", claim)
    policy.unlink()
    write_doc(tmp_path / "v", "other.md", "Nothing.\n")
    with pytest.raises(CheckRefusedError):
        verify(tmp_path / "v", claim)


def test_a_path_outside_the_vault_is_refused_even_if_the_file_holds_the_claim(  # [NEG]
    tmp_path: Path,
) -> None:
    write_policy_vault(tmp_path / "v")
    write_policy_vault(tmp_path / "outside")  # the same sentence, in a folder the vault excludes
    claim = claim_of(tmp_path / "v")
    for path in (
        "../outside/policy.md",
        str(tmp_path / "outside" / "policy.md"),
        "..\\outside\\policy.md",
        "",
    ):
        with pytest.raises(CheckRefusedError) as raised:
            verify(tmp_path / "v", {**claim, "path": path})
        assert raised.value.code == "claim_not_verbatim"


def test_a_path_through_a_symlink_is_refused(tmp_path: Path) -> None:  # [NEG]
    write_policy_vault(tmp_path / "outside")
    write_policy_vault(tmp_path / "v")
    claim = claim_of(tmp_path / "v")
    link = tmp_path / "v" / "linked.md"
    try:
        os.symlink(tmp_path / "outside" / "policy.md", link)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symlinks (Windows needs developer mode)")
    with pytest.raises(CheckRefusedError):
        verify(tmp_path / "v", {**claim, "path": "linked.md"})
    # The scanner refuses the link, so it is not even a candidate.
    assert all(c.path != "linked.md" for c in find_claims(tmp_path / "v").candidates)
