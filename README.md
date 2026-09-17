# HouseTracker

Daily for-sale listing snapshots for Merced and Atwater, CA. A GitHub Actions
cron pulls listings from Redfin's CSV export every morning, appends them to an
append-only CSV committed to this repo, and rebuilds a static dashboard served
by GitHub Pages. No server, no API key, no hosting bill.

Live dashboard: https://matosg-code.github.io/HouseTracker/

## How it works

```
Redfin CSV export ──► tracker/collect.py ──► data/listings.csv   (one row per listing per day, never rewritten)
                                              │
                                              ▼
                                      tracker/metrics.py ──► docs/data/summary.json ──► docs/index.html
```

Days on market, price cuts, relists and market medians are all **derived** from
the snapshot history. The CSV is the source of truth; everything else can be
regenerated.

## Schema (`data/listings.csv`)

| column | meaning |
|---|---|
| `snapshot_date` | day the row was captured (YYYY-MM-DD) |
| `listing_id` | `mls:<MLS#>` from Redfin, or the Redfin URL when no MLS number is given |
| `address`, `city`, `zip` | street address and locality |
| `price`, `beds`, `baths`, `sqft`, `lot_sqft`, `year_built` | listing facts as reported that day |
| `property_type` | Single Family, Condo, Land, ... |
| `status` | listing status (`Active`) |
| `list_date` | snapshot date minus Redfin's days-on-market; resets when a listing is relisted |
| `latitude`, `longitude` | for a map later |
| `url` | Redfin listing page |
| `source` | `redfin` (or `rentcast` if switched) |

## Sources

**Redfin (default).** The same CSV that the "Download All" button on a Redfin
search page produces, fetched with a browser user agent. Free, no account, but
unofficial: if Redfin changes the endpoint or blocks GitHub's IP range the run
fails loudly and nothing is recorded. Each export is capped at 350 rows, so
`tracker/redfin.py` splits a region into price bands until every band fits.
Merced currently takes 3 requests, Atwater 1. Region ids in `config.json` came
from Redfin's location autocomplete (`/city/11970/CA/Merced`, `/city/844/CA/Atwater`).

**RentCast (fallback).** A real API with a free 50-request tier, but it needs
a card on file. The client is still in `tracker/rentcast.py`; to switch, copy
the `rentcast` block in `config.json` over the top-level keys and add the
`RENTCAST_API_KEY` repository secret.

## Request budget

Every HTTP request is recorded in `data/api_usage.json` per calendar month and
committed back to the repo. The collector refuses to start a run that could
push the month past `monthly_call_budget` (400 for Redfin, which is ~4 a day
with headroom; 45 if using RentCast's free tier). Failed requests still count.
Re-running a date that already has rows costs nothing. If the cap trips the
run exits with code 2 and the workflow shows as failed with the reason in the
log. To spend more, raise the number in `config.json` on purpose.

## Setup (already done for this repo)

1. GitHub Pages: Settings → Pages → Deploy from branch `main`, folder `/docs`.
2. First run: Actions → "Daily snapshot" → Run workflow. Tick "force" to
   replace a day's rows.

Snapshots run at 14:00 UTC (6am PST / 7am PDT) and commit as
`data: snapshot YYYY-MM-DD`.

## Local use

```bash
pip install -r requirements.txt
python -m pytest -q
python -m tracker.collect                               # live pull (Redfin, no key)
python -m tracker.collect --from-file tests/fixtures/redfin_sale_listings.csv --date 2026-09-16
python -m tracker.metrics
cd docs && python -m http.server 8000                   # dashboard at http://localhost:8000
```

`--force` replaces rows already recorded for a date; `--from-file` replays a
saved API response without spending a request.
