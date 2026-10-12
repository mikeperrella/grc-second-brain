"""Only the generation client names the Ollama chat endpoint (ADR-012).

The runtime control is the client's request hook; this is the second line, a text scan, like the
Semgrep rule `grc-py-chat-endpoint-outside-generation-client` (which CI runs). The scan is itself
tested: a checker that finds nothing in the real tree proves little unless it can find something.
"""

import re
from pathlib import Path

from grc_sidecar.llm import generation_client

SRC = Path(generation_client.__file__).resolve().parents[1]
CHAT = re.compile(r"(?i)api/+(\./+)*chat\b")
ALLOWED = Path("llm") / "generation_client.py"


def files_naming_the_chat_endpoint(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*.py")
        if CHAT.search(path.read_text(encoding="utf-8"))
    }


def test_only_the_generation_client_names_the_chat_endpoint() -> None:
    assert files_naming_the_chat_endpoint(SRC) == {ALLOWED}


def test_the_scan_can_find_an_offender_in_every_spelling(tmp_path: Path) -> None:  # [NEG]
    spellings = ['"/api/chat"', '"api/chat"', '"/API/Chat"', '"/api//chat"', '"/api/./chat"']
    for number, spelling in enumerate(spellings):
        (tmp_path / f"offender_{number}.py").write_text(f"url = {spelling}\n", encoding="utf-8")
    (tmp_path / "innocent.py").write_text(
        'url = "/api/tags"  # and /api/chatter\n', encoding="utf-8"
    )
    found = {path.name for path in files_naming_the_chat_endpoint(tmp_path)}
    assert found == {f"offender_{n}.py" for n in range(len(spellings))}


def test_the_allowed_module_really_does_name_it() -> None:
    """If the client stopped naming the endpoint, the first test would pass for the wrong reason."""
    assert CHAT.search((SRC / ALLOWED).read_text(encoding="utf-8")) is not None
