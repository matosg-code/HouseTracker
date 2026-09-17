"""Redfin CSV export client. No API key; this is the "Download All" link on a search page.

Redfin caps each export at 350 rows, so a region with more listings is split
into price bands until every band fits. Each HTTP request is reported via
``on_request`` so the caller can count and cap them.
"""
from __future__ import annotations

import csv
import io
import time

import requests

BASE_URL = "https://www.redfin.com/stingray/api/gis-csv"
ROW_CAP = 350
SPLIT_AT = ROW_CAP - 10
MAX_DEPTH = 6
REQUEST_GAP_SECONDS = 1.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/csv,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.redfin.com/",
}

BASE_PARAMS = {
    "al": 1,
    "num_homes": ROW_CAP,
    "ord": "redfin-recommended-asc",
    "page_number": 1,
    "sf": "1,2,3,5,6,7",
    "status": 9,
    "uipt": "1,2,3,4,5,6,7,8",
    "v": 8,
}


class RedfinError(RuntimeError):
    pass


def parse_csv(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    return [r for r in rows if r.get("ADDRESS")]


def fetch_page(params: dict, session, on_request=None) -> list[dict]:
    if on_request:
        on_request()
    resp = session.get(BASE_URL, params={**BASE_PARAMS, **params}, headers=HEADERS, timeout=60)
    if resp.status_code != 200:
        raise RedfinError(f"Redfin returned {resp.status_code}: {resp.text[:200]}")
    if "text/csv" not in resp.headers.get("content-type", ""):
        raise RedfinError(f"Redfin returned non-CSV content: {resp.text[:200]}")
    return parse_csv(resp.text)


def _price(row: dict):
    try:
        return int(float(row["PRICE"]))
    except (KeyError, TypeError, ValueError):
        return None


def fetch_region(region_id: int, region_type: int = 6, session=None, on_request=None,
                 min_price: int | None = None, max_price: int | None = None, depth: int = 0,
                 sleep=time.sleep) -> list[dict]:
    """Return every active listing in a region, splitting by price until each request is under the cap."""
    session = session or requests.Session()
    params: dict = {"region_id": region_id, "region_type": region_type}
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if depth > 0:
        sleep(REQUEST_GAP_SECONDS)

    rows = fetch_page(params, session, on_request)
    if len(rows) < SPLIT_AT or depth >= MAX_DEPTH:
        return rows

    prices = sorted(p for p in (_price(r) for r in rows) if p)
    if not prices:
        return rows
    mid = prices[len(prices) // 2]
    lo = min_price if min_price is not None else 0
    if mid <= lo or (max_price is not None and mid > max_price):
        return rows
    lower = fetch_region(region_id, region_type, session, on_request, min_price, mid - 1, depth + 1, sleep)
    upper = fetch_region(region_id, region_type, session, on_request, mid, max_price, depth + 1, sleep)
    return lower + upper
