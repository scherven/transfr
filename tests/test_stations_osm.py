"""The optional OSM-backed station index (api/stations.load_osm_stations).

Covers the Korea gap: with the vendored CSV alone, no Korean station resolves in
any script. These tests drive the loader with a fake connection (offline,
deterministic) and assert it is additive, indexes both scripts, folds
punctuation, and dedups. A skippable tail test runs it against a real transfr_kr.
"""

import pytest

from api import stations


@pytest.fixture(autouse=True)
def _restore_station_index():
    """load_osm_stations mutates module-level index state in place; snapshot and
    restore it so these tests don't leak KR rows into the rest of the suite."""
    stations_snapshot = list(stations._stations)
    index_snapshot = list(stations._search_index)
    loaded_snapshot = set(stations._osm_loaded_relation_ids)
    try:
        yield
    finally:
        stations._stations[:] = stations_snapshot
        stations._search_index[:] = index_snapshot
        stations._osm_loaded_relation_ids.clear()
        stations._osm_loaded_relation_ids.update(loaded_snapshot)


# --- fake DB plumbing (mirrors RealDictCursor: rows are dicts) ---------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed_sql = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed_sql = sql

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


# Seoul (main hub), Myeong-dong (hyphenated name:en), Gangnam (main hub).
_ROWS = [
    {"relation_id": 1, "name": "서울역", "name_en": "Seoul Station",
     "latitude": 37.5546, "longitude": 126.9707, "country": None, "n_members": 40},
    {"relation_id": 3, "name": "명동", "name_en": "Myeong-dong Station",
     "latitude": 37.5608, "longitude": 126.9862, "country": None, "n_members": 12},
    {"relation_id": 9, "name": "강남", "name_en": "Gangnam Station",
     "latitude": 37.4979, "longitude": 127.0276, "country": None, "n_members": 41},
]


def _load(rows=_ROWS):
    return stations.load_osm_stations(_FakeConn(rows))


# --- the gap this closes -----------------------------------------------------

def test_korean_station_does_not_resolve_without_the_osm_index():
    # Baseline: the CSV is Europe-only, so before loading nothing Korean resolves
    # -- in English or in Hangul. This is the bug.
    with pytest.raises(ValueError):
        stations.resolve_station("Seoul Station")
    with pytest.raises(ValueError):
        stations.resolve_station("서울역")


def test_resolves_by_english_and_by_native_name_to_the_same_station():
    _load()
    by_en = stations.resolve_station("Seoul Station")
    by_ko = stations.resolve_station("서울역")
    assert by_en["latitude"] == pytest.approx(37.5546)
    assert by_ko["latitude"] == pytest.approx(37.5546)
    assert by_en["id"] == by_ko["id"] == "osm-r1"


def test_display_name_prefers_english():
    _load()
    # Even when matched via the Hangul key, the suggestion shows the English name
    # -- and it stays re-resolvable because both names are indexed.
    assert stations.resolve_station("서울역")["name"] == "Seoul Station"


def test_english_typing_matches_hyphenated_name_en_via_folding():
    _load()
    hits = stations.autocomplete_station("Myeongdong")
    assert any(h["name"] == "Myeong-dong Station" for h in hits), hits


def test_one_result_per_station_despite_multiple_index_keys():
    _load()
    hits = stations.autocomplete_station("Seoul Station")
    assert sum(1 for h in hits if h["id"] == "osm-r1") == 1


def test_load_is_idempotent():
    first = _load()
    n_after_first = len(stations._stations)
    second = _load()
    assert first == 3
    assert second == 0
    assert len(stations._stations) == n_after_first


def test_additive_european_csv_search_is_untouched():
    _load()
    hits = stations.autocomplete_station("Frankf")
    assert any("Frankfurt" in h["name"] for h in hits), hits


def test_query_excludes_transit_route_relations():
    # The arrow guard lives in SQL (a fake cursor can't exercise the WHERE), so
    # assert the guard is present. The live test below proves it on real data.
    assert "->" in stations._OSM_STATIONS_SQL
    assert "→" in stations._OSM_STATIONS_SQL  # →


def test_rows_missing_coordinates_are_skipped():
    added = _load([
        {"relation_id": 77, "name": "좌표없음", "name_en": "No Coords",
         "latitude": None, "longitude": None, "country": None, "n_members": 5},
    ])
    assert added == 0
    with pytest.raises(ValueError):
        stations.resolve_station("No Coords")


# --- live smoke test against a real transfr_kr, if one is present ------------

def _kr_db_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(dbname="transfr_kr")
        conn.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _kr_db_available(), reason="transfr_kr database not present")
def test_live_transfr_kr_resolves_korean_stations():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(dbname="transfr_kr", cursor_factory=RealDictCursor)
    try:
        added = stations.load_osm_stations(conn)
    finally:
        conn.close()

    assert added > 1000, added
    # The user's ask: English text resolves a Korean station.
    seoul = stations.resolve_station("Seoul Station")
    assert seoul["latitude"] == pytest.approx(37.55, abs=0.1)
    # And the arrow-route noise never entered the index.
    assert not any("→" in s["name"] for s in stations._stations)
