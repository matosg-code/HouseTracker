"""Pull today's for-sale listings and append them to the snapshot CSV.

The CSV is append-only: one row per listing per snapshot date. Never rewrite
history; everything else (days on market, price cuts, relists) is derived
from it by ``tracker.metrics``.

Usage:
    python -m tracker.collect                       # live pull via RentCast
    python -m tracker.collect --from-file resp.json # replay a saved API response
    python -m tracker.collect --date 2026-09-16     # override the snapshot date
    python -m tracker.collect --force               # replace today's rows if present
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from datetime import date
from pathlib import Path

from tracker import rentcast

log = logging.getLogger("tracker.collect")

COLUMNS = [
    "snapshot_date", "listing_id", "address", "city", "zip", "price", "beds", "baths",
    "sqft", "lot_sqft", "year_built", "property_type", "status", "list_date",
    "latitude", "longitude", "url", "source",
]

DEFAULT_CONFIG = Path("config.json")


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def zillow_url(formatted_address: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", formatted_address).strip("-")
    return f"https://www.zillow.com/homes/{slug}_rb/"


def _blank_if_none(value):
    return "" if value is None else value


def normalize(raw: dict, snapshot_date: str, source: str = "rentcast") -> dict:
    formatted = raw.get("formattedAddress") or raw.get("addressLine1") or ""
    street = raw.get("addressLine1") or formatted
    if raw.get("addressLine2"):
        street = f"{street} {raw['addressLine2']}"
    return {
        "snapshot_date": snapshot_date,
        "listing_id": raw.get("id") or formatted,
        "address": street,
        "city": raw.get("city") or "",
        "zip": raw.get("zipCode") or "",
        "price": _blank_if_none(raw.get("price")),
        "beds": _blank_if_none(raw.get("bedrooms")),
        "baths": _blank_if_none(raw.get("bathrooms")),
        "sqft": _blank_if_none(raw.get("squareFootage")),
        "lot_sqft": _blank_if_none(raw.get("lotSize")),
        "year_built": _blank_if_none(raw.get("yearBuilt")),
        "property_type": raw.get("propertyType") or "",
        "status": raw.get("status") or "",
        "list_date": (raw.get("listedDate") or "")[:10],
        "latitude": _blank_if_none(raw.get("latitude")),
        "longitude": _blank_if_none(raw.get("longitude")),
        "url": zillow_url(formatted) if formatted else "",
        "source": source,
    }


def filter_cities(rows: list[dict], cities: list[str] | None) -> list[dict]:
    if not cities:
        return rows
    allowed = {c.strip().lower() for c in cities}
    return [r for r in rows if r["city"].strip().lower() in allowed]


def dedupe(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for r in rows:
        if r["listing_id"] in seen:
            continue
        seen.add(r["listing_id"])
        out.append(r)
    return out


def read_rows(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, rows: list[dict], append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not append or not path.exists() or path.stat().st_size == 0
    with open(path, "a" if append else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


class BudgetExceeded(RuntimeError):
    pass


def read_usage(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_usage(path: Path, usage: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(usage, f, indent=1, sort_keys=True)


def fetch_all(config: dict, snapshot_date: str, from_file: str | None = None):
    """Fetch every configured query, charging each HTTP request against the monthly budget.

    Usage is persisted per calendar month in ``usage_file`` and written even
    when a request fails, so a bad key or outage still counts what it spent.
    """
    if from_file:
        with open(from_file, encoding="utf-8") as f:
            return json.load(f), 0

    usage_file = Path(config.get("usage_file", "data/api_usage.json"))
    budget = config.get("monthly_call_budget", 45)
    month = snapshot_date[:7]
    usage = read_usage(usage_file)
    used = usage.get(month, 0)
    worst_case = len(config["queries"]) * config.get("max_pages_per_query", 2)
    if used + worst_case > budget:
        raise BudgetExceeded(
            f"{used} of {budget} API calls already used in {month}; this run could need {worst_case}. Skipping."
        )

    calls = 0

    def charge():
        nonlocal calls
        calls += 1
        usage[month] = used + calls
        write_usage(usage_file, usage)

    listings: list[dict] = []
    for query in config["queries"]:
        page, _ = rentcast.fetch_sale_listings(
            query, max_pages=config.get("max_pages_per_query", 2), on_request=charge
        )
        listings.extend(page)
    log.info("API budget: %d of %d calls used in %s", used + calls, budget, month)
    return listings, calls


def run(config: dict, snapshot_date: str, from_file: str | None = None, force: bool = False) -> int:
    data_file = Path(config["data_file"])
    existing = read_rows(data_file)
    already = [r for r in existing if r["snapshot_date"] == snapshot_date]
    if already and not force:
        log.info("Snapshot for %s already has %d rows; use --force to replace", snapshot_date, len(already))
        return 0

    raw, calls = fetch_all(config, snapshot_date, from_file)
    rows = [normalize(r, snapshot_date, config.get("source", "rentcast")) for r in raw]
    rows = dedupe(filter_cities(rows, config.get("cities")))
    rows.sort(key=lambda r: (r["city"], r["address"]))
    log.info("Fetched %d listings in %d API call(s); %d kept after city filter", len(raw), calls, len(rows))

    if not rows:
        log.warning("No listings returned; not recording an empty snapshot")
        return 0

    if already:
        kept = [r for r in existing if r["snapshot_date"] != snapshot_date]
        write_rows(data_file, kept + rows, append=False)
    else:
        write_rows(data_file, rows, append=True)
    log.info("Appended %d rows for %s to %s", len(rows), snapshot_date, data_file)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--date", default=date.today().isoformat(), help="snapshot date, YYYY-MM-DD")
    parser.add_argument("--from-file", help="JSON file with a saved RentCast response instead of calling the API")
    parser.add_argument("--force", action="store_true", help="replace rows already recorded for --date")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        run(load_config(Path(args.config)), args.date, args.from_file, args.force)
    except BudgetExceeded as e:
        log.error("%s", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
