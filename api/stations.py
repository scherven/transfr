"""Local station autocomplete.

The base index is the trainline-eu/stations CSV, loaded once at import time
(no DB needed -- this is what lets /stations serve before the DB is up).

For regions the CSV doesn't cover -- notably Korea, where it has zero rows, so
*no* Korean station resolved in any script -- an optional OSM-backed index can be
layered on top at startup (load_osm_stations, gated by TRANSFR_OSM_STATION_INDEX).
It draws names straight from the deployed OSM data (`name` + `name:en`), so every
suggestion is real map truth, and it is purely additive: with the flag off the CSV
path is byte-identical to before and still needs no DB.
"""

import csv
import os
from pathlib import Path
from typing import Dict, List, Any
from unicodedata import normalize, combining

# stations.csv lives at the repo root (this module now lives under api/).
_CSV_PATH = Path(__file__).parent.parent / "stations.csv"

_stations: List[Dict[str, Any]] = []
_search_index: List[tuple[str, int]] = []

# Relation ids already folded into the index by load_osm_stations, so a second
# call (or a retried startup) is idempotent rather than duplicating every station.
_osm_loaded_relation_ids: set[int] = set()


def _strip_accents(s: str) -> str:
    return "".join(c for c in normalize("NFD", s) if not combining(c))


def _normalize(s: str) -> str:
    return _strip_accents(s).lower()


def _fold(s: str) -> str:
    """Normalize, then drop every non-alphanumeric char (spaces, hyphens, dots).

    Unicode-aware: keeps letters/digits of any script (Hangul syllables included),
    so "Myeong-dong Station" folds to "myeongdongstation" -- letting a user who
    types "Myeongdong" match the hyphenated OSM `name:en`. Used only as an extra
    index key for OSM stations; the CSV index and its matching are untouched.
    """
    return "".join(ch for ch in _normalize(s) if ch.isalnum())


def _load_stations() -> None:
    global _stations, _search_index

    with open(_CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if row.get("is_suggestable") != "t":
                continue

            lat = row.get("latitude", "").strip()
            lon = row.get("longitude", "").strip()
            if not lat or not lon:
                continue

            _stations.append({
                "id": row["id"],
                "name": row["name"],
                "slug": row.get("slug", ""),
                "latitude": float(lat),
                "longitude": float(lon),
                "country": row.get("country", ""),
                "db_id": row.get("db_id", "") or None,
                "uic": row.get("uic", "") or None,
                "is_main_station": row.get("is_main_station") == "t",
            })

    _search_index = [(_normalize(s["name"]), i) for i, s in enumerate(_stations)]


_load_stations()


def autocomplete_station(term: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Return stations matching a partial name.

    Prefers prefix matches over substring matches, and main stations
    over non-main stations within each group.
    """
    if not term or not term.strip():
        return []

    q = _normalize(term.strip())

    prefix_hits: list[tuple[bool, str, int]] = []
    substring_hits: list[tuple[bool, str, int]] = []

    for norm_name, idx in _search_index:
        if norm_name.startswith(q):
            s = _stations[idx]
            prefix_hits.append((not s["is_main_station"], norm_name, idx))
        elif q in norm_name:
            s = _stations[idx]
            substring_hits.append((not s["is_main_station"], norm_name, idx))

    prefix_hits.sort()
    substring_hits.sort()

    results = []
    seen: set[int] = set()  # an OSM station is indexed under several keys (name,
    # name:en, folded) and so can hit more than once -- collapse to one result.
    for _, _, idx in prefix_hits + substring_hits:
        if len(results) >= max_results:
            break
        if idx in seen:
            continue
        seen.add(idx)
        s = _stations[idx]
        results.append({
            "id": s["id"],
            "name": s["name"],
            "latitude": s["latitude"],
            "longitude": s["longitude"],
            "country": s["country"],
        })
    return results


def resolve_station(name: str) -> Dict[str, Any]:
    """Look up a station by name, return the best match with full details.

    Raises ValueError if nothing is found.
    """
    q = _normalize(name.strip())

    for norm_name, idx in _search_index:
        if norm_name == q:
            return _stations[idx]

    results = autocomplete_station(name, max_results=1)
    if not results:
        raise ValueError(f"No station found for: {name!r}")

    found_id = results[0]["id"]
    for s in _stations:
        if s["id"] == found_id:
            return s

    return results[0]


# ---------------------------------------------------------------------------
# Optional OSM-backed index (regions the CSV doesn't cover, e.g. Korea)
# ---------------------------------------------------------------------------

# A stop_area whose `name` is really a transit *route*, not a station -- OSM
# writes these as "A -> B" -- must not enter the index. The arrow is the tell
# (real station names never carry it); this deliberately spares legitimate hubs
# like 서울고속버스터미널 / "Seoul Express Bus Terminal".
_OSM_STATIONS_SQL = """
    SELECT sp.relation_id,
           sp.name                 AS name,
           r.tags->>'name:en'      AS name_en,
           sp.lat                  AS latitude,
           sp.lon                  AS longitude,
           sp.country              AS country,
           sp.n_members            AS n_members
    FROM station_points sp
    JOIN osm_relations r ON r.id = sp.relation_id
    WHERE sp.name IS NOT NULL
      AND sp.name !~ '(->|→)'
      AND COALESCE(r.tags->>'name:en', '') !~ '(->|→)'
"""

# n_members at/above this counts a station as a "main" hub for autocomplete
# tie-breaking (surfaces 강남/Gangnam Station over a namesake bus stop). Matches
# the "prominent station" cut used elsewhere; purely a ranking hint.
_OSM_MAIN_STATION_MIN_MEMBERS = 10


def _add_to_index(idx: int, *values: str) -> None:
    """Register `idx` in the search index under the normalized and folded form of
    each given name string. Empty/duplicate keys are skipped."""
    keys = set()
    for v in values:
        if not v:
            continue
        keys.add(_normalize(v))
        keys.add(_fold(v))
    for key in keys:
        if key:
            _search_index.append((key, idx))


def load_osm_stations(conn) -> int:
    """Augment the in-memory index with stations drawn from the deployed OSM DB.

    Indexes each station under both its native `name` (e.g. Hangul "서울역") and
    its `name:en` (e.g. "Seoul Station"), so it resolves whether typed in the
    local script or in English. Idempotent by relation id. Returns the number of
    stations newly added. Purely additive -- the CSV-backed entries are left
    exactly as they were, so European resolution is unchanged.
    """
    with conn.cursor() as cur:
        cur.execute(_OSM_STATIONS_SQL)
        rows = cur.fetchall()

    added = 0
    for row in rows:
        rid = row["relation_id"]
        if rid in _osm_loaded_relation_ids:
            continue
        name = (row["name"] or "").strip()
        name_en = (row["name_en"] or "").strip()
        if not name and not name_en:
            continue
        lat, lon = row["latitude"], row["longitude"]
        if lat is None or lon is None:
            continue

        # Show the English name when we have it (this is the "type in English"
        # win); fall back to the native name. Both are indexed as keys below, so
        # whichever we display is re-resolvable by resolve_station.
        display = name_en or name
        idx = len(_stations)
        _stations.append({
            "id": f"osm-r{rid}",
            "name": display,
            "slug": "",
            "latitude": float(lat),
            "longitude": float(lon),
            "country": row["country"] or "",
            "db_id": None,
            "uic": None,
            "is_main_station": (row["n_members"] or 0) >= _OSM_MAIN_STATION_MIN_MEMBERS,
        })
        _add_to_index(idx, name, name_en)
        _osm_loaded_relation_ids.add(rid)
        added += 1

    return added
