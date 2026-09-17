import csv
import io

from tracker import metrics

HEADER = "snapshot_date,listing_id,address,city,zip,price,beds,baths,sqft,lot_sqft,year_built,property_type,status,list_date,latitude,longitude,url,source"


def rows_from(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(HEADER + "\n" + text.strip() + "\n")))


CSV = """
2026-09-01,A,1 A St,Merced,95340,400000,3,2,2000,8000,1990,Single Family,Active,2026-08-15,,,https://x/a,rentcast
2026-09-02,A,1 A St,Merced,95340,390000,3,2,2000,8000,1990,Single Family,Active,2026-08-15,,,https://x/a,rentcast
2026-09-03,A,1 A St,Merced,95340,390000,3,2,2000,8000,1990,Single Family,Active,2026-08-15,,,https://x/a,rentcast
2026-09-01,B,2 B St,Atwater,95301,300000,2,1,1100,5000,1960,Single Family,Active,2026-08-30,,,https://x/b,rentcast
2026-09-02,B,2 B St,Atwater,95301,300000,2,1,1100,5000,1960,Single Family,Active,2026-08-30,,,https://x/b,rentcast
2026-09-01,C,3 C St,Merced,95348,500000,4,3,2600,12000,2005,Single Family,Active,2026-06-01,,,https://x/c,rentcast
2026-09-03,C,3 C St,Merced,95348,510000,4,3,2600,12000,2005,Single Family,Active,2026-09-02,,,https://x/c,rentcast
2026-09-03,D,4 D St,Merced,95340,,,,,,,Land,Active,,,,https://x/d,rentcast
"""


def test_summary_counts():
    summary = metrics.build_summary(rows_from(CSV))
    assert summary["latest_snapshot"] == "2026-09-03"
    assert summary["first_snapshot"] == "2026-09-01"
    assert summary["snapshot_count"] == 3
    assert summary["listing_count"] == 4
    assert summary["active_count"] == 3


def by_id(summary):
    return {l["listing_id"]: l for l in summary["listings"]}


def test_price_cut_and_days_on_market():
    a = by_id(metrics.build_summary(rows_from(CSV)))["A"]
    assert a["active"] is True
    assert a["original_price"] == 400000
    assert a["price"] == 390000
    assert a["price_cut_count"] == 1
    assert a["price_cut_total"] == 10000
    assert a["price_history"] == [["2026-09-01", 400000], ["2026-09-02", 390000]]
    assert a["days_on_market"] == 19
    assert a["days_tracked"] == 3
    assert a["price_per_sqft"] == 195
    assert a["relisted"] is False


def test_gone_listing_freezes_at_last_seen():
    b = by_id(metrics.build_summary(rows_from(CSV)))["B"]
    assert b["active"] is False
    assert b["last_seen"] == "2026-09-02"
    assert b["days_on_market"] == 3


def test_relist_detected_from_list_date_change():
    c = by_id(metrics.build_summary(rows_from(CSV)))["C"]
    assert c["relisted"] is True
    assert c["price_cut_count"] == 0
    assert c["list_date"] == "2026-09-02"
    assert c["days_on_market"] == 1


def test_list_date_jitter_is_not_a_relist():
    rows = rows_from("""
2026-09-01,F,6 F St,Merced,95340,250000,2,1,900,4000,1950,Single Family,Active,2026-08-20,,,https://x/f,redfin
2026-09-02,F,6 F St,Merced,95340,250000,2,1,900,4000,1950,Single Family,Active,2026-08-21,,,https://x/f,redfin
2026-09-03,F,6 F St,Merced,95340,250000,2,1,900,4000,1950,Single Family,Active,2026-08-20,,,https://x/f,redfin
""")
    assert by_id(metrics.build_summary(rows))["F"]["relisted"] is False


def test_relist_detected_from_snapshot_gap():
    rows = rows_from("""
2026-08-01,E,5 E St,Merced,95340,250000,2,1,900,4000,1950,Single Family,Active,,,,https://x/e,rentcast
2026-08-20,E,5 E St,Merced,95340,250000,2,1,900,4000,1950,Single Family,Active,,,,https://x/e,rentcast
""")
    e = by_id(metrics.build_summary(rows))["E"]
    assert e["relisted"] is True
    assert e["days_on_market"] == 19


def test_missing_numbers_become_null():
    d = by_id(metrics.build_summary(rows_from(CSV)))["D"]
    assert d["price"] is None
    assert d["price_per_sqft"] is None
    assert d["list_date"] is None
    assert d["original_price"] is None


def test_history_per_snapshot():
    h = metrics.build_summary(rows_from(CSV))["history"]
    assert [x["date"] for x in h] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert [x["active"] for x in h] == [3, 2, 3]
    assert h[0]["median_price"] == 400000
    assert h[0]["new"] == 0 and h[0]["gone"] == 0
    assert h[2]["new"] == 1          # D first seen on 09-03
    assert h[2]["gone"] == 1         # B last seen on 09-02
    assert h[2]["median_dom"] == 10  # A:19, C:1; D has no list_date
    assert h[2]["median_ppsf"] == 196


def test_last_cut_date():
    a = by_id(metrics.build_summary(rows_from(CSV)))["A"]
    assert a["last_cut_date"] == "2026-09-02"
    assert by_id(metrics.build_summary(rows_from(CSV)))["C"]["last_cut_date"] is None


def test_empty_input():
    summary = metrics.build_summary([])
    assert summary["listings"] == []
    assert summary["latest_snapshot"] is None
