"""Offline, fixture-only CSV ingestion. Never fetches data or places orders."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from semiedge.data.csv_import import CsvImportError, parse_csv_bars


def ingest_fixture(
    csv_path: Path,
    *,
    symbol: str,
    as_of: date,
    output: Path,
    now: datetime,
    settle_minutes: int = 30,
) -> int:
    """Write a new fixture Parquet file; refuse production paths and existing outputs.

    The caller declares the latest completed session via ``as_of``. This does not
    establish point-in-time availability or validate a vendor's historical revisions.
    """
    if output.is_symlink():
        raise FileExistsError("output must not be a symbolic link")
    destination = output.resolve()
    parts = tuple(part.casefold() for part in destination.parts)
    cache_layout = any(parts[i : i + 3] == ("raw", "bars", "daily") for i in range(len(parts) - 2))
    if (
        destination.suffix != ".parquet"
        or destination.parent.name.casefold() not in {"fixture", "fixtures"}
        or cache_layout
        or any(p.startswith(("feed=", "adjustment=")) for p in parts)
    ):
        raise ValueError(
            "output must be directly inside a fixture directory outside vendor layouts"
        )
    # Hash and parsing must describe the same immutable bytes, not two independent reads.
    original = csv_path.read_bytes()
    try:
        text = original.decode("utf-8-sig")
    except UnicodeError:
        raise CsvImportError("Cannot read CSV fixture") from None
    frame = parse_csv_bars(text, symbol=symbol, as_of=as_of, now=now, settle_minutes=settle_minutes)
    snapshot = frame.copy()
    snapshot["symbol"] = symbol
    snapshot["source"] = "fixture"
    snapshot["completed_through"] = as_of.isoformat()
    snapshot["input_sha256"] = hashlib.sha256(original).hexdigest()
    snapshot["last_session"] = frame.index[-1].date().isoformat()
    snapshot["missing_sessions"] = json.dumps(frame.attrs["missing_sessions"])
    snapshot["adjustment"] = "unknown"
    snapshot["ingested_at"] = now.astimezone(UTC).isoformat()
    # Exclusive creation: an existing artifact is never overwritten, including symlinks.
    with destination.open("xb") as stream:
        try:
            snapshot.to_parquet(stream, engine="pyarrow", index=True)
        except BaseException:
            stream.close()
            destination.unlink()
            raise
    return len(frame)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="CSV with session and OHLCV columns")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        rows = ingest_fixture(
            args.input,
            symbol=args.symbol,
            as_of=args.as_of,
            output=args.output,
            now=datetime.now(UTC),
        )
    except (ValueError, OSError):
        sys.stderr.write(
            "Fixture ingestion failed: check CSV, cutoff, and a new fixture output path.\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(
            {
                "source": "fixture",
                "symbol": args.symbol,
                "rows": rows,
                "completed_through": args.as_of.isoformat(),
                "execution_available": False,
                "warning": "USER-SUPPLIED FIXTURE — NOT VERIFIED MARKET DATA",
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
