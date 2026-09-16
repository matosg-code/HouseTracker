# HouseTracker

Daily for-sale listing snapshots for Merced and Atwater, CA. A GitHub Actions
cron pulls listings from RentCast every morning, appends them to an
append-only CSV committed to this repo, and rebuilds a static dashboard served
by GitHub Pages. No server, no hosting bill.

## How it works

```
RentCast API ──► tracker/collect.py ──► data/listings.csv   (one row per listing per day, never rewritten)
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
| `listing_id` | RentCast listing id (address-based, stable across relists) |
| `address`, `city`, `zip` | street address and locality |
| `price`, `beds`, `baths`, `sqft`, `lot_sqft`, `year_built` | listing facts as reported that day |
| `property_type` | Single Family, Condo, Land, ... |
| `status` | RentCast status (`Active`) |
| `list_date` | date the listing went live per the MLS |
| `latitude`, `longitude` | for a map later |
| `url` | Zillow search link for the address |
| `source` | `rentcast` |

## One-time setup

1. Create a RentCast account and API key at https://app.rentcast.io/app/api.
2. Push this repo to GitHub, then add the key as a repository secret:
   ```bash
   gh secret set RENTCAST_API_KEY
   ```
3. Enable GitHub Pages: repo Settings → Pages → Source "Deploy from a branch",
   branch `main`, folder `/docs`.
4. Trigger the first run: Actions → "Daily snapshot" → Run workflow.

Snapshots run at 14:00 UTC (6am PST / 7am PDT) and commit as
`data: snapshot YYYY-MM-DD`.

## API budget

`config.json` uses one radius query centred between Merced and Atwater and
filters the response to the `cities` list. Each page of 500 results is one
request, so a day normally costs 1 call (2 if the area has more than 500
active listings; the workflow log prints the count). RentCast's free tier is
50 requests/month, so if you see 2 calls/day either move to the paid entry
tier or change the cron to every other day (`0 14 */2 * *`).

## Local use

```bash
pip install -r requirements.txt
python -m pytest -q
RENTCAST_API_KEY=... python -m tracker.collect          # live pull
python -m tracker.collect --from-file tests/fixtures/rentcast_sale_listings.json --date 2026-09-16
python -m tracker.metrics
cd docs && python -m http.server 8000                   # dashboard at http://localhost:8000
```

`--force` replaces rows already recorded for a date; `--from-file` replays a
saved API response without spending a request.
