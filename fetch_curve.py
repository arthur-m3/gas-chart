"""Fetch the Henry Hub natural gas forward curve from NYMEX monthly futures.

Refactored from the original ad-hoc price-summary script. For each monthly
NYMEX Henry Hub contract from the front month forward, we compute:

    forward_rate : latest settlement (close)
    range_low    : lowest settlement over the contract's 3-year window
    range_high   : highest settlement over the contract's 3-year window

All figures use NYMEX settlement (close) prices in $/MMBtu.

Outputs (only written after validation passes):
    outputs/curve.csv       month,forward_rate,range_low,range_high
    outputs/metadata.json   run metadata
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import yfinance as yf

# CME/NYMEX Henry Hub Natural Gas delivery-month codes.
MONTH_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
CURVE_CSV = OUTPUT_DIR / "curve.csv"
METADATA_JSON = OUTPUT_DIR / "metadata.json"

# How many months of contracts to attempt, starting at the front month.
HORIZON_MONTHS = 66
# Minimum rows required for a valid curve.
MIN_MONTHS = 36
# Retry policy for network calls.
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
SOURCE = "NYMEX via Yahoo Finance"


@dataclass
class CurvePoint:
    month: str          # YYYY-MM
    forward_rate: float
    range_low: float
    range_high: float


def ticker_for(year: int, month: int) -> str:
    return f"NG{MONTH_CODE[month]}{year % 100:02d}.NYM"


def front_month(today: pd.Timestamp) -> pd.Timestamp:
    """First delivery month of the forward curve (the month after today)."""
    return (today.normalize().replace(day=1) + pd.DateOffset(months=1))


def fetch_history(ticker: str) -> pd.DataFrame:
    """Fetch 3y daily history with retry + exponential backoff.

    Returns an empty DataFrame if the contract simply has no data (a valid
    outcome for far-dated months that are not yet listed). Raises the last
    exception only if every attempt errored out (a real network failure).
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            hist = yf.Ticker(ticker).history(period="3y")
            return hist if hist is not None else pd.DataFrame()
        except Exception as exc:  # network / parsing failure -> retry
            last_exc = exc
            if attempt < MAX_ATTEMPTS - 1:
                delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(
                    f"  [warn] {ticker}: attempt {attempt + 1}/{MAX_ATTEMPTS} "
                    f"failed ({exc}); retrying in {delay:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def summarize(hist: pd.DataFrame) -> tuple[float, float, float] | None:
    """Return (forward_rate, range_low, range_high) from settlement closes."""
    if hist is None or hist.empty:
        return None
    closes = hist["Close"]
    closes = closes[closes.notna() & (closes > 0)]
    if closes.empty:
        return None
    return float(closes.iloc[-1]), float(closes.min()), float(closes.max())


def build_curve(today: pd.Timestamp) -> list[CurvePoint]:
    start = front_month(today)
    points: list[CurvePoint] = []
    consecutive_missing = 0
    for i in range(HORIZON_MONTHS):
        delivery = start + pd.DateOffset(months=i)
        ticker = ticker_for(delivery.year, delivery.month)
        summary = summarize(fetch_history(ticker))
        if summary is None:
            consecutive_missing += 1
            print(f"  {delivery:%Y-%m} {ticker:12s} no data")
            # Stop scanning once we're clearly past the listed horizon.
            if consecutive_missing >= 3 and len(points) >= MIN_MONTHS:
                break
            continue
        consecutive_missing = 0
        fwd, low, high = summary
        points.append(
            CurvePoint(
                month=f"{delivery:%Y-%m}",
                forward_rate=round(fwd, 3),
                range_low=round(low, 3),
                range_high=round(high, 3),
            )
        )
        print(f"  {delivery:%Y-%m} {ticker:12s} fwd={fwd:.3f} "
              f"low={low:.3f} high={high:.3f}")

    points.sort(key=lambda p: p.month)
    return points


def validate_and_clamp(points: list[CurvePoint]) -> None:
    """Validate the curve in place. Raises ValueError on unrecoverable issues."""
    if len(points) < MIN_MONTHS:
        raise ValueError(
            f"only {len(points)} months of data (need >= {MIN_MONTHS}); "
            "refusing to write a partial curve"
        )
    for p in points:
        if p.forward_rate is None or pd.isna(p.forward_rate):
            raise ValueError(f"null forward_rate for {p.month}")
        # Thin far-dated contracts can settle just outside their historical
        # band; clamp the band to include the forward rate and warn.
        if p.range_low > p.forward_rate:
            print(
                f"  [warn] {p.month}: range_low {p.range_low} > forward_rate "
                f"{p.forward_rate}; clamping band low",
                file=sys.stderr,
            )
            p.range_low = p.forward_rate
        if p.range_high < p.forward_rate:
            print(
                f"  [warn] {p.month}: range_high {p.range_high} < forward_rate "
                f"{p.forward_rate}; clamping band high",
                file=sys.stderr,
            )
            p.range_high = p.forward_rate


def write_outputs(points: list[CurvePoint], generated_at: datetime) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(
        [(p.month, p.forward_rate, p.range_low, p.range_high) for p in points],
        columns=["month", "forward_rate", "range_low", "range_high"],
    )
    # Write atomically via a temp file so a crash never leaves a partial CSV.
    tmp = CURVE_CSV.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(CURVE_CSV)

    metadata = {
        "generated_at": generated_at.isoformat(),
        "months": len(points),
        "front_month": points[0].month,
        "source": SOURCE,
    }
    tmp_meta = METADATA_JSON.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps(metadata, indent=2) + "\n")
    tmp_meta.replace(METADATA_JSON)


def main() -> int:
    generated_at = datetime.now(timezone.utc)
    today = pd.Timestamp(generated_at.date())
    print(f"Building NG forward curve as of {today:%Y-%m-%d} ...")

    try:
        points = build_curve(today)
        validate_and_clamp(points)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    write_outputs(points, generated_at)
    print(
        f"Wrote {CURVE_CSV.relative_to(OUTPUT_DIR.parent)} "
        f"({len(points)} months, front {points[0].month}) "
        f"and {METADATA_JSON.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
