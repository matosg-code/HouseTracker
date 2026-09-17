import json

from tracker import enrich


def below(groups):
    """Build a belowTheFold payload from {group title: {amenity name: [values]}}."""
    return {"payload": {"amenitiesInfo": {"superGroups": [{"amenityGroups": [
        {"groupTitle": g, "amenityEntries": [{"amenityName": n, "amenityValues": v} for n, v in entries.items()]}
        for g, entries in groups.items()]}]}}}


def main(remark):
    return {"payload": {"mainHouseInfo": {"marketingRemarks": [{"marketingRemark": remark}]}}}


def parse(remark="", groups=None):
    return enrich.parse_details({"mainHouseInfoPanelInfo": main(remark), "belowTheFold": below(groups or {})})


def test_property_id_from_url():
    assert enrich.property_id("https://www.redfin.com/CA/Merced/1-A-St-95340/home/19689152") == "19689152"
    assert enrich.property_id("https://example.com/") is None


def test_flatten_amenities_joins_values():
    amen = enrich.flatten_amenities(below({"Parking & Garage Information": {"# of Garage Spaces": [2], "Parking Features": ["Attached", "RV Access"]}})["payload"])
    assert amen == {"Parking & Garage Information": {"# of Garage Spaces": "2", "Parking Features": "Attached, RV Access"}}


def test_garage_from_structured_field():
    p = parse(groups={"Parking & Garage Information": {"# of Garage Spaces": [3]}})
    assert p["garage_spaces"] == 3 and p["has_garage"] is True


def test_garage_estimated_from_sqft():
    p = parse(groups={"Parking & Garage Information": {"Parking Type": ["Attached Garage"], "Garage/Parking Sq. Ft": [455]}})
    assert p["garage_spaces"] == 2 and p["garage_source"] == "sqft" and p["garage_sqft"] == 455
    p = parse(groups={"Parking & Garage Information": {"Parking Type": ["Attached Garage"], "# of Parking Spaces": [3], "Garage/Parking Sq. Ft": [722]}})
    assert p["garage_spaces"] == 3 and p["garage_source"] == "field"


def test_garage_from_prose():
    assert parse("Spacious three-car garage and RV parking.")["garage_spaces"] == 3
    assert parse("Attached 2 car garage.")["garage_spaces"] == 2
    assert parse("Laundry room and two-bay garage.")["garage_spaces"] == 2
    assert parse("Carport only, no garage.")["has_garage"] is False


def test_solar_classification():
    assert parse("Enjoy PAID solar for energy-efficient living.")["solar"] == "owned"
    assert parse("Solar panels are owned outright.")["solar"] == "owned"
    assert parse("Leased solar panels, buyer to assume.")["solar"] == "leased"
    assert parse("Solar is already in place.")["solar"] == "mentioned"
    assert parse("Sunny backyard.")["solar"] == "none"


def test_pool_from_field_and_prose():
    assert parse(groups={"Exterior Features": {"Pool Features": ["None"]}})["pool"] is False
    assert parse(groups={"Exterior Features": {"Pool Features": ["In Ground", "Gunite"]}})["pool"] is True
    assert parse("Sparkling pool and covered patio.")["pool"] is True
    assert parse("Close to the carpool lane.")["pool"] is None


def test_outbuildings():
    p = parse("Detached workshop with power, two sheds, RV parking, and a casita out back.")
    assert set(p["outbuildings"]) == {"workshop", "shed", "RV parking", "guest house"}
    assert parse("Nice house.")["outbuildings"] == []
    assert parse("Plenty of room to build a shop or ADU later.")["outbuildings"] == []
    assert parse("Garage has a shop-like feel.")["outbuildings"] == []
    assert parse("Includes a 324sf shop with power.")["outbuildings"] == ["workshop"]


def test_adu_sewer_water():
    p = parse(groups={"Property Information": {"Property ADU": ["No"]}, "Utilities Information": {"Sewer": ["Public Sewer"], "Water Source": ["District/Public"]}})
    assert p["adu"] is False and p["sewer"] == "Public Sewer" and p["water"] == "District/Public"


def test_ingest_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "DETAILS_DIR", tmp_path)
    saved, rejected = enrich.ingest([
        {"listing_id": "mls:1", "property_id": "111", "address": "1 A St", "url": "u", "raw": {"mainHouseInfoPanelInfo": main("paid solar, 3-car garage")}},
        {"listing_id": "mls:2", "property_id": "abc", "raw": {}},
    ])
    assert saved == 1 and len(rejected) == 1
    stored = json.loads((tmp_path / "111.json").read_text(encoding="utf-8"))
    assert stored["raw"]["mainHouseInfoPanelInfo"]["payload"]["mainHouseInfo"]["marketingRemarks"][0]["marketingRemark"].startswith("paid")
    parsed = enrich.load_parsed(tmp_path)
    assert parsed["mls:1"]["solar"] == "owned" and parsed["mls:1"]["garage_spaces"] == 3


def test_ingest_scrubs_google_maps_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "DETAILS_DIR", tmp_path)
    key = "AIza" + "x" * 35
    enrich.ingest([{"listing_id": "mls:9", "property_id": "999", "raw": {"propertyParcelInfo": {"payload": {"staticMapUrl": f"https://maps.google.com/x?key={key}"}}}}])
    text = (tmp_path / "999.json").read_text(encoding="utf-8")
    assert key not in text and "key=REDACTED" in text


def test_todo_skips_done_and_filters_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "DETAILS_DIR", tmp_path)
    (tmp_path / "222.json").write_text("{}")
    csv_path = tmp_path / "listings.csv"
    csv_path.write_text(
        "snapshot_date,listing_id,address,city,zip,price,beds,baths,sqft,lot_sqft,year_built,property_type,status,list_date,latitude,longitude,url,source\n"
        "2026-09-16,mls:1,1 A St,Merced,95340,1,3,2,1900,,,Single Family Residential,Active,,,,https://r/home/111,redfin\n"
        "2026-09-17,mls:1,1 A St,Merced,95340,1,3,2,1900,,,Single Family Residential,Active,,,,https://r/home/111,redfin\n"
        "2026-09-17,mls:2,2 B St,Merced,95340,1,3,2,1900,,,Single Family Residential,Active,,,,https://r/home/222,redfin\n"
        "2026-09-17,mls:3,3 C St,Merced,95340,1,2,1,900,,,Single Family Residential,Active,,,,https://r/home/333,redfin\n"
        "2026-09-17,mls:4,4 D St,Merced,95340,1,,,,,,Vacant Land,Active,,,,https://r/home/444,redfin\n"
    )
    config = {"data_file": str(csv_path)}
    assert [i["property_id"] for i in enrich.todo(config)] == ["111", "333", "444"]
    assert [i["property_id"] for i in enrich.todo(config, only_defaults=True)] == ["111"]
