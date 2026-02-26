"""Flask API wrapping hafas.py for the React frontend."""

from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS

from hafas import autocomplete_station, search_journeys

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


if __name__ == "__main__":
    app.run(debug=True, port=5001)
