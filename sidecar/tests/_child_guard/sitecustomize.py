"""Installs the egress guard inside child processes started by the tests.

Python imports `sitecustomize` at interpreter start-up. The tests put this directory
on the child's PYTHONPATH, so the guard (tests/egress_guard.py) is active in the real
sidecar process too, not just in the pytest process. The module is loaded by file
path so it does not depend on the `tests` package being importable.
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "grc_test_egress_guard", Path(__file__).resolve().parents[1] / "egress_guard.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError("egress guard module could not be located")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_module.install()
