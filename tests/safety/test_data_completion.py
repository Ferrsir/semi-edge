from datetime import date

import pytest

from semiedge.data.synthetic import SyntheticMarketDataSource
from semiedge.data.validation import filter_completed_bars


@pytest.mark.safety
def test_in_progress_session_is_never_exposed():
    frame = SyntheticMarketDataSource().get_daily_bars(
        ["A"], date(2024, 11, 25), date(2024, 12, 2)
    )["A"]
    completed = filter_completed_bars(frame, date(2024, 11, 27))
    assert list(completed.index.date) == [
        date(2024, 11, 25),
        date(2024, 11, 26),
        date(2024, 11, 27),
    ]
