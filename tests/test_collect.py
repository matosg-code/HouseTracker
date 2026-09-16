import json
from pathlib import Path

from tracker import collect

FIXTURE = Path(__file__).parent / "fixtures" / "rentcast_sale_listings.json"


def make_config(tmp_path):
    return {
        "source": "rentcast",
        "queries": [{"latitude": 37.325, "longitude": -120.546, "radius": 8, "status": "Active"}],
        "cities": ["Merced", "Atwater"],
        "data_file": str(tmp_path / "listings.csv"),
        "summary_file": str(tmp_path / "summary.json"),
    }


def test_normalize_maps_rentcast_fields():
    raw = json.loads(FIXTURE.read_text())[0]
    row = collect.normalize(raw, "2026-09-16")
    assert row == {
        "snapshot_date": "2026-09-16",
        "listing_id": "1234-Olive-Ave,-Merced,-CA-95348",
        "address": "1234 Olive Ave",
        "city": "Merced",
        "zip": "95348",
        "price": 479000,
        "beds": 4,
        "baths": 2.5,
        "sqft": 2150,
        "lot_sqft": 9800,
        "year_built": 1998,
        "property_type": "Single Family",
        "status": "Active",
        "list_date": "2026-08-20",
        "latitude": 37.3195,
        "longitude": -120.4912,
        "url": "https://www.zillow.com/homes/1234-Olive-Ave-Merced-CA-95348_rb/",
        "source": "rentcast",
    }


def test_normalize_tolerates_missing_fields():
    row = collect.normalize({"id": "x", "formattedAddress": "1 Main St, Merced, CA", "addressLine2": "Unit B"}, "2026-09-16")
    assert row["price"] == ""
    assert row["list_date"] == ""
    assert row["address"] == "1 Main St, Merced, CA Unit B"
    assert set(row) == set(collect.COLUMNS)


def test_run_appends_header_and_filtered_rows(tmp_path):
    config = make_config(tmp_path)
    n = collect.run(config, "2026-09-16", from_file=str(FIXTURE))
    assert n == 3
    rows = collect.read_rows(Path(config["data_file"]))
    assert [r["city"] for r in rows] == ["Atwater", "Merced", "Merced"]
    assert all(r["snapshot_date"] == "2026-09-16" for r in rows)


def test_run_is_idempotent_per_date(tmp_path):
    config = make_config(tmp_path)
    collect.run(config, "2026-09-16", from_file=str(FIXTURE))
    assert collect.run(config, "2026-09-16", from_file=str(FIXTURE)) == 0
    assert len(collect.read_rows(Path(config["data_file"]))) == 3


def test_run_appends_second_day_and_force_replaces(tmp_path):
    config = make_config(tmp_path)
    collect.run(config, "2026-09-16", from_file=str(FIXTURE))
    collect.run(config, "2026-09-17", from_file=str(FIXTURE))
    assert len(collect.read_rows(Path(config["data_file"]))) == 6

    smaller = tmp_path / "smaller.json"
    smaller.write_text(json.dumps(json.loads(FIXTURE.read_text())[:1]))
    assert collect.run(config, "2026-09-17", from_file=str(smaller), force=True) == 1
    rows = collect.read_rows(Path(config["data_file"]))
    assert [r["snapshot_date"] for r in rows] == ["2026-09-16"] * 3 + ["2026-09-17"]


def test_run_skips_empty_pull(tmp_path):
    config = make_config(tmp_path)
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    assert collect.run(config, "2026-09-16", from_file=str(empty)) == 0
    assert not Path(config["data_file"]).exists()


def test_dedupe_keeps_first_occurrence():
    rows = [{"listing_id": "a", "v": 1}, {"listing_id": "a", "v": 2}, {"listing_id": "b", "v": 3}]
    assert [r["v"] for r in collect.dedupe(rows)] == [1, 3]
