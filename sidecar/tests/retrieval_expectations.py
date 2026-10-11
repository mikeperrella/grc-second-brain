"""What the sample vault must return for a few questions: ONE table, shared (tests only).

The golden tests (`test_retrieval_golden.py`, fake embedder) and the live Ollama check
(`test_retrieval_live.py`, real models) both import this, so they cannot drift apart: they refer to
the same documents for the same questions.

The synthetic company is "Halcyon Ridge Software, Inc." (vault-sample/). Document map:

  HR-POL-001  access-control-and-mfa-policy.md
  HR-POL-002  encryption-policy.md
  HR-POL-003  incident-response-plan.md        (security logs kept 365 days)
  HR-POL-004  vendor-management-policy.md      (review_by 2024-06-30: overdue)
  HR-POL-005  data-retention-policy.md         (security logs kept 90 days)
  HR-PRC-001  mfa-enrollment-procedure.md
"""

from dataclasses import dataclass

DOC_FILES: dict[str, str] = {
    "HR-POL-001": "access-control-and-mfa-policy.md",
    "HR-POL-002": "encryption-policy.md",
    "HR-POL-003": "incident-response-plan.md",
    "HR-POL-004": "vendor-management-policy.md",
    "HR-POL-005": "data-retention-policy.md",
    "HR-PRC-001": "mfa-enrollment-procedure.md",
}

# question -> the documents (by doc_id) its result must include. Membership, not rank: how the
# real model ranks them is exactly what the live check measures.
QUESTIONS: dict[str, tuple[str, ...]] = {
    "multi-factor authentication": ("HR-POL-001",),
    "log retention": ("HR-POL-005", "HR-POL-003"),  # the two sides of 90 versus 365 days
    "vendor review": ("HR-POL-004",),
}

# doc_id -> expected `review_overdue` on the date the golden run fixes (2026-10-10)
OVERDUE: dict[str, bool] = {"HR-POL-004": True}

# The question that exercises expansion: two retention chunks, two framework chunks and a link to
# the vendor policy (data-retention-policy.md line 30 and its frontmatter).
EXPANSION_QUESTION = "retention schedule deleted expired data audit record retention"


@dataclass(frozen=True)
class IdentifierGroup:
    """Written forms of ONE control, and how to recognise the control's chunk in a result."""

    label: str
    forms: tuple[str, ...]
    file_name: str  # the pack file the control lives in
    marker: str  # appears in the control's heading or text


IDENTIFIER_GROUPS: tuple[IdentifierGroup, ...] = (
    IdentifierGroup("800-171 r3", ("3.1.1", "03.01.01"), "03.01.md", "03.01.01 Account Management"),
    IdentifierGroup("800-53", ("AC-2", "AC-02", "ac-2"), "ac.md", "AC-2 Account Management"),
    IdentifierGroup("800-53 enh.", ("AC-2(1)",), "ac.md", "AC-2(1) Automated System Account"),
    IdentifierGroup("CSF 2.0", ("PR.AA-01", "PR.AA-1"), "pr.md", "PR.AA-01"),
    IdentifierGroup("SOC 2", ("CC6.1", "SOC2:CC6.1"), "cc.md", "**CC6.1**"),
    IdentifierGroup("ISO 27001", ("5.15", "A.5.15"), "a5-organizational.md", "**A.5.15**"),
)
