"""Local station autocomplete from trainline-eu/stations CSV.

Loads the CSV once at import time, filters to suggestable stations,
and provides fast in-memory prefix/substring search.
"""

import csv
import os
from pathlib import Path
from typing import Dict, List, Any
from unicodedata import normalize, combining

_CSV_PATH = Path(__file__).parent / "stations.csv"

_stations: List[Dict[str, Any]] = []
_search_index: List[tuple[str, int]] = []


def _strip_accents(s: str) -> str:
    return "".join(c for c in normalize("NFD", s) if not combining(c))


def _normalize(s: str) -> str:
    return _strip_accents(s).lower()


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
    for _, _, idx in prefix_hits + substring_hits:
        if len(results) >= max_results:
            break
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
