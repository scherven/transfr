#!/usr/bin/env python3
"""
One-off exploration script: find good candidate stations for the ground-truth
test suite -- diverse countries, diverse platform-edge tagging density, at
least one likely-disconnected case.

Not part of the application; just a research tool for picking test fixtures.
"""
import sys

from db import connect
from ground_truth import find_platform_edges, find_station_relations
from graph import load_station_ways

CANDIDATE_NAMES = [
    "München Hauptbahnhof",
    "Strasbourg-Ville",
    "Wien Hauptbahnhof",
    "Zürich HB",
    "Amsterdam Centraal",
    "Praha hlavní nádraží",
    "Milano Centrale",
    "Bruxelles-Central",
    "Brussel-Centraal",
    "København H",
    "Warszawa Centralna",
    "Gare de Lyon",
    "Madrid-Puerta de Atocha",
    "Berlin Hauptbahnhof",
    "Frankfurt(Main)Hauptbahnhof",
    "Basel SBB",
    "Roma Termini",
    "Budapest-Keleti",
    "Stockholm Central",
    "Oslo S",
]


def main():
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM osm_relations "
                "WHERE tags->>'public_transport' IN ('stop_area','stop_area_group')"
            )
            print(f"Total stop_area/stop_area_group relations loaded: {cur.fetchone()['n']:,}")

        print(f"\n{'name':40s} {'rel_id':>12s} {'ways':>6s} {'nodes':>7s} {'refs found'}")
        print("-" * 100)
        for name in CANDIDATE_NAMES:
            rel_ids = find_station_relations(conn, name)
            if not rel_ids:
                print(f"{name:40s} {'--':>12s}  (not found)")
                continue
            for rel_id in rel_ids:
                ways, coords = load_station_ways(conn, rel_id)
                refs = sorted(
                    {info["tags"].get("ref") for info in ways.values()
                     if info["tags"].get("railway") == "platform_edge" and info["tags"].get("ref")},
                    key=lambda r: (len(r), r),
                )
                print(f"{name:40s} {rel_id:>12d} {len(ways):>6d} {len(coords):>7d} {refs[:20]}")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
