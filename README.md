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

## SharePoint mirror (optional)

After each run the workflow can mirror `outputs/` into a SharePoint document
library so team members can grab the chart and data without touching GitHub.
It uploads via the Microsoft Graph API using app-only (client-credentials)
auth, so it runs unattended. The step is **skipped** until the Azure secrets
below are set; once they are, any upload failure fails the job.

### One-time setup

1. **Register an app** in Entra ID (Azure AD) → *App registrations* → *New
   registration*. Note the **Directory (tenant) ID** and **Application
   (client) ID**.
2. **Add a client secret** under *Certificates & secrets* → *New client
   secret*. Copy the secret **value**.
3. **Grant Graph permission** under *API permissions* → *Add a permission* →
   *Microsoft Graph* → *Application permissions*:
   - `Sites.Selected` (recommended — least privilege), then have an admin
     grant this app write access to the specific site (e.g. via
     [Graph `sites/{id}/permissions`](https://learn.microsoft.com/graph/api/site-post-permissions)
     or PnP PowerShell `Grant-PnPAzureADAppSitePermission`), **or**
   - `Sites.ReadWrite.All` (simpler, broader).
   Then click **Grant admin consent**.

### Configure the repo

Add these under *Settings → Secrets and variables → Actions*.

Secrets (sensitive):

| Secret | Value |
| --- | --- |
| `AZURE_TENANT_ID` | Directory (tenant) ID |
| `AZURE_CLIENT_ID` | Application (client) ID |
| `AZURE_CLIENT_SECRET` | Client secret value |

Variables (not sensitive):

| Variable | Example | Notes |
| --- | --- | --- |
| `SHAREPOINT_HOSTNAME` | `contoso.sharepoint.com` | your tenant host |
| `SHAREPOINT_SITE_PATH` | `/sites/EnergyTeam` | server-relative site path |
| `SHAREPOINT_FOLDER` | `gas-chart` | target folder (created if missing) |
| `SHAREPOINT_DRIVE` | `Documents` | *optional* library name; omit for the site default |

For a site URL like `https://contoso.sharepoint.com/sites/EnergyTeam`, the
hostname is `contoso.sharepoint.com` and the site path is `/sites/EnergyTeam`.

### Test it

Trigger a manual run (Actions → *Run workflow*), or run locally after
exporting the same variables:

```bash
export AZURE_TENANT_ID=... AZURE_CLIENT_ID=... AZURE_CLIENT_SECRET=...
export SHAREPOINT_HOSTNAME=contoso.sharepoint.com
export SHAREPOINT_SITE_PATH=/sites/EnergyTeam
export SHAREPOINT_FOLDER=gas-chart
python upload_sharepoint.py
```

## Data source

NYMEX Henry Hub natural gas settlements, retrieved via Yahoo Finance
(`NG<month><yy>.NYM`). All prices are settlement (close) values in `$/MMBtu`.
