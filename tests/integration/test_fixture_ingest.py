from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from semiedge.data.fixture_ingest import ingest_fixture, main

pytestmark = pytest.mark.integration
CSV = "session,open,high,low,close,volume\n2024-01-02,10,12,9,11,100\n2024-01-03,11,13,10,12,200\n"


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "bars.csv"
    source.write_text(CSV, encoding="utf-8")
    directory = tmp_path / "fixtures"
    directory.mkdir()
    return source, directory / "NVDA.parquet"


def test_round_trip_provenance_and_cutoff(paths: tuple[Path, Path]) -> None:
    source, output = paths
    assert ingest_fixture(source, symbol="NVDA", as_of=date(2024, 1, 2), output=output) == 1
    frame = pd.read_parquet(output)
    assert frame.index.name == "session"
    assert frame.index[0] == pd.Timestamp("2024-01-02")
    assert frame.iloc[0]["close"] == 11.0
    assert frame.iloc[0]["source"] == "fixture"
    assert frame.iloc[0]["symbol"] == "NVDA"
    assert frame.iloc[0]["completed_through"] == "2024-01-02"
    assert frame.iloc[0]["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert pd.Timestamp(frame.iloc[0]["ingested_at"]).tzinfo is not None


def test_does_not_overwrite(paths: tuple[Path, Path]) -> None:
    source, output = paths
    output.write_bytes(b"existing artifact")
    with pytest.raises(FileExistsError):
        ingest_fixture(source, symbol="NVDA", as_of=date(2024, 1, 2), output=output)
    assert output.read_bytes() == b"existing artifact"


def test_rejects_production_path(paths: tuple[Path, Path], tmp_path: Path) -> None:
    source, _ = paths
    output = tmp_path / "NVDA.parquet"
    with pytest.raises(ValueError, match="fixture"):
        ingest_fixture(source, symbol="NVDA", as_of=date(2024, 1, 2), output=output)
    assert not output.exists()


def test_rejects_output_reported_as_symlink(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, output = paths
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == output or original(path))
    with pytest.raises(FileExistsError, match="symbolic link"):
        ingest_fixture(source, symbol="NVDA", as_of=date(2024, 1, 2), output=output)
    assert not output.exists()


def test_cli_labels_fixture(paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    source, output = paths
    assert (
        main([str(source), "--symbol", "NVDA", "--as-of", "2024-01-02", "--output", str(output)])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == "fixture"
    assert result["execution_available"] is False
    assert "NOT VERIFIED" in result["warning"]


def test_cli_invalid_data_is_sanitized(
    paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    source, output = paths
    source.write_text("fixture-secret-content", encoding="utf-8")
    assert (
        main([str(source), "--symbol", "NVDA", "--as-of", "2024-01-02", "--output", str(output)])
        == 2
    )
    assert "fixture-secret-content" not in capsys.readouterr().err
    assert not output.exists()


def test_failed_serialization_removes_partial_file(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, output = paths

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk error")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    with pytest.raises(OSError, match="disk error"):
        ingest_fixture(source, symbol="NVDA", as_of=date(2024, 1, 2), output=output)
    assert not output.exists()
