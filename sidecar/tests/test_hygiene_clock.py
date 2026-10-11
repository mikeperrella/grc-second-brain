"""No module in src/ may read the system date or the monotonic timer except the clock module.

Two scans. `clock_calls`: date.today(), datetime.today(), datetime.now() and datetime.utcnow(),
however the datetime module or its classes were imported or renamed. `timer_calls` (added in M4a,
ADR-011): time.monotonic() and time.monotonic_ns(), however imported. time.time() and
time.sleep() are deliberately not covered.
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


TIMER_FUNCTIONS = {"monotonic", "monotonic_ns"}


def timer_calls(source: str) -> list[int]:
    """Line numbers of calls to time.monotonic() / time.monotonic_ns(), however imported.

    The monotonic timer is read through `Clock.monotonic()` so interval logic (the model gate's
    minimum re-check interval) can be tested by advancing a fake clock instead of sleeping.
    """
    tree = ast.parse(source)
    module_names: set[str] = set()  # names bound to the time module
    function_names: set[str] = set()  # names bound to time.monotonic / time.monotonic_ns
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names |= {a.asname or a.name for a in node.names if a.name == "time"}
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            function_names |= {a.asname or a.name for a in node.names if a.name in TIMER_FUNCTIONS}
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        bare_name = isinstance(func, ast.Name) and func.id in function_names
        on_time_module = (
            isinstance(func, ast.Attribute)
            and func.attr in TIMER_FUNCTIONS
            and isinstance(func.value, ast.Name)
            and func.value.id in module_names
        )
        if bare_name or on_time_module:
            lines.append(node.lineno)
    return sorted(lines)


def test_the_timer_scanner_finds_every_way_of_reading_the_timer() -> None:  # [NEG]
    samples = {
        "import time\ntime.monotonic()": [2],
        "import time as t\nt.monotonic_ns()": [2],
        "from time import monotonic\nmonotonic()": [2],
        "from time import monotonic as now\nnow()": [2],
    }
    for source, expected in samples.items():
        assert timer_calls(source) == expected, source


def test_the_timer_scanner_ignores_code_that_does_not_read_the_timer() -> None:
    harmless = [
        "import time\ntime.sleep(1)",
        "class C:\n    def monotonic(self): ...\nC().monotonic()",
        "from time import sleep\nsleep(1)",
    ]
    for source in harmless:
        assert timer_calls(source) == [], source


def test_no_direct_timer_calls() -> None:  # [NEG]
    offenders = {
        str(path.relative_to(SRC).as_posix()): lines
        for path in sorted(SRC.rglob("*.py"))
        if (lines := timer_calls(path.read_text(encoding="utf-8")))
        and path.relative_to(SRC).as_posix() not in ALLOWED
    }
    assert offenders == {}


def test_the_clock_module_still_reads_the_timer() -> None:
    assert timer_calls((SRC / "vault/clock.py").read_text(encoding="utf-8"))


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
