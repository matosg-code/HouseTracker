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

from tracker.collect import DEFAULT_CONFIG, load_config, read_rows

log = logging.getLogger("tracker.metrics")

RELIST_GAP_DAYS = 7


def to_number(value):
    if value in ("", None):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def summarize_listing(rows: list[dict], latest_snapshot: date) -> dict:
    rows = sorted(rows, key=lambda r: r["snapshot_date"])
    first, last = rows[0], rows[-1]
    first_seen = date.fromisoformat(first["snapshot_date"])
    last_seen = date.fromisoformat(last["snapshot_date"])

    price_history: list[list] = []
    cuts = 0
    cut_total = 0
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
            prev_price = p

    list_dates = {r["list_date"] for r in rows if r["list_date"]}
    gap_relist = any(
        (date.fromisoformat(b["snapshot_date"]) - date.fromisoformat(a["snapshot_date"])).days > RELIST_GAP_DAYS
        for a, b in zip(rows, rows[1:])
    )
    relisted = len(list_dates) > 1 or gap_relist

    active = last_seen == latest_snapshot
    end = latest_snapshot if active else last_seen
    dom_start = date.fromisoformat(last["list_date"]) if last["list_date"] else first_seen
    days_on_market = max((end - dom_start).days, 0)

    price = to_number(last["price"])
    sqft = to_number(last["sqft"])
    return {
        "listing_id": last["listing_id"],
        "address": last["address"],
        "city": last["city"],
        "zip": last["zip"],
        "price": price,
        "original_price": price_history[0][1] if price_history else None,
        "price_cut_count": cuts,
        "price_cut_total": cut_total,
        "price_per_sqft": round(price / sqft) if price and sqft else None,
        "beds": to_number(last["beds"]),
        "baths": to_number(last["baths"]),
        "sqft": sqft,
        "lot_sqft": to_number(last["lot_sqft"]),
        "year_built": to_number(last["year_built"]),
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
    }


def build_summary(rows: list[dict]) -> dict:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not rows:
        return {
            "generated_at": generated_at, "latest_snapshot": None, "first_snapshot": None,
            "snapshot_count": 0, "listing_count": 0, "active_count": 0, "listings": [],
        }
    dates = sorted({r["snapshot_date"] for r in rows})
    latest = date.fromisoformat(dates[-1])
    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_id[r["listing_id"]].append(r)
    listings = [summarize_listing(group, latest) for group in by_id.values()]
    listings.sort(key=lambda l: (not l["active"], l["city"], l["address"]))
    return {
        "generated_at": generated_at,
        "latest_snapshot": dates[-1],
        "first_snapshot": dates[0],
        "snapshot_count": len(dates),
        "listing_count": len(listings),
        "active_count": sum(1 for l in listings if l["active"]),
        "listings": listings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config(Path(args.config))
    summary = build_summary(read_rows(Path(config["data_file"])))
    out = Path(config["summary_file"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    log.info("Wrote %s: %d listings (%d active) across %d snapshots",
             out, summary["listing_count"], summary["active_count"], summary["snapshot_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
