import React, { useState, useCallback } from 'react';
import TrainMap from './components/TrainMap';
import CityList from './components/CityList';
import { City, TrainRoute } from './types';
import './App.css';

// Get API key from environment variable
const GOOGLE_MAPS_API_KEY = process.env.REACT_APP_GOOGLE_MAPS_API_KEY || '';

function App() {
  const [selectedCityId, setSelectedCityId] = useState<string | null>(null);
  const [showSidebar, setShowSidebar] = useState(true);

  const handleCitySelect = useCallback((city: City) => {
    setSelectedCityId(city.id);
  }, []);

  const handleRouteSelect = useCallback((route: TrainRoute) => {
    // When a route is selected, we could pan to it or highlight it
    console.log('Route selected:', route);
  }, []);

  if (!GOOGLE_MAPS_API_KEY) {
    return (
      <div className="app-no-key">
        <div className="no-key-content">
          <h1>🚆 European Train Routes</h1>
          <div className="warning-box">
            <h2>Google Maps API Key Required</h2>
            <p>
              To use this application, you need to provide a Google Maps API key.
            </p>
            <p>
              Create a <code>.env</code> file in the project root with:
            </p>
            <pre>REACT_APP_GOOGLE_MAPS_API_KEY=your_api_key_here</pre>
            <p>
              Get an API key from the{' '}
              <a
                href="https://console.cloud.google.com/apis/credentials"
                target="_blank"
                rel="noopener noreferrer"
              >
                Google Cloud Console
              </a>
            </p>
            <p>Make sure to enable the Maps JavaScript API for your project.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🚆 European Train Routes</h1>
        <p className="app-subtitle">
          Explore high-speed train connections across Europe
        </p>
        <button
          className="sidebar-toggle"
          onClick={() => setShowSidebar(!showSidebar)}
        >
          {showSidebar ? '◀ Hide' : '▶ Show'} Cities
        </button>
      </header>
      <main className="app-main">
        <aside className={`app-sidebar ${showSidebar ? 'open' : 'closed'}`}>
          <CityList
            onCitySelect={handleCitySelect}
            onRouteSelect={handleRouteSelect}
            selectedCityId={selectedCityId}
          />
        </aside>
        <div className="app-map">
          <TrainMap apiKey={GOOGLE_MAPS_API_KEY} />
        </div>
      </main>
    </div>
  );
}

export default App;
