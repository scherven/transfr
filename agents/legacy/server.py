"""Flask API for the React frontend.

Station autocomplete is backed by a local CSV (trainline-eu/stations).
Journey routing is powered by the Transitous (MOTIS 2) API.
"""

import json
import queue
import threading
from datetime import datetime

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

from stations import autocomplete_station
from journeys import search_journeys
from pathfind import init_pool, get_conn, put_conn, compute_path_geometry
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


@app.route("/api/osm-stations")
def api_osm_stations():
    """Autocomplete over OSM station names stored in platform_edges_indexed."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT station_name FROM platform_edges_indexed "
                "WHERE station_name ILIKE %s ORDER BY station_name LIMIT 10",
                (f"%{q}%",),
            )
            results = [{"id": r["station_name"], "name": r["station_name"]} for r in cur.fetchall()]
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        put_conn(conn)


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


@app.route("/api/path/stream")
def api_path_stream():
    """SSE endpoint: streams progress events then emits a final result/error event."""
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

    q: queue.Queue = queue.Queue()

    def progress_cb(msg: str) -> None:
        q.put({"type": "progress", "message": msg})

    def worker() -> None:
        try:
            result = find_path(station, p1, station, p2, progress_cb=progress_cb)
            if result is None:
                q.put({"type": "error", "message": f"No walkable path found between platforms {p1} and {p2} at {station!r}"})
                return
            progress_cb("Fetching node coordinates for map…")
            conn = get_conn()
            try:
                geometry = compute_path_geometry(conn, result)
            finally:
                put_conn(conn)
            walking_time = result.get("walking_time_seconds", 0)
            q.put({"type": "result", "data": {
                "station": station,
                "from_platform": p1,
                "to_platform": p2,
                "walking_time_seconds": walking_time,
                "walking_distance_meters": result.get("walking_distance_meters", 0),
                "feasible": walking_time <= 600,
                "path_type": result.get("type"),
                "path_breakdown": result.get("path_breakdown", []),
                **geometry,
            }})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)  # sentinel

    threading.Thread(target=worker, daemon=True).start()

    def generate():
        while True:
            item = q.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/path")
def api_path():
    """Compute walking path between two platform edges and return full geometry."""
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

    conn = get_conn()
    try:
        geometry = compute_path_geometry(conn, result)
    except Exception as e:
        return jsonify({"error": f"Geometry error: {e}"}), 500
    finally:
        put_conn(conn)

    walking_time = result.get("walking_time_seconds", 0)
    return jsonify({
        "station": station,
        "from_platform": p1,
        "to_platform": p2,
        "walking_time_seconds": walking_time,
        "walking_distance_meters": result.get("walking_distance_meters", 0),
        "feasible": walking_time <= 600,
        "path_type": result.get("type"),
        "path_breakdown": result.get("path_breakdown", []),
        **geometry,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001)
