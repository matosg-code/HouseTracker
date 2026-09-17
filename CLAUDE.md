# HouseTracker

Daily housing snapshots for Merced and Atwater, CA. Redfin CSV export (no key)
→ append-only CSV in git via GitHub Actions → static dashboard on GitHub Pages.
RentCast client kept as a fallback source; it needs a key and a card on file.

## Rules

- `data/listings.csv` is append-only. Never rewrite or "clean up" historical rows;
  derive everything in `tracker/metrics.py` instead.
- New columns go at the end of `COLUMNS` in `tracker/collect.py`; old rows are
  read back with blanks for missing columns.
- Every HTTP request is counted against `monthly_call_budget` in `config.json`
  and persisted in `data/api_usage.json`. Keep the count visible in logs.
- Redfin caps exports at 350 rows; `redfin.fetch_region` splits by price band.
- Never commit an empty snapshot; a failed pull must not look like a mass delisting.
- Run `python -m pytest -q` after changes.

## Layout

- `tracker/redfin.py` CSV export client (primary)
- `tracker/rentcast.py` API client (fallback)
- `tracker/collect.py` fetch, normalize, append
- `tracker/metrics.py` derive DOM, price cuts, relists → `docs/data/summary.json`
- `docs/index.html` dashboard (vanilla JS, reads `data/summary.json`)
- `.github/workflows/snapshot.yml` daily cron, commits data back
