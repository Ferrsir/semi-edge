from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from semiedge.data.csv_import import CsvImportError, load_csv_bars, parse_csv_bars
from semiedge.data.schema import validate_bar_frame

NOW = datetime(2024, 7, 5, 22, tzinfo=UTC)

HEADER = "session,open,high,low,close,volume\n"
ROW = "2024-07-02,10,12,9,11,100\n"


def write_csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bars.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_frame_and_cutoff(tmp_path: Path) -> None:
    path = write_csv(tmp_path, HEADER + ROW + "2024-07-03,11,13,10,12,0\n")
    frame = load_csv_bars(path, symbol="NVDA", now=NOW, as_of=date(2024, 7, 2))
    validate_bar_frame(frame)
    assert frame.index.dtype == "datetime64[ns]"
    assert frame.index.tolist() == [pd.Timestamp("2024-07-02")]
    assert frame.iloc[0].to_dict() == {
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 100.0,
    }


def test_reordered_columns_and_early_close(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "volume,close,low,high,open,session\n0,11,9,12,10,2024-07-03\n")
    assert len(load_csv_bars(path, symbol="BRK.B", now=NOW, as_of=date(2024, 7, 3))) == 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        HEADER,
        HEADER.replace("volume", "close") + ROW,
        HEADER.replace("volume", "extra") + ROW,
        HEADER + ROW.rstrip() + ",1\n",
        HEADER + "\n",
        HEADER + ROW + ROW,
        HEADER + "2024-07-03,10,12,9,11,100\n" + ROW,
        HEADER + ROW.replace("2024-07-02", "2024-07-04"),
        HEADER + ROW.replace("2024-07-02", "2024-07-06"),
        HEADER + ROW.replace("2024-07-02", "2024-02-30"),
        HEADER + ROW.replace("2024-07-02", "2024-7-2"),
        HEADER + ROW.replace("2024-07-02", "2024-07-02T00:00:00"),
        HEADER + ROW.replace(",10,", ",0,"),
        HEADER + ROW.replace(",12,", ",10,"),
        HEADER + ROW.replace(",9,", ",12,"),
        HEADER + ROW.replace(",100", ",-1"),
    ],
)
def test_invalid_csv(tmp_path: Path, text: str) -> None:
    with pytest.raises(CsvImportError):
        load_csv_bars(write_csv(tmp_path, text), symbol="NVDA", now=NOW, as_of=date(2024, 7, 3))


@pytest.mark.parametrize("number", ["NaN", "inf", "1e9999", "true", "1_000", " 10", "１０"])
def test_malformed_numbers(tmp_path: Path, number: str) -> None:
    with pytest.raises(CsvImportError):
        load_csv_bars(
            write_csv(tmp_path, HEADER + ROW.replace(",10,", f",{number},")),
            symbol="NVDA",
            now=NOW,
            as_of=date(2024, 7, 2),
        )


def test_bad_future_row_is_validated_before_cutoff(tmp_path: Path) -> None:
    path = write_csv(tmp_path, HEADER + ROW + "2024-07-03,11,13,10,NaN,10\n")
    with pytest.raises(CsvImportError):
        load_csv_bars(path, symbol="NVDA", now=NOW, as_of=date(2024, 7, 2))


def test_no_rows_before_cutoff(tmp_path: Path) -> None:
    with pytest.raises(CsvImportError, match="cutoff"):
        load_csv_bars(
            write_csv(tmp_path, HEADER + ROW), symbol="NVDA", now=NOW, as_of=date(2024, 7, 1)
        )


@pytest.mark.parametrize("symbol", ["", "nvda", "../NVDA", "NVDA\n"])
def test_bad_symbol(tmp_path: Path, symbol: str) -> None:
    with pytest.raises(CsvImportError, match="symbol"):
        load_csv_bars(
            write_csv(tmp_path, HEADER + ROW), symbol=symbol, now=NOW, as_of=date(2024, 7, 2)
        )


def test_datetime_cutoff_refused(tmp_path: Path) -> None:
    with pytest.raises(CsvImportError, match="as_of"):
        load_csv_bars(
            write_csv(tmp_path, HEADER + ROW),
            symbol="NVDA",
            now=NOW,
            as_of=datetime(2024, 7, 2, tzinfo=UTC),
        )


def test_missing_file_sanitized(tmp_path: Path) -> None:
    with pytest.raises(CsvImportError, match="Cannot read"):
        load_csv_bars(tmp_path / "missing.csv", symbol="NVDA", now=NOW, as_of=date(2024, 7, 2))


def test_parser_matches_file_loader(tmp_path: Path) -> None:
    text = HEADER + ROW
    expected = load_csv_bars(
        write_csv(tmp_path, text), symbol="NVDA", now=NOW, as_of=date(2024, 7, 2)
    )
    actual = parse_csv_bars(text, symbol="NVDA", now=NOW, as_of=date(2024, 7, 2))
    pd.testing.assert_frame_equal(expected, actual)
