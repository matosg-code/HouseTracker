from tracker import utility

SQUARE = {"type": "Feature", "properties": {"Acronym": "MeID"},
          "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}}
DONUT = {"type": "Feature", "properties": {"Acronym": "TID"},
         "geometry": {"type": "MultiPolygon", "coordinates": [
             [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]], [[24, 24], [26, 24], [26, 26], [24, 26], [24, 24]]]]}}
AREAS = [SQUARE, DONUT]


def test_inside_polygon_maps_acronym():
    assert utility.lookup(5, 5, AREAS) == "MID"


def test_outside_all_polygons_defaults_to_pge():
    assert utility.lookup(50, 50, AREAS) == "PG&E"


def test_hole_in_multipolygon_is_not_inside():
    assert utility.lookup(22, 22, AREAS) == "TID"
    assert utility.lookup(25, 25, AREAS) == "PG&E"


def test_missing_coordinates():
    assert utility.lookup("", "", AREAS) is None
    assert utility.lookup(None, None, AREAS) is None


def test_real_polygons_classify_merced_and_atwater():
    areas = utility.load_areas()
    assert areas, "data/utility_areas.geojson missing"
    assert utility.lookup(37.3022, -120.4830, areas) == "MID"
    assert utility.lookup(37.3477, -120.6090, areas) == "MID"
    assert utility.lookup(37.0583, -120.8499, areas) == "PG&E"
