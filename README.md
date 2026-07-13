# gas-chart

Auto-generates a Henry Hub natural gas **forward-curve chart** on a schedule.

Every weekday morning a GitHub Actions job pulls NYMEX monthly settlement data,
rebuilds the forward curve with its 3-year trading range, renders a
client-facing chart image, and commits the refreshed artifacts to `outputs/`.

## Outputs

Committed artifacts (raw URLs update automatically after each run):

- Chart image: `https://raw.githubusercontent.com/arthur-m3/gas-chart/main/outputs/gas_forward_chart.jpg`
- Curve data: `https://raw.githubusercontent.com/arthur-m3/gas-chart/main/outputs/curve.csv`
- Run metadata: `https://raw.githubusercontent.com/arthur-m3/gas-chart/main/outputs/metadata.json`

`curve.csv` columns:

| column | meaning |
| --- | --- |
| `month` | delivery month, `YYYY-MM` |
| `forward_rate` | latest NYMEX settlement, `$/MMBtu` |
| `range_low` | lowest settlement over the contract's 3-year window |
| `range_high` | highest settlement over the contract's 3-year window |

## Schedule & manual runs

The workflow (`.github/workflows/chart.yml`) runs on cron `30 11 * * 1-5`
(7:30am ET, weekdays) and can be triggered manually:

- **GitHub UI:** Actions → *Build gas forward-curve chart* → **Run workflow**.
- **CLI:** `gh workflow run chart.yml`

The job fails loudly (and emails you) if data fetching, validation, or
rendering fails — it never commits a partial or stale curve.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python fetch_curve.py      # writes outputs/curve.csv + outputs/metadata.json
python render_chart.py     # writes outputs/gas_forward_chart.jpg
```

### Render from a hand-made CSV (no network)

Use `--input` to iterate on chart styling without fetching data:

```bash
python render_chart.py --input path/to/your.csv --output /tmp/preview.jpg
```

The CSV must have the four columns listed above. If a `metadata.json` sits
next to the input CSV, its `generated_at` drives the "As of" caption;
otherwise today's date is used.

## Data source

NYMEX Henry Hub natural gas settlements, retrieved via Yahoo Finance
(`NG<month><yy>.NYM`). All prices are settlement (close) values in `$/MMBtu`.
