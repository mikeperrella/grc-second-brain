"""Shared helpers for the vault tests: repo paths and small document builders."""

from pathlib import Path

from grc_sidecar.vault.frontmatter import parse_document
from grc_sidecar.vault.models import ParsedDocument

# tests/ -> sidecar/ -> repo root. In the air-gap image the tests live in /app/tests, so this is
# "/", and the CI job mounts vault-sample and fixtures there (see .github/workflows/airgap.yml).
REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_VAULT = REPO_ROOT / "vault-sample" / "vault"
SAMPLE_README = REPO_ROOT / "vault-sample" / "README.md"
ADVERSARIAL_VAULT = REPO_ROOT / "fixtures" / "vault-adversarial"

DEFAULT_FRONTMATTER = (
    "doc_id: T-001",
    "type: policy",
    "owner: Test Owner",
    "review_by: 2028-01-01",
)


def doc_text(body: str = "", *frontmatter_lines: str) -> str:
    """A whole file: `---`, the frontmatter lines, `---`, then the body.

    With no frontmatter lines given, a valid default block is used. The body starts on line
    len(frontmatter_lines) + 3 (the two fences, plus one).
    """
    lines = frontmatter_lines or DEFAULT_FRONTMATTER
    return "\n".join(["---", *lines, "---", body])


def make_doc(body: str = "", *frontmatter_lines: str, path: str = "note.md") -> ParsedDocument:
    return parse_document(doc_text(body, *frontmatter_lines), path)


# Where the first body line lands when the default frontmatter is used.
DEFAULT_BODY_START = len(DEFAULT_FRONTMATTER) + 3
