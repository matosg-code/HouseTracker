"""Verify the RentCast key with one cheap request. Prints status only, never the key.

Reads RENTCAST_API_KEY from the environment or from a local .env file.

Usage:
    python -m tracker.check_key
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

from tracker.rentcast import BASE_URL


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_dotenv()
    key = os.environ.get("RENTCAST_API_KEY", "")
    if not key:
        print("RENTCAST_API_KEY not set (env or .env)")
        return 1
    stripped = key.strip()
    print(f"key length: {len(key)} chars" + (" (has surrounding whitespace!)" if stripped != key else ""))
    resp = requests.get(
        f"{BASE_URL}/listings/sale",
        params={"city": "Merced", "state": "CA", "status": "Active", "limit": 1},
        headers={"X-Api-Key": stripped, "Accept": "application/json"},
        timeout=30,
    )
    print(f"status: {resp.status_code}")
    if resp.status_code == 200:
        print(f"ok: {len(resp.json())} listing(s) returned")
        return 0
    print(resp.text[:300])
    return 1


if __name__ == "__main__":
    sys.exit(main())
