"""Minimal RentCast client for the sale-listings endpoint.

Every page fetched counts as one request against the monthly quota, so the
caller controls ``max_pages`` and gets back how many requests were made.
"""
from __future__ import annotations

import os

import requests

BASE_URL = "https://api.rentcast.io/v1"
PAGE_SIZE = 500


class RentCastError(RuntimeError):
    pass


def fetch_sale_listings(params: dict, api_key: str | None = None, session=None, max_pages: int = 2):
    """Return (listings, requests_made) for one query, following offset pagination."""
    api_key = api_key or os.environ.get("RENTCAST_API_KEY")
    if not api_key:
        raise RentCastError("RENTCAST_API_KEY is not set")
    session = session or requests.Session()
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}

    listings: list[dict] = []
    offset = 0
    requests_made = 0
    for _ in range(max_pages):
        query = {**params, "limit": PAGE_SIZE, "offset": offset}
        resp = session.get(f"{BASE_URL}/listings/sale", params=query, headers=headers, timeout=60)
        requests_made += 1
        if resp.status_code == 404:
            break
        if resp.status_code != 200:
            raise RentCastError(f"RentCast returned {resp.status_code}: {resp.text[:300]}")
        page = resp.json()
        if not isinstance(page, list):
            raise RentCastError(f"Unexpected response shape: {str(page)[:300]}")
        listings.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return listings, requests_made
