from pathlib import Path

import pytest

from tracker import collect, redfin

FIXTURE = Path(__file__).parent / "fixtures" / "redfin_sale_listings.csv"
HEADER = FIXTURE.read_text().splitlines()[0]


def make_csv(prices):
    lines = [HEADER]
    for i, p in enumerate(prices):
        lines.append(f"MLS Listing,,Single Family Residential,{i} Main St,Merced,CA,95340,{p},3,2.0,Merced,1500,6000,1990,5,200,,Active,,,https://www.redfin.com/x/{i},MetroList,{100000 + i},N,Y,37.3,-120.4")
    return "\n".join(lines) + "\n"


class FakeResponse:
    def __init__(self, text, status=200, ctype="text/csv;charset=UTF-8"):
        self.text = text
        self.status_code = status
        self.headers = {"content-type": ctype}


class FakeSession:
    """Serves listings filtered by min_price/max_price, capped at ROW_CAP like Redfin."""

    def __init__(self, prices):
        self.prices = prices
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        lo = params.get("min_price", 0)
        hi = params.get("max_price", 10**12)
        chosen = [p for p in self.prices if lo <= p <= hi][: redfin.ROW_CAP]
        return FakeResponse(make_csv(chosen))


def test_parse_csv_skips_disclaimer_line():
    rows = redfin.parse_csv(FIXTURE.read_text())
    assert len(rows) == 4
    assert rows[0]["ADDRESS"] == "2747 Saratoga Ave"


def test_small_region_is_one_request():
    session = FakeSession(list(range(100000, 100000 + 80)))
    rows = redfin.fetch_region(11970, session=session, sleep=lambda s: None)
    assert len(rows) == 80
    assert len(session.calls) == 1


def test_large_region_splits_by_price_until_complete():
    prices = [200000 + i * 1000 for i in range(900)]
    session = FakeSession(prices)
    counted = []
    rows = redfin.fetch_region(11970, session=session, on_request=lambda: counted.append(1), sleep=lambda s: None)
    got = sorted(int(r["PRICE"]) for r in rows)
    assert got == prices
    assert len(counted) == len(session.calls)
    assert len(session.calls) >= 3


def test_non_csv_response_raises():
    class S:
        def get(self, *a, **k):
            return FakeResponse("<html>blocked</html>", status=200, ctype="text/html")

    with pytest.raises(redfin.RedfinError):
        redfin.fetch_region(1, session=S(), sleep=lambda s: None)


def test_normalize_redfin_row():
    row = redfin.parse_csv(FIXTURE.read_text())[1]
    out = collect.normalize(row, "2026-09-16", source="redfin")
    assert out == {
        "snapshot_date": "2026-09-16",
        "listing_id": "mls:226100001",
        "address": "402 Fruitland Ave",
        "city": "Atwater",
        "zip": "95301",
        "price": 615000,
        "beds": 4,
        "baths": 3,
        "sqft": 2480,
        "lot_sqft": 21780,
        "year_built": 2005,
        "property_type": "Single Family Residential",
        "status": "Active",
        "list_date": "2026-07-02",
        "latitude": 37.3521,
        "longitude": -120.6103,
        "url": "https://www.redfin.com/CA/Atwater/402-Fruitland-Ave-95301/home/19700001",
        "source": "redfin",
    }


def test_normalize_redfin_missing_mls_falls_back_to_url():
    row = redfin.parse_csv(FIXTURE.read_text())[2]
    out = collect.normalize(row, "2026-09-16", source="redfin")
    assert out["listing_id"] == "https://www.redfin.com/CA/Merced/0-Bear-Creek-Dr-95340/home/19700002"
    assert out["beds"] == ""
    assert out["price"] == 95000


def test_run_from_redfin_csv_file(tmp_path):
    config = {
        "source": "redfin",
        "queries": [{"region_id": 11970}],
        "cities": ["Merced", "Atwater"],
        "data_file": str(tmp_path / "listings.csv"),
    }
    assert collect.run(config, "2026-09-16", from_file=str(FIXTURE)) == 3
    rows = collect.read_rows(Path(config["data_file"]))
    assert {r["city"] for r in rows} == {"Merced", "Atwater"}
    assert all(r["source"] == "redfin" for r in rows)
