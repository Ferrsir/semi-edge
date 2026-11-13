from __future__ import annotations

import pandas as pd
import pytest

from semiedge.data.schema import DataSchemaError, empty_bar_frame, validate_bar_frame


def test_schema_requires_nanosecond_sessions():
    frame = empty_bar_frame()
    frame.index = frame.index.as_unit("us")
    with pytest.raises(DataSchemaError, match="datetime64"):
        validate_bar_frame(frame)


@pytest.mark.parametrize("column", ["vwap", "trade_count"])
def test_schema_requires_float_optional_columns(column):
    frame = empty_bar_frame()
    frame[column] = pd.Series(dtype="str")
    with pytest.raises(DataSchemaError, match="must be float64"):
        validate_bar_frame(frame)


def test_schema_rejects_duplicate_columns():
    frame = empty_bar_frame()
    frame = pd.concat([frame, frame[["close"]]], axis=1)
    with pytest.raises(DataSchemaError, match="duplicate columns"):
        validate_bar_frame(frame)


def test_schema_rejects_missing_session_label():
    frame = pd.DataFrame(
        {column: [1.0] for column in ("open", "high", "low", "close", "volume")},
        index=pd.DatetimeIndex([pd.NaT], name="session", dtype="datetime64[ns]"),
    )
    with pytest.raises(DataSchemaError, match="without NaT"):
        validate_bar_frame(frame)
