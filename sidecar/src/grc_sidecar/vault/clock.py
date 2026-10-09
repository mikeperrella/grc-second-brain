"""The only place in the sidecar that reads the system date.

Stale-document logic ("is review_by in the past?") must be testable without depending on the day
the tests run. So code asks a Clock for today's date, tests pass a FixedClock, and a hygiene
test (test_hygiene_clock.py) fails if any other module in src/ calls date.today() or
datetime.now().
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


class Clock(Protocol):
    def today(self) -> date: ...


class SystemClock:
    def today(self) -> date:
        return date.today()


@dataclass(frozen=True)
class FixedClock:
    fixed: date

    def today(self) -> date:
        return self.fixed


def is_stale(review_by: date | None, today: date) -> bool:
    """A document is stale when its review date has passed. No review date means not stale."""
    return review_by is not None and review_by < today
