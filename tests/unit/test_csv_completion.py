from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from semiedge.data.csv_import import CsvImportError, parse_csv_bars

pytestmark = pytest.mark.safety
HEADER = "session,open,high,low,close,volume\n"


def csv(*sessions: str, volume: str = "100") -> str:
    return HEADER + "".join(f"{session},10,12,9,11,{volume}\n" for session in sessions)


@pytest.mark.parametrize(
    ("session", "clock", "accepted"),
    [
        ("2024-01-02", datetime(2024, 1, 2, 20, 59, tzinfo=UTC), False),
        ("2024-01-02", datetime(2024, 1, 2, 21, 0, tzinfo=UTC), False),
        ("2024-01-02", datetime(2024, 1, 2, 21, 29, 59, tzinfo=UTC), False),
        ("2024-01-02", datetime(2024, 1, 2, 21, 30, tzinfo=UTC), True),
        ("2024-07-03", datetime(2024, 7, 3, 17, 29, 59, tzinfo=UTC), False),
        ("2024-07-03", datetime(2024, 7, 3, 17, 30, tzinfo=UTC), True),
        ("2024-03-11", datetime(2024, 3, 11, 20, 30, tzinfo=UTC), True),
    ],
)
def test_close_settle_early_close_and_dst(session: str, clock: datetime, accepted: bool) -> None:
    if accepted:
        assert (
            len(
                parse_csv_bars(
                    csv(session), symbol="NVDA", as_of=date.fromisoformat(session), now=clock
                )
            )
            == 1
        )
    else:
        with pytest.raises(CsvImportError, match="closed and settled"):
            parse_csv_bars(
                csv(session), symbol="NVDA", as_of=date.fromisoformat(session), now=clock
            )


@pytest.mark.parametrize("cutoff", [date(2024, 1, 6), date(2024, 1, 1), date(2099, 12, 31)])
def test_weekend_holiday_and_future_cutoff_rejected(cutoff: date) -> None:
    with pytest.raises(CsvImportError):
        parse_csv_bars(
            csv("2024-01-02"), symbol="NVDA", as_of=cutoff, now=datetime(2024, 1, 8, 22, tzinfo=UTC)
        )


@pytest.mark.parametrize("later_session", ["2024-01-03", "2031-03-03"])
def test_future_row_after_historical_cutoff_is_rejected(later_session: str) -> None:
    with pytest.raises(CsvImportError):
        parse_csv_bars(
            csv("2024-01-02", later_session),
            symbol="NVDA",
            as_of=date(2024, 1, 2),
            now=datetime(2024, 1, 3, 15, tzinfo=UTC),
        )


def test_offset_clock_and_zero_settle() -> None:
    frame = parse_csv_bars(
        csv("2024-01-02"),
        symbol="NVDA",
        as_of=date(2024, 1, 2),
        now=datetime(2024, 1, 2, 15, tzinfo=timezone(timedelta(hours=-6))),
        settle_minutes=0,
    )
    assert len(frame) == 1


@pytest.mark.parametrize("settle", [-1, 241, True, 0.5])
def test_invalid_settle_rejected(settle: int) -> None:
    with pytest.raises(CsvImportError, match="settle_minutes"):
        parse_csv_bars(
            csv("2024-01-02"),
            symbol="NVDA",
            as_of=date(2024, 1, 2),
            now=datetime(2024, 1, 3, tzinfo=UTC),
            settle_minutes=settle,
        )


def test_naive_clock_rejected() -> None:
    naive = datetime(2024, 1, 3)  # noqa: DTZ001 -- invalid input regression
    with pytest.raises(CsvImportError, match="timezone-aware"):
        parse_csv_bars(csv("2024-01-02"), symbol="NVDA", as_of=date(2024, 1, 2), now=naive)


@pytest.mark.parametrize(
    "volume", ["100.5", "-0", "-0.0", "100.000000000000000001", "9007199254740993"]
)
def test_volume_requires_exact_nonnegative_integer(volume: str) -> None:
    with pytest.raises(CsvImportError, match="integer"):
        parse_csv_bars(
            csv("2024-01-02", volume=volume),
            symbol="NVDA",
            as_of=date(2024, 1, 2),
            now=datetime(2024, 1, 3, tzinfo=UTC),
        )


def test_pre_history_rejected() -> None:
    with pytest.raises(CsvImportError, match="1990"):
        parse_csv_bars(
            csv("1800-01-02"),
            symbol="NVDA",
            as_of=date(1800, 1, 2),
            now=datetime(2024, 1, 3, tzinfo=UTC),
        )


def test_gaps_are_explicit_and_never_filled() -> None:
    frame = parse_csv_bars(
        csv("2024-01-02", "2024-01-04"),
        symbol="NVDA",
        as_of=date(2024, 1, 5),
        now=datetime(2024, 1, 6, tzinfo=UTC),
    )
    assert len(frame) == 2
    assert frame.attrs["missing_sessions"] == ["2024-01-03", "2024-01-05"]
