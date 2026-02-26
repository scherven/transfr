export default function JourneyResults({ data, loading }) {
  if (loading) {
    return (
      <div className="results-container">
        <div className="results-loading">Searching journeys&hellip;</div>
      </div>
    );
  }

  if (!data) return null;

  if (data.error) {
    return (
      <div className="results-container">
        <div className="results-error">{data.error}</div>
      </div>
    );
  }

  const journeys = data.journeys || [];
  if (journeys.length === 0) {
    return (
      <div className="results-container">
        <div className="results-empty">No journeys found.</div>
      </div>
    );
  }

  function formatTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function formatDuration(seconds) {
    if (!seconds) return "";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }

  function formatDelay(seconds) {
    if (!seconds || seconds === 0) return null;
    const m = Math.round(seconds / 60);
    return m > 0 ? `+${m}` : `${m}`;
  }

  function PlatformBadge({ actual, planned }) {
    const display = actual || planned;
    if (!display) return null;

    const changed = planned && actual && planned !== actual;
    return (
      <span className={`platform ${changed ? "platform-changed" : ""}`}>
        Pl. {display}
        {changed && (
          <span className="platform-was" title={`Was Pl. ${planned}`}>
            {" "}
            (was {planned})
          </span>
        )}
      </span>
    );
  }

  return (
    <div className="results-container">
      <h2 className="results-heading">
        {data.origin?.name} → {data.destination?.name}
      </h2>
      <div className="journey-list">
        {journeys.map((journey, ji) => (
          <div key={ji} className="journey-card">
            <div className="journey-header">
              <span className="journey-duration">
                {formatDuration(journey.duration_s)}
              </span>
              <span className="journey-changes">
                {journey.num_changes === 0
                  ? "Direct"
                  : `${journey.num_changes} change${journey.num_changes > 1 ? "s" : ""}`}
              </span>
            </div>

            <div className="legs">
              {journey.legs.map((leg, li) => (
                <div
                  key={li}
                  className={`leg ${leg.mode === "walking" ? "leg-walk" : ""}`}
                >
                  <div className="leg-train-name">
                    {leg.mode === "walking" ? (
                      <span className="walk-icon">
                        🚶 Walk{leg.distance_m ? ` (${leg.distance_m}m)` : ""}
                      </span>
                    ) : (
                      <span>{leg.train_name || leg.mode}</span>
                    )}
                    {leg.cancelled && (
                      <span className="cancelled-badge">CANCELLED</span>
                    )}
                  </div>

                  <div className="leg-stops">
                    <div className="leg-stop">
                      <span className="stop-time">
                        {formatTime(leg.departure)}
                        {formatDelay(leg.departure_delay_s) && (
                          <span className="delay">
                            {formatDelay(leg.departure_delay_s)}
                          </span>
                        )}
                      </span>
                      <span className="stop-name">{leg.origin?.name}</span>
                      <PlatformBadge
                        actual={leg.departure_platform}
                        planned={leg.planned_departure_platform}
                      />
                    </div>
                    <div className="leg-arrow">↓</div>
                    <div className="leg-stop">
                      <span className="stop-time">
                        {formatTime(leg.arrival)}
                        {formatDelay(leg.arrival_delay_s) && (
                          <span className="delay">
                            {formatDelay(leg.arrival_delay_s)}
                          </span>
                        )}
                      </span>
                      <span className="stop-name">
                        {leg.destination?.name}
                      </span>
                      <PlatformBadge
                        actual={leg.arrival_platform}
                        planned={leg.planned_arrival_platform}
                      />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
