"""Derive per-listing history from the snapshot CSV and write the dashboard JSON.

Usage:
    python -m tracker.metrics [--config config.json]
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from tracker import enrich, utility
from tracker.collect import DEFAULT_CONFIG, load_config, read_rows

log = logging.getLogger("tracker.metrics")

RELIST_GAP_DAYS = 7
LIST_DATE_JITTER_DAYS = 3


def to_number(value):
    if value in ("", None):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


DETAIL_FIELDS = ["garage_spaces", "garage_source", "garage_sqft", "has_garage", "parking", "solar", "pool", "outbuildings", "adu",
                 "sewer", "water", "stories", "hoa", "remarks", "fetched_at"]


def summarize_listing(rows: list[dict], latest_snapshot: date, details: dict | None = None) -> dict:
    rows = sorted(rows, key=lambda r: r["snapshot_date"])
    first, last = rows[0], rows[-1]
    first_seen = date.fromisoformat(first["snapshot_date"])
    last_seen = date.fromisoformat(last["snapshot_date"])

    price_history: list[list] = []
    cuts = 0
    cut_total = 0
    last_cut_date = None
    prev_price = None
    for r in rows:
        p = to_number(r["price"])
        if p is None:
            continue
        if prev_price is None or p != prev_price:
            price_history.append([r["snapshot_date"], p])
            if prev_price is not None and p < prev_price:
                cuts += 1
                cut_total += prev_price - p
                last_cut_date = r["snapshot_date"]
            prev_price = p

    # Sources derive list_date from a days-on-market counter that ticks at its
    # own hour, so consecutive snapshots can disagree by a day. Only a jump
    # bigger than LIST_DATE_JITTER_DAYS means the listing was actually relisted.
    relisted = False
    for a, b in zip(rows, rows[1:]):
        if (date.fromisoformat(b["snapshot_date"]) - date.fromisoformat(a["snapshot_date"])).days > RELIST_GAP_DAYS:
            relisted = True
        if a["list_date"] and b["list_date"]:
            shift = abs((date.fromisoformat(b["list_date"]) - date.fromisoformat(a["list_date"])).days)
            if shift > LIST_DATE_JITTER_DAYS:
                relisted = True

    active = last_seen == latest_snapshot
    end = latest_snapshot if active else last_seen
    dom_start = date.fromisoformat(last["list_date"]) if last["list_date"] else first_seen
    days_on_market = max((end - dom_start).days, 0)

    price = to_number(last["price"])
    sqft = to_number(last["sqft"])
    year_built = to_number(last["year_built"])
    return {
        "listing_id": last["listing_id"],
        "address": last["address"],
        "city": last["city"],
        "zip": last["zip"],
        "price": price,
        "original_price": price_history[0][1] if price_history else None,
        "price_cut_count": cuts,
        "price_cut_total": cut_total,
        "last_cut_date": last_cut_date,
        "price_per_sqft": round(price / sqft) if price and sqft else None,
        "beds": to_number(last["beds"]),
        "baths": to_number(last["baths"]),
        "sqft": sqft,
        "lot_sqft": to_number(last["lot_sqft"]),
        "year_built": year_built,
        "age": latest_snapshot.year - year_built if year_built else None,
        "utility": utility.lookup(last["latitude"], last["longitude"]),
        "property_type": last["property_type"],
        "list_date": last["list_date"] or None,
        "first_seen": first_seen.isoformat(),
        "last_seen": last_seen.isoformat(),
        "days_on_market": days_on_market,
        "days_tracked": (last_seen - first_seen).days + 1,
        "active": active,
        "relisted": relisted,
        "price_history": price_history,
        "url": last["url"],
        "details": {k: details.get(k) for k in DETAIL_FIELDS} if details else None,
    }


def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def build_history(rows: list[dict], dates: list[str], listings: list[dict]) -> list[dict]:
    """One point per snapshot date: market size, medians, and churn for that day."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["snapshot_date"]].append(r)
    first_seen = defaultdict(int)
    last_seen = defaultdict(int)
    for l in listings:
        first_seen[l["first_seen"]] += 1
        last_seen[l["last_seen"]] += 1

    history = []
    for i, d in enumerate(dates):
        day = by_date[d]
        prices = [to_number(r["price"]) for r in day]
        ppsf = []
        dom = []
        d_obj = date.fromisoformat(d)
        for r in day:
            p, s = to_number(r["price"]), to_number(r["sqft"])
            if p and s:
                ppsf.append(p / s)
            if r["list_date"]:
                dom.append((d_obj - date.fromisoformat(r["list_date"])).days)
        history.append({
            "date": d,
            "active": len(day),
            "median_price": median(prices),
            "median_ppsf": round(median(ppsf)) if ppsf else None,
            "median_dom": median(dom),
            "new": first_seen[d] if i > 0 else 0,
            "gone": last_seen[dates[i - 1]] if i > 0 else 0,
        })
    return history


def build_summary(rows: list[dict], details: dict[str, dict] | None = None) -> dict:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not rows:
        return {
            "generated_at": generated_at, "latest_snapshot": None, "first_snapshot": None,
            "snapshot_count": 0, "listing_count": 0, "active_count": 0, "enriched_count": 0,
            "listings": [], "history": [],
        }
    details = details or {}
    dates = sorted({r["snapshot_date"] for r in rows})
    latest = date.fromisoformat(dates[-1])
    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_id[r["listing_id"]].append(r)
    listings = [summarize_listing(group, latest, details.get(lid)) for lid, group in by_id.items()]
    listings.sort(key=lambda l: (not l["active"], l["city"], l["address"]))
    return {
        "generated_at": generated_at,
        "latest_snapshot": dates[-1],
        "first_snapshot": dates[0],
        "snapshot_count": len(dates),
        "listing_count": len(listings),
        "active_count": sum(1 for l in listings if l["active"]),
        "enriched_count": sum(1 for l in listings if l["details"]),
        "history": build_history(rows, dates, listings),
        "listings": listings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config(Path(args.config))
    summary = build_summary(read_rows(Path(config["data_file"])), enrich.load_parsed())
    out = Path(config["summary_file"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    log.info("Wrote %s: %d listings (%d active, %d enriched) across %d snapshots",
             out, summary["listing_count"], summary["active_count"], summary["enriched_count"], summary["snapshot_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
