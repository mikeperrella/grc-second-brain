"""No module in src/ may read the system date except the clock module (so tests never depend on it).

Scope, deliberately narrow: calls to date.today(), datetime.today(), datetime.now() and
datetime.utcnow(), however the datetime module or its classes were imported or renamed.
time.time() and time.monotonic() are not covered: they measure durations, not calendar dates.
The `%(asctime)s` field in logging_config.py is a logging format string, not a call, so it needs
no entry. Any future legitimate use must be added to ALLOWED below with a reason, which makes it
visible in review instead of silently excluded.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "grc_sidecar"
CLOCK_METHODS = {"today", "now", "utcnow"}
CLOCK_CLASSES = {"date", "datetime"}

# path relative to src/grc_sidecar -> why it may read the clock
ALLOWED = {"vault/clock.py": "the only place that reads the system date"}


def clock_calls(source: str) -> list[int]:
    """Line numbers of calls that read the current date or time from the datetime module."""
    tree = ast.parse(source)
    module_names: set[str] = set()  # names bound to the datetime module itself
    class_names: set[str] = set()  # names bound to datetime.date / datetime.datetime
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names |= {a.asname or a.name for a in node.names if a.name == "datetime"}
        elif isinstance(node, ast.ImportFrom) and node.module == "datetime":
            class_names |= {a.asname or a.name for a in node.names if a.name in CLOCK_CLASSES}
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in CLOCK_METHODS:
            continue
        base = node.func.value
        on_imported_class = isinstance(base, ast.Name) and base.id in class_names
        on_module_attribute = (
            isinstance(base, ast.Attribute)
            and base.attr in CLOCK_CLASSES
            and isinstance(base.value, ast.Name)
            and base.value.id in module_names
        )
        if on_imported_class or on_module_attribute:
            lines.append(node.lineno)
    return sorted(lines)


def test_the_scanner_finds_every_way_of_reading_the_clock() -> None:  # [NEG]
    samples = {
        "from datetime import date\ndate.today()": [2],
        "from datetime import datetime\ndatetime.now()": [2],
        "from datetime import datetime as dt\ndt.utcnow()": [2],
        "import datetime\ndatetime.date.today()": [2],
        "import datetime\ndatetime.datetime.now()": [2],
        "import datetime as d\nd.datetime.today()": [2],
    }
    for source, expected in samples.items():
        assert clock_calls(source) == expected, source


def test_the_scanner_ignores_code_that_does_not_read_the_clock() -> None:
    harmless = [
        "from datetime import date\ndate(2026, 1, 1)",
        "from datetime import date\ndate.fromisoformat('2026-01-01')",
        "import time\ntime.monotonic()",
        "class C:\n    def today(self): ...\nC().today()",
        "x = '%(asctime)s'",
    ]
    for source in harmless:
        assert clock_calls(source) == [], source


def test_no_direct_clock_calls() -> None:  # [NEG]
    offenders = {
        str(path.relative_to(SRC).as_posix()): lines
        for path in sorted(SRC.rglob("*.py"))
        if (lines := clock_calls(path.read_text(encoding="utf-8")))
        and path.relative_to(SRC).as_posix() not in ALLOWED
    }
    assert offenders == {}


def test_every_allowlist_entry_is_still_needed() -> None:
    for relative in ALLOWED:
        assert clock_calls((SRC / relative).read_text(encoding="utf-8")), relative
