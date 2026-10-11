"""A clock tests can advance (tests only): lets interval logic run without sleeping."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class ManualClock:
    """`today()` is fixed; `monotonic()` moves only when the test calls `advance`."""

    fixed: date = date(2026, 10, 10)
    now: float = field(default=1000.0)  # arbitrary start, like a real monotonic timer

    def today(self) -> date:
        return self.fixed

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
