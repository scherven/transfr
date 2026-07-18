"""
Tests for api/facilities.py -- nearest-facility selection.

  * Offline: the pure ranking/selection (category filter + aliases, nearest-first
    from a synthetic POI list, the routed-walk anchor + enrichment with an
    injected route function, and every honest empty/unavailable state) with no DB
    and no osmium.
  * DB-gated (TRANSFR_DB=1): platform_centroids resolves real Berlin platform
    centroids (base DB only, no POI layer), and build_facilities degrades to
    `no_poi_layer` here because no planet extract exists on this host.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core", "pathfinding"))

from api import facilities  # noqa: E402
from api.facilities import (  # noqa: E402
    NO_POI_LAYER, NONE_MAPPED, UNSUPPORTED_CATEGORY,
    attach_walks, canonical_category, nearest_platform_ref, poi_matches,
    rank_facilities, resolve_category, station_bbox,
)
from api.transfers import STATION_UNRESOLVED  # noqa: E402

DB = pytest.mark.skipif(
    os.environ.get("TRANSFR_DB") != "1",
    reason="needs the transfr_eu DB; set TRANSFR_DB=1",
)


# A tiny synthetic POI set in the gather_details shape, laid out east of a
# station centroid at (52.5250, 13.3690) so straight-line distances are known-ish.
def _poi(cat, sub, name, lat, lon, level=None):
    return {"kind": "poi", "category": cat, "subtype": sub, "name": name,
            "lat": lat, "lon": lon, "level_raw": level}


STATION = (52.5250, 13.3690)
POIS = [
    _poi("amenity", "toilets", "WC near", 52.5251, 13.3691),          # closest toilet
    _poi("amenity", "toilets", "WC far", 52.5250, 13.3705),           # farther toilet
    _poi("amenity", "bench", "A bench", 52.5250, 13.36905),           # noise, never a facility
    _poi("shop", "coffee", "Beans", 52.5252, 13.3692, level="1"),     # coffee (shop)
    _poi("amenity", "cafe", "Espresso", 52.5250, 13.36915),          # coffee (amenity), closest coffee
    _poi("amenity", "atm", "Cashpoint", 52.5250, 13.3693),           # atm
    _poi("shop", "clothes", "Threads", 52.5250, 13.3694),            # a shop
]


# ---------------------------------------------------------------------------
# Category resolution + aliases
# ---------------------------------------------------------------------------

def test_canonical_category_normalises_and_aliases():
    assert canonical_category("Toilets") == "toilets"
    assert canonical_category("  WC ") == "toilets"
    assert canonical_category("cafe") == "coffee"
    assert canonical_category("cash") == "atm"
    assert canonical_category("bogus") is None
    assert canonical_category("") is None


def test_resolve_category_unknown_is_none():
    assert resolve_category("toilets") is not None
    assert resolve_category("elevator") is None  # a real thing, but not a POI we map


# ---------------------------------------------------------------------------
# poi_matches + ranking
# ---------------------------------------------------------------------------

def test_poi_matches_respects_category_and_subtype():
    spec = resolve_category("coffee")
    assert poi_matches(_poi("amenity", "cafe", "x", 0, 0), spec)
    assert poi_matches(_poi("shop", "coffee", "x", 0, 0), spec)
    assert not poi_matches(_poi("amenity", "toilets", "x", 0, 0), spec)
    # "shops" matches any shop subtype (None subtypes clause).
    any_shop = resolve_category("shops")
    assert poi_matches(_poi("shop", "clothes", "x", 0, 0), any_shop)
    assert not poi_matches(_poi("amenity", "atm", "x", 0, 0), any_shop)


def test_rank_filters_by_category_and_sorts_nearest_first():
    got = rank_facilities(POIS, *STATION, resolve_category("toilets"))
    assert [f.name for f in got] == ["WC near", "WC far"]      # only toilets, nearest first
    assert got[0].distance_m < got[1].distance_m
    assert got[0].category == "amenity" and got[0].subtype == "toilets"


def test_rank_excludes_noise_and_other_categories():
    got = rank_facilities(POIS, *STATION, resolve_category("coffee"))
    names = {f.name for f in got}
    assert names == {"Beans", "Espresso"}                     # no bench, no toilet, no atm
    # The amenity=cafe sits closer than the shop=coffee, so it ranks first.
    assert got[0].name == "Espresso"
    # level tag is passed through as a display string.
    assert next(f for f in got if f.name == "Beans").level == "1"


def test_rank_drops_poi_without_coordinates_and_caps_limit():
    pois = [_poi("amenity", "toilets", "no-coord", None, None),
            _poi("amenity", "toilets", "a", 52.5251, 13.3690),
            _poi("amenity", "toilets", "b", 52.5252, 13.3690)]
    got = rank_facilities(pois, *STATION, resolve_category("toilets"), limit=1)
    assert len(got) == 1 and got[0].name == "a"              # nearest kept, no-coord dropped


def test_rank_empty_when_none_of_category_present():
    assert rank_facilities(POIS, *STATION, resolve_category("taxi")) == []


# ---------------------------------------------------------------------------
# Routed-walk anchor + enrichment (pure; route function injected)
# ---------------------------------------------------------------------------

PLATFORMS = {
    "1": (52.5250, 13.3690),   # by the station centroid
    "16": (52.5250, 13.3700),  # to the east, near the far toilet
}


def test_nearest_platform_ref_picks_closest():
    assert nearest_platform_ref(52.5250, 13.3691, PLATFORMS) == "1"
    assert nearest_platform_ref(52.5250, 13.3699, PLATFORMS) == "16"
    assert nearest_platform_ref(52.5, 13.3, {}) is None      # no platforms known


def test_attach_walks_routes_from_platform_to_each_facility_anchor():
    facs = rank_facilities(POIS, *STATION, resolve_category("toilets"))
    calls = []

    def route(a, b):
        calls.append((a, b))
        return {"found": True, "walk_time_s": 42.0, "walk_distance_m": 55.0}

    attach_walks(facs, "1", PLATFORMS, route)
    near, far = facs                                          # nearest-first from ranking
    assert near.nearest_platform == "1"                      # WC near -> platform 1
    assert far.nearest_platform == "16"                      # WC far -> platform 16
    # Anchor == from_platform is a zero walk (no pathfind); the other routes.
    assert near.walk_time_s == 0.0 and near.walk_distance_m == 0.0
    assert far.walk_time_s == 42.0 and far.walk_distance_m == 55.0
    assert calls == [("1", "16")]                            # only the non-trivial one routed


def test_attach_walks_leaves_walk_none_when_route_not_found():
    facs = rank_facilities(POIS, *STATION, resolve_category("atm"))
    # Anchor from platform 16 so the ATM's nearest platform ("1") differs and a
    # route is actually attempted -- which here returns unfound.
    attach_walks(facs, "16", PLATFORMS, lambda a, b: {"found": False})
    assert facs[0].nearest_platform == "1"                    # anchor still chosen
    assert facs[0].walk_time_s is None                        # but no walk attached


def test_attach_walks_noop_without_platform_coords():
    facs = rank_facilities(POIS, *STATION, resolve_category("atm"))
    attach_walks(facs, "1", {}, lambda a, b: {"found": True, "walk_time_s": 1.0})
    assert facs[0].nearest_platform is None and facs[0].walk_time_s is None


# ---------------------------------------------------------------------------
# station_bbox
# ---------------------------------------------------------------------------

def test_station_bbox_is_lonlat_ordered_and_encloses_point():
    min_lon, min_lat, max_lon, max_lat = station_bbox(52.5250, 13.3690, radius_m=250.0)
    assert min_lon < 13.3690 < max_lon
    assert min_lat < 52.5250 < max_lat


# ---------------------------------------------------------------------------
# build_facilities: honest degradation without touching the POI layer
# ---------------------------------------------------------------------------

class _FakeCur:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCur()


def test_build_unsupported_category_short_circuits(monkeypatch):
    # Unknown category never resolves a station or touches the layer.
    monkeypatch.setattr(facilities, "resolve_station",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not resolve")))
    r = facilities.build_facilities(_FakeConn(), 52.5, 13.3, "elevator")
    assert not r.found and r.reason == UNSUPPORTED_CATEGORY
    assert r.category == "elevator" and r.facilities == []


def test_build_station_unresolved(monkeypatch):
    monkeypatch.setattr(facilities, "resolve_station", lambda *a, **k: None)
    r = facilities.build_facilities(_FakeConn(), 0.0, 0.0, "toilets")
    assert not r.found and r.reason == STATION_UNRESOLVED
    assert r.relation_id is None


def test_build_degrades_to_no_poi_layer(monkeypatch):
    from api.bridge import StationMatch
    monkeypatch.setattr(facilities, "resolve_station",
                        lambda *a, **k: StationMatch(5688517, "Berlin Hbf", 52.525, 13.369, 3.0))
    monkeypatch.setattr(facilities, "poi_layer_available", lambda cur: False)
    r = facilities.build_facilities(_FakeConn(), 52.525, 13.369, "toilets")
    assert not r.found and r.reason == NO_POI_LAYER
    assert r.station == "Berlin Hbf" and r.relation_id == 5688517
    assert r.facilities == []


def test_build_none_mapped_when_layer_present_but_empty(monkeypatch):
    from api.bridge import StationMatch
    monkeypatch.setattr(facilities, "resolve_station",
                        lambda *a, **k: StationMatch(1, "Somewhere", 52.525, 13.369, 3.0))
    monkeypatch.setattr(facilities, "poi_layer_available", lambda cur: True)
    monkeypatch.setattr(facilities, "gather_pois", lambda cur, bbox: [])   # layer up, nothing tagged
    r = facilities.build_facilities(_FakeConn(), 52.525, 13.369, "toilets")
    assert not r.found and r.reason == NONE_MAPPED


def test_build_found_ranks_and_attaches_walk(monkeypatch):
    from api.bridge import StationMatch
    monkeypatch.setattr(facilities, "resolve_station",
                        lambda *a, **k: StationMatch(1, "Berlin Hbf", *STATION, 3.0))
    monkeypatch.setattr(facilities, "poi_layer_available", lambda cur: True)
    monkeypatch.setattr(facilities, "gather_pois", lambda cur, bbox: POIS)
    monkeypatch.setattr(facilities, "platform_centroids", lambda cur, rel: PLATFORMS)
    monkeypatch.setattr(facilities, "_route_fn",
                        lambda conn, rel: (lambda a, b: {"found": True, "walk_time_s": 30.0,
                                                         "walk_distance_m": 40.0}))
    r = facilities.build_facilities(_FakeConn(), *STATION, "toilets", from_platform="1")
    assert r.found and r.reason is None
    assert [f.name for f in r.facilities] == ["WC near", "WC far"]
    assert r.facilities[0].nearest_platform == "1"            # routed walk attached
    assert r.facilities[1].walk_time_s == 30.0


def test_build_found_without_from_platform_has_no_walk(monkeypatch):
    from api.bridge import StationMatch
    monkeypatch.setattr(facilities, "resolve_station",
                        lambda *a, **k: StationMatch(1, "Berlin Hbf", *STATION, 3.0))
    monkeypatch.setattr(facilities, "poi_layer_available", lambda cur: True)
    monkeypatch.setattr(facilities, "gather_pois", lambda cur, bbox: POIS)
    monkeypatch.setattr(facilities, "platform_centroids",
                        lambda cur, rel: (_ for _ in ()).throw(AssertionError("no anchor needed")))
    r = facilities.build_facilities(_FakeConn(), *STATION, "toilets")
    assert r.found and r.facilities[0].nearest_platform is None
    assert r.facilities[0].walk_time_s is None


# ---------------------------------------------------------------------------
# DB-gated: platform centroids (base DB only) + real degradation here
# ---------------------------------------------------------------------------

@DB
def test_platform_centroids_real_berlin():
    import db
    conn = db.connect(connect_timeout=5)
    with conn.cursor() as cur:
        coords = facilities.platform_centroids(cur, 5688517)  # Berlin Hbf
    assert coords, "expected real Berlin platform centroids"
    assert "1" in coords and "16" in coords
    for ref, (lat, lon) in coords.items():
        assert 52.4 < lat < 52.6 and 13.2 < lon < 13.5       # in Berlin


@DB
def test_build_facilities_degrades_without_pois_table():
    # This host never loaded the `pois` table, so a real end-to-end call must
    # degrade to no_poi_layer rather than raise (the honest-degradation contract).
    import db
    conn = db.connect(connect_timeout=5)
    r = facilities.build_facilities(conn, 52.5251, 13.3694, "toilets")  # near Berlin Hbf
    assert not r.found and r.reason == NO_POI_LAYER
    assert r.station and r.relation_id                        # station still resolved


@DB
def test_gather_pois_reads_the_pois_table():
    """The DB-backed POI layer: a bbox SELECT over `pois`, no osmium. Creates the
    table + a row inside a transaction and ROLLS BACK, so it never touches a real
    loaded table (CREATE TABLE and INSERT both unwind) and needs no planet."""
    import db
    conn = db.connect(connect_timeout=5)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS pois (id BIGINT PRIMARY KEY, category TEXT NOT NULL, "
                "subtype TEXT, name TEXT, level TEXT, lat DOUBLE PRECISION NOT NULL, "
                "lon DOUBLE PRECISION NOT NULL)"
            )
            cur.execute(
                "INSERT INTO pois (id, category, subtype, name, level, lat, lon) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (-999_001, "amenity", "toilets", "Test WC", "0", 52.5252, 13.3690),
            )
            assert facilities.poi_layer_available(cur) is True
            got = facilities.gather_pois(cur, facilities.station_bbox(52.5252, 13.3690))
            assert "Test WC" in [g["name"] for g in got]
            assert all(g["kind"] == "poi" and g["category"] == "amenity" for g in got)
            # The bbox actually filters: a box over Madrid finds none of our Berlin row.
            madrid = facilities.gather_pois(cur, facilities.station_bbox(40.0, -3.0))
            assert "Test WC" not in [g["name"] for g in madrid]
    finally:
        conn.rollback()      # never persist the test table/row
        conn.close()


@DB
def test_build_facility_map_pins_every_facility_in_order(monkeypatch):
    """The map-first surface: a browse export with EVERY facility of the category
    pinned, aligned index-for-index with the ranked list, against the real Berlin
    DB. The POI layer isn't on this host, so inject a synthetic set (as the offline
    tests do) and let the real export + platform centroids do the rest."""
    import db
    conn = db.connect(connect_timeout=5)

    synthetic = [
        _poi("amenity", "toilets", "WC North", 52.5252, 13.3690, "0"),
        _poi("amenity", "toilets", "WC Upper", 52.5255, 13.3696, "1"),
        _poi("amenity", "toilets", None,       52.5249, 13.3688, "-1"),
    ]
    monkeypatch.setattr(facilities, "poi_layer_available", lambda cur: True)
    monkeypatch.setattr(facilities, "gather_pois", lambda cur, bbox: synthetic)

    r = facilities.build_facility_map(conn, 52.525, 13.369, "toilets")
    assert r.found is True and r.station == "Berlin Hauptbahnhof"
    det = r.export["details"]
    # Every facility is pinned, flagged focus, and aligned index-for-index so a
    # tapped pin maps back to its row.
    assert len(det) == len(r.facilities) == 3
    assert all(d.get("focus") for d in det)
    assert [d.get("name") for d in det] == [f.name for f in r.facilities]
    # Pins are lifted to their tagged floor (level x 4 m), not flattened to ground.
    zs = {d.get("name"): d["xyz"][2] for d in det}
    assert zs["WC North"] == 0.0 and zs["WC Upper"] == 4.0
    # Cheap anchor: each facility got a nearest platform, with no pathfind.
    assert all(f.nearest_platform for f in r.facilities)
    # Browse export: the whole station, not a two-platform corridor.
    assert sum(1 for w in r.export["ways"] if w["kind"] == "platform") > 2


@DB
def test_build_facility_map_end_to_end_via_pois_table():
    """The REAL path, no monkeypatch: rows in `pois` -> gather_pois SQL -> ranked
    facilities -> browse export with every pin. Inserts Berlin toilets in a
    transaction and rolls back, so it needs no loaded table and leaves none. This
    is the whole new architecture (indexed bbox SELECT, no osmium) end to end."""
    import db
    conn = db.connect(connect_timeout=5)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS pois (id BIGINT PRIMARY KEY, category TEXT NOT NULL, "
                "subtype TEXT, name TEXT, level TEXT, lat DOUBLE PRECISION NOT NULL, "
                "lon DOUBLE PRECISION NOT NULL)"
            )
            for row in [(-999_101, "amenity", "toilets", "WC Main", "0", 52.5252, 13.3690),
                        (-999_102, "amenity", "toilets", "WC Upper", "1", 52.5256, 13.3697),
                        (-999_103, "amenity", "toilets", None, "-1", 52.5248, 13.3686)]:
                cur.execute(
                    "INSERT INTO pois (id, category, subtype, name, level, lat, lon) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING", row)
        # build_facility_map opens its own cursors on the same connection, so it sees
        # these uncommitted rows -- the real poi_layer_available + gather_pois run.
        r = facilities.build_facility_map(conn, 52.525, 13.369, "toilets")
        assert r.found is True and r.station == "Berlin Hauptbahnhof"
        assert len(r.facilities) == 3
        det = r.export["details"]
        assert len(det) == 3 and all(d.get("focus") for d in det)
        assert [d.get("name") for d in det] == [f.name for f in r.facilities]
        assert all(f.nearest_platform for f in r.facilities)
    finally:
        conn.rollback()      # never persist the test table/rows
        conn.close()


@DB
def test_build_facility_map_degrades_without_pois_table():
    """The `pois` table was never loaded here -> honest no_poi_layer, station still
    resolved (the honest-degradation contract, now DB-backed not planet-backed)."""
    import db
    conn = db.connect(connect_timeout=5)
    r = facilities.build_facility_map(conn, 52.525, 13.369, "toilets")
    assert r.found is False and r.reason == NO_POI_LAYER
    assert r.station == "Berlin Hauptbahnhof" and r.relation_id
