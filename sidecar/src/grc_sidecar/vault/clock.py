"""The only place in the sidecar that reads the system date or the monotonic timer.

Stale-document logic ("is review_by in the past?") and "has enough time passed since the last
check?" must be testable without depending on the day or the speed of the machine. So code asks a
Clock, tests pass a FixedClock (or a test-only clock they can advance), and a hygiene test
(test_hygiene_clock.py) fails if any other module in src/ calls date.today(), datetime.now() or
time.monotonic().
"""

import time
from dataclasses import dataclass
from datetime import date
from typing import Protocol


class Clock(Protocol):
    def today(self) -> date: ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary start that never goes backwards. Only differences mean
        anything; used to measure intervals."""


class SystemClock:
    def today(self) -> date:
        return date.today()

    def monotonic(self) -> float:
        return time.monotonic()


@dataclass(frozen=True)
class FixedClock:
    fixed: date

    def today(self) -> date:
        return self.fixed

    def monotonic(self) -> float:
        return 0.0  # time never passes on a fixed clock


def is_stale(review_by: date | None, today: date) -> bool:
    """A document is stale when its review date has passed. No review date means not stale."""
    return review_by is not None and review_by < today
