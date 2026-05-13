"""Flask API for the React frontend.

Station autocomplete is backed by a local CSV (trainline-eu/stations).
Journey routing is powered by the Transitous (MOTIS 2) API.
"""

from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS

from stations import autocomplete_station
from journeys import search_journeys
from pathfind import init_pool
from test import find_path, find_all_edges, DB_CONFIG

init_pool(DB_CONFIG)

app = Flask(__name__)
CORS(app)


@app.route("/api/autocomplete")
def api_autocomplete():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        return jsonify(autocomplete_station(q, max_results=8))
    except Exception as e:
        print(e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/journeys")
def api_journeys():
    origin = request.args.get("origin", "").strip()
    destination = request.args.get("destination", "").strip()
    time_str = request.args.get("time", "").strip()

    if not origin or not destination:
        return jsonify({"error": "origin and destination are required"}), 400

    try:
        dep_time = datetime.fromisoformat(time_str) if time_str else datetime.now()
    except ValueError:
        return jsonify({"error": f"Invalid time format: {time_str!r}"}), 400

    try:
        result = search_journeys(origin, destination, dep_time)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/platforms")
def api_platforms():
    """List all platform edge refs at a station."""
    station = request.args.get("station", "").strip()
    if not station:
        return jsonify({"error": "station is required"}), 400
    try:
        edges = find_all_edges(station)
        refs = sorted({e["edge_ref"] for e in edges if e.get("edge_ref")}, key=lambda x: (len(x), x))
        return jsonify({"station": station, "platforms": refs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transfer")
def api_transfer():
    """Compute walking time between two platform edges at the same station."""
    station = request.args.get("station", "").strip()
    from_platform = request.args.get("from_platform", "").strip()
    to_platform = request.args.get("to_platform", "").strip()

    if not station or not from_platform or not to_platform:
        return jsonify({"error": "station, from_platform, and to_platform are required"}), 400

    try:
        p1 = int(from_platform)
        p2 = int(to_platform)
    except ValueError:
        return jsonify({"error": "from_platform and to_platform must be integers"}), 400

    try:
        result = find_path(station, p1, station, p2)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if result is None:
        return jsonify({"error": f"No walkable path found between platforms {p1} and {p2} at {station!r}"}), 404

    walking_time = result.get("walking_time_seconds", 0)
    return jsonify({
        "station": station,
        "from_platform": p1,
        "to_platform": p2,
        "walking_time_seconds": walking_time,
        "walking_distance_meters": result.get("walking_distance_meters", 0),
        "feasible": walking_time <= 600,  # ≤10 minutes considered feasible
        "path_type": result.get("type"),
        "path_breakdown": result.get("path_breakdown", []),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001)
