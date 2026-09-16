# HouseTracker

Daily housing snapshots for Merced and Atwater, CA. RentCast API → append-only
CSV in git via GitHub Actions → static dashboard on GitHub Pages.

## Rules

- `data/listings.csv` is append-only. Never rewrite or "clean up" historical rows;
  derive everything in `tracker/metrics.py` instead.
- New columns go at the end of `COLUMNS` in `tracker/collect.py`; old rows are
  read back with blanks for missing columns.
- Every API page costs one RentCast request. Keep `max_pages_per_query` low and
  log the call count.
- Never commit an empty snapshot; a failed pull must not look like a mass delisting.
- Run `python -m pytest -q` after changes.

## Layout

- `tracker/rentcast.py` API client
- `tracker/collect.py` fetch, normalize, append
- `tracker/metrics.py` derive DOM, price cuts, relists → `docs/data/summary.json`
- `docs/index.html` dashboard (vanilla JS, reads `data/summary.json`)
- `.github/workflows/snapshot.yml` daily cron, commits data back
