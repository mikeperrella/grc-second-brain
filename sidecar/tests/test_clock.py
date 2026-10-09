from datetime import date

from grc_sidecar.vault.clock import FixedClock, SystemClock, is_stale


def test_fixed_clock_always_returns_its_date() -> None:
    clock = FixedClock(date(2026, 1, 1))
    assert clock.today() == date(2026, 1, 1)
    assert clock.today() == date(2026, 1, 1)


def test_system_clock_returns_a_date() -> None:
    assert type(SystemClock().today()) is date


def test_a_review_date_in_the_past_is_stale() -> None:
    assert is_stale(date(2024, 6, 30), date(2026, 1, 1))


def test_a_review_date_today_or_in_the_future_is_not_stale() -> None:
    assert not is_stale(date(2026, 1, 1), date(2026, 1, 1))  # the review is due today
    assert not is_stale(date(2026, 1, 2), date(2026, 1, 1))


def test_a_missing_review_date_is_never_stale() -> None:
    assert not is_stale(None, date(2099, 1, 1))
