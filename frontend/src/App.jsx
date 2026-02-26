import { useState } from "react";
import Background from "./components/Background.jsx";
import SearchBar from "./components/SearchBar.jsx";
import JourneyResults from "./components/JourneyResults.jsx";
import "./App.css";

export default function App() {
  const [origin, setOrigin] = useState(null);
  const [destination, setDestination] = useState(null);
  const [departureTime, setDepartureTime] = useState(
    toLocalISO(new Date()),
  );
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);

  function toLocalISO(d) {
    const off = d.getTimezoneOffset();
    const local = new Date(d.getTime() - off * 60000);
    return local.toISOString().slice(0, 16);
  }

  async function handleSearch() {
    if (!origin || !destination) return;
    setLoading(true);
    setResults(null);

    const params = new URLSearchParams({
      origin: origin.name,
      destination: destination.name,
      time: new Date(departureTime).toISOString(),
    });

    try {
      const res = await fetch(`/api/journeys?${params}`);
      const data = await res.json();
      setResults(data);
    } catch (err) {
      setResults({ error: err.message });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <Background />

      <div className="overlay">
        <header className="hero">
          <h1 className="title">transfr</h1>
          <p className="subtitle">can you really make that transfer?</p>
        </header>

        <div className="search-panel">
          <div className="search-fields">
            <SearchBar
              label="From"
              placeholder="Departure station"
              onSelect={setOrigin}
            />
            <SearchBar
              label="To"
              placeholder="Arrival station"
              onSelect={setDestination}
            />
            <div className="datetime-wrapper">
              <label className="search-label">When</label>
              <input
                type="datetime-local"
                className="search-input datetime-input"
                value={departureTime}
                onChange={(e) => setDepartureTime(e.target.value)}
              />
            </div>
          </div>
          <button
            className="search-button"
            onClick={handleSearch}
            disabled={!origin || !destination || loading}
          >
            {loading ? "Searching…" : "Find journeys"}
          </button>
        </div>

        <JourneyResults data={results} loading={loading} />
      </div>
    </div>
  );
}
