import { useState, useRef, useEffect } from "react";
import SearchBar from "../components/SearchBar.jsx";
import PathMap from "../components/PathMap.jsx";

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s.toString().padStart(2, "0")}s` : `${s}s`;
}

export default function PlatformPath() {
  const [station, setStation] = useState(null);
  const [fromPlatform, setFromPlatform] = useState("");
  const [toPlatform, setToPlatform] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const esRef = useRef(null);
  const logEndRef = useRef(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [progress]);

  function handleFind() {
    if (!station || !fromPlatform || !toPlatform) return;

    // Close any in-flight request
    esRef.current?.close();

    setLoading(true);
    setProgress([]);
    setResult(null);
    setError(null);

    const params = new URLSearchParams({
      station: station.name,
      from_platform: fromPlatform,
      to_platform: toPlatform,
    });

    const es = new EventSource(`/api/path/stream?${params}`);
    esRef.current = es;

    es.onmessage = (e) => {
      const event = JSON.parse(e.data);
      if (event.type === "progress") {
        setProgress((prev) => [...prev, event.message]);
      } else if (event.type === "result") {
        setResult(event.data);
        setLoading(false);
        es.close();
      } else if (event.type === "error") {
        setError(event.message);
        setLoading(false);
        es.close();
      }
    };

    es.onerror = () => {
      setError("Connection to server lost");
      setLoading(false);
      es.close();
    };
  }

  const canSearch = station && fromPlatform && toPlatform && !loading;

  return (
    <div className="path-page">
      <div className="search-panel">
        <div className="search-fields">
          <SearchBar
            label="Station"
            placeholder="Station name"
            onSelect={setStation}
            apiUrl="/api/osm-stations"
          />
          <div className="platform-input-wrapper">
            <label className="search-label">From platform</label>
            <input
              type="number"
              className="search-input"
              placeholder="e.g. 1"
              min="1"
              value={fromPlatform}
              onChange={(e) => setFromPlatform(e.target.value)}
            />
          </div>
          <div className="platform-input-wrapper">
            <label className="search-label">To platform</label>
            <input
              type="number"
              className="search-input"
              placeholder="e.g. 7"
              min="1"
              value={toPlatform}
              onChange={(e) => setToPlatform(e.target.value)}
            />
          </div>
        </div>
        <button
          className="search-button"
          onClick={handleFind}
          disabled={!canSearch}
        >
          {loading ? "Finding path…" : "Find path"}
        </button>
      </div>

      {(loading || progress.length > 0) && (
        <div className="progress-log">
          {progress.map((msg, i) => (
            <div
              key={i}
              className={`progress-line${i === progress.length - 1 && loading ? " progress-line--active" : ""}`}
            >
              <span className="progress-tick">✓</span>
              {msg}
            </div>
          ))}
          {loading && (
            <div className="progress-line progress-line--pending">
              <span className="progress-spinner" />
              Working…
            </div>
          )}
          <div ref={logEndRef} />
        </div>
      )}

      {error && <p className="results-error">{error}</p>}

      {result && (
        <>
          <div className="path-summary">
            <div className="path-summary-row">
              <span className="path-summary-label">Walk time</span>
              <span className="path-summary-value">
                {formatTime(result.walking_time_seconds)}
              </span>
            </div>
            <div className="path-summary-row">
              <span className="path-summary-label">Distance</span>
              <span className="path-summary-value">
                {Math.round(result.walking_distance_meters)} m
              </span>
            </div>
            <div className="path-summary-row">
              <span className="path-summary-label">Feasible</span>
              <span
                className={`path-summary-value ${result.feasible ? "feasible-yes" : "feasible-no"}`}
              >
                {result.feasible ? "Yes" : "No (>10 min)"}
              </span>
            </div>
            {result.path_breakdown?.length > 0 && (
              <div className="path-breakdown">
                {result.path_breakdown.map((seg, i) => (
                  <span key={i} className="path-segment-chip">
                    {seg.type} · {seg.distance_m}m
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="path-legend">
            <span className="legend-item legend-p1">Platform {result.from_platform}</span>
            <span className="legend-item legend-path">Walking path</span>
            <span className="legend-item legend-p2">Platform {result.to_platform}</span>
          </div>

          <PathMap
            polyline={result.polyline}
            platform1Coords={result.platform_1_coords}
            platform2Coords={result.platform_2_coords}
          />
        </>
      )}
    </div>
  );
}
