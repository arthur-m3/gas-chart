"""Render the natural gas forward-curve chart from curve.csv.

Produces a clean, client-facing figure for energy memos: a navy forward-rate
line over a light-blue 3-year trading-range band. No title (the caption lives
in the memo document).

    python render_chart.py                       # from outputs/curve.csv
    python render_chart.py --input sample.csv     # render a hand-made CSV
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MultipleLocator

NAVY = "#1F395F"
BAND = "#B8CCE4"
GRID_GRAY = "#D9D9D9"
CAPTION_GRAY = "#808080"

DEFAULT_INPUT = Path(__file__).resolve().parent / "outputs" / "curve.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "outputs" / "gas_forward_chart.jpg"

REQUIRED_COLUMNS = ["month", "forward_rate", "range_low", "range_high"]


def resolve_as_of(input_path: Path) -> str:
    """Return the 'as of' date string from a sibling metadata.json, if present."""
    meta_path = input_path.resolve().parent / "metadata.json"
    if meta_path.exists():
        try:
            generated_at = json.loads(meta_path.read_text())["generated_at"]
            return pd.Timestamp(generated_at).strftime("%B %-d, %Y")
        except (ValueError, KeyError):
            pass
    return datetime.now(timezone.utc).strftime("%B %-d, %Y")


def month_label(month: str) -> str:
    """'2027-01' -> "Jan '27"."""
    ts = pd.Timestamp(f"{month}-01")
    return f"{ts:%b} '{ts:%y}"


def load_curve(input_path: Path) -> pd.DataFrame:
    if not input_path.exists():
        raise FileNotFoundError(f"input CSV not found: {input_path}")
    df = pd.read_csv(input_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"input CSV missing columns: {', '.join(missing)}")
    return df.sort_values("month").reset_index(drop=True)


def render(df: pd.DataFrame, output_path: Path, as_of: str) -> None:
    x = range(len(df))

    fig, ax = plt.subplots(figsize=(13, 5.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # 3-year trading range band.
    ax.fill_between(
        x, df["range_low"], df["range_high"],
        color=BAND, alpha=0.5, linewidth=0, zorder=1,
    )
    # Forward-rate line: straight segments, no smoothing.
    ax.plot(
        x, df["forward_rate"],
        color=NAVY, linewidth=2.0, solid_joinstyle="round", zorder=3,
    )

    # Horizontal gridlines only.
    ax.grid(axis="y", color=GRID_GRAY, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, colors="#333333")

    # No top/right spines; soften the remaining ones.
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID_GRAY)

    # Y axis: $/MMBtu, $1 ticks, "$3.00" format.
    lo = float(min(df["range_low"].min(), df["forward_rate"].min()))
    hi = float(max(df["range_high"].max(), df["forward_rate"].max()))
    ax.set_ylim(int(lo), int(hi) + 1)
    ax.yaxis.set_major_locator(MultipleLocator(1))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:0.2f}"))
    ax.set_ylabel("$/MMBtu", color="#333333")

    # X axis: vertical labels so we can fit them densely (every 2 months).
    tick_positions = list(range(0, len(df), 2))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(
        [month_label(df["month"].iloc[i]) for i in tick_positions],
        rotation=90,
    )
    ax.set_xlim(0, len(df) - 1)

    # Legend: line + band.
    legend_handles = [
        plt.Line2D([0], [0], color=NAVY, linewidth=2.0, label="Current Rate"),
        Patch(facecolor=BAND, alpha=0.5, label="3-Year Trading Range"),
    ]
    ax.legend(
        handles=legend_handles, loc="upper left",
        frameon=False, fontsize=10,
    )

    # Bottom-right caption.
    fig.text(
        0.995, 0.02,
        f"As of {as_of}. Source: NYMEX settlements.",
        ha="right", va="bottom", fontsize=8, color=CAPTION_GRAY,
    )

    fig.tight_layout(rect=(0, 0.06, 1, 1))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path, format="jpg", dpi=200,
        pil_kwargs={"quality": 90},
    )
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT,
        help="input curve CSV (default: outputs/curve.csv)",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help="output JPG path (default: outputs/gas_forward_chart.jpg)",
    )
    args = parser.parse_args()

    try:
        df = load_curve(args.input)
        as_of = resolve_as_of(args.input)
        render(df, args.output, as_of)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
