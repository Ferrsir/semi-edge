"""F-175: distinguish full sessions and select the last recorded observation explicitly."""

import importlib.util

import pytest

from semiedge.dashboard.app import _equity_chart

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("altair") is None, reason="equity chart requires dashboard extra"
)


class ChartSink:
    def __init__(self):
        self.chart = None

    def subheader(self, value):
        pass

    def altair_chart(self, chart, **kwargs):
        self.chart = chart


def test_equity_chart_preserves_full_session_identity():
    sink = ChartSink()
    days = ["2024-01-02", "2024-01-03", "2025-01-02", "2025-01-03"]
    _equity_chart(
        sink,
        [
            {"session": day, "ts": day + "T21:00:00+00:00", "equity": 100.0 + i}
            for i, day in reversed(list(enumerate(days)))
        ],
    )
    spec = sink.chart.to_dict()
    x = spec["encoding"]["x"]["field"]
    assert sink.chart.data[x].nunique() == 4
    assert sink.chart.data[x].astype(str).tolist() == days
    assert sink.chart.data["Equity"].tolist() == [100, 101, 102, 103]
    assert spec["encoding"]["y"]["scale"]["zero"] is False


def test_equity_chart_uses_latest_timestamp_per_session():
    sink = ChartSink()
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    points = [
        {"session": day, "ts": day + "T21:00:00+00:00", "equity": 100.0 + i}
        for i, day in enumerate(days)
    ]
    points.append({"session": days[0], "ts": days[0] + "T15:00:00+00:00", "equity": 10.0})
    _equity_chart(sink, points)
    assert len(sink.chart.data) == 4
    assert sink.chart.data["Equity"].tolist() == [100, 101, 102, 103]


def test_equity_chart_minimum_counts_distinct_sessions():
    sink = ChartSink()
    _equity_chart(
        sink,
        [
            {"session": "2024-01-02", "ts": f"2024-01-02T{hour}:00:00+00:00", "equity": 100.0}
            for hour in [15, 16, 17, 18]
        ],
    )
    assert sink.chart is None
