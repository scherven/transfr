import React, { useState, useCallback, useMemo } from 'react';
import {
  GoogleMap,
  useJsApiLoader,
  Marker,
  Polyline,
  InfoWindow,
} from '@react-google-maps/api';
import { City, TrainRoute } from '../types';
import { europeanCities } from '../data/cities';
import { trainRoutes } from '../data/routes';
import './TrainMap.css';

const containerStyle = {
  width: '100%',
  height: '100%',
};

// Center on Europe
const center = {
  lat: 48.5,
  lng: 8.0,
};

const mapOptions = {
  disableDefaultUI: false,
  zoomControl: true,
  mapTypeControl: true,
  streetViewControl: false,
  fullscreenControl: true,
};

// Train type colors
const trainTypeColors: { [key: string]: string } = {
  Eurostar: '#004494',
  Thalys: '#D31F26',
  TGV: '#A1006B',
  ICE: '#EC0016',
  'ICE/TGV': '#EC0016',
  EuroCity: '#2E8B57',
  Frecciarossa: '#C00000',
  AVE: '#662483',
  RailJet: '#C20E1A',
  'RailJet/EuroCity': '#C20E1A',
};

interface TrainMapProps {
  apiKey: string;
}

const TrainMap: React.FC<TrainMapProps> = ({ apiKey }) => {
  const { isLoaded, loadError } = useJsApiLoader({
    googleMapsApiKey: apiKey,
  });

  const [selectedCity, setSelectedCity] = useState<City | null>(null);
  const [selectedRoute, setSelectedRoute] = useState<TrainRoute | null>(null);
  const [highlightedRoutes, setHighlightedRoutes] = useState<string[]>([]);

  const citiesMap = useMemo(() => {
    const map = new Map<string, City>();
    europeanCities.forEach((city) => map.set(city.id, city));
    return map;
  }, []);

  const getCityRoutes = useCallback(
    (cityId: string): TrainRoute[] => {
      return trainRoutes.filter(
        (route) => route.from === cityId || route.to === cityId
      );
    },
    []
  );

  const handleCityClick = useCallback(
    (city: City) => {
      setSelectedCity(city);
      setSelectedRoute(null);
      const routes = getCityRoutes(city.id);
      setHighlightedRoutes(routes.map((r) => r.id));
    },
    [getCityRoutes]
  );

  const handleRouteClick = useCallback(
    (route: TrainRoute) => {
      setSelectedRoute(route);
      setSelectedCity(null);
      setHighlightedRoutes([route.id]);
    },
    []
  );

  const handleMapClick = useCallback(() => {
    setSelectedCity(null);
    setSelectedRoute(null);
    setHighlightedRoutes([]);
  }, []);

  const getRoutePath = useCallback(
    (route: TrainRoute): google.maps.LatLngLiteral[] => {
      const fromCity = citiesMap.get(route.from);
      const toCity = citiesMap.get(route.to);
      if (!fromCity || !toCity) return [];
      return [fromCity.position, toCity.position];
    },
    [citiesMap]
  );

  if (loadError) {
    return (
      <div className="map-error">
        <h2>Error loading Google Maps</h2>
        <p>Please check your API key and try again.</p>
        <p>Error: {loadError.message}</p>
      </div>
    );
  }

  if (!isLoaded) {
    return (
      <div className="map-loading">
        <div className="spinner"></div>
        <p>Loading map...</p>
      </div>
    );
  }

  return (
    <div className="train-map-container">
      <GoogleMap
        mapContainerStyle={containerStyle}
        center={center}
        zoom={5}
        options={mapOptions}
        onClick={handleMapClick}
      >
        {/* Draw train routes */}
        {trainRoutes.map((route) => {
          const path = getRoutePath(route);
          const isHighlighted = highlightedRoutes.includes(route.id);
          const color = trainTypeColors[route.trainType] || '#666666';

          return (
            <Polyline
              key={route.id}
              path={path}
              options={{
                strokeColor: color,
                strokeOpacity: isHighlighted ? 1 : 0.5,
                strokeWeight: isHighlighted ? 5 : 2,
                clickable: true,
                zIndex: isHighlighted ? 10 : 1,
              }}
              onClick={() => handleRouteClick(route)}
            />
          );
        })}

        {/* Draw city markers */}
        {europeanCities.map((city) => (
          <Marker
            key={city.id}
            position={city.position}
            title={city.name}
            onClick={() => handleCityClick(city)}
            icon={{
              url: highlightedRoutes.length > 0 && 
                   getCityRoutes(city.id).some(r => highlightedRoutes.includes(r.id))
                ? 'https://maps.google.com/mapfiles/ms/icons/blue-dot.png'
                : 'https://maps.google.com/mapfiles/ms/icons/red-dot.png',
            }}
          />
        ))}

        {/* City Info Window */}
        {selectedCity && (
          <InfoWindow
            position={selectedCity.position}
            onCloseClick={() => {
              setSelectedCity(null);
              setHighlightedRoutes([]);
            }}
          >
            <div className="info-window">
              <h3>{selectedCity.name}</h3>
              <p className="country">{selectedCity.country}</p>
              <p className="station">
                <strong>Station:</strong> {selectedCity.station}
              </p>
              <div className="routes-list">
                <strong>Direct connections:</strong>
                <ul>
                  {getCityRoutes(selectedCity.id).map((route) => {
                    const destinationId =
                      route.from === selectedCity.id ? route.to : route.from;
                    const destination = citiesMap.get(destinationId);
                    return (
                      <li key={route.id}>
                        {destination?.name} ({route.trainType}) - {route.duration}
                      </li>
                    );
                  })}
                </ul>
              </div>
            </div>
          </InfoWindow>
        )}

        {/* Route Info Window */}
        {selectedRoute && (
          <InfoWindow
            position={{
              lat:
                ((citiesMap.get(selectedRoute.from)?.position.lat || 0) +
                  (citiesMap.get(selectedRoute.to)?.position.lat || 0)) /
                2,
              lng:
                ((citiesMap.get(selectedRoute.from)?.position.lng || 0) +
                  (citiesMap.get(selectedRoute.to)?.position.lng || 0)) /
                2,
            }}
            onCloseClick={() => {
              setSelectedRoute(null);
              setHighlightedRoutes([]);
            }}
          >
            <div className="info-window route-info">
              <h3>
                {citiesMap.get(selectedRoute.from)?.name} ↔{' '}
                {citiesMap.get(selectedRoute.to)?.name}
              </h3>
              <p>
                <strong>Train:</strong> {selectedRoute.trainType}
              </p>
              <p>
                <strong>Duration:</strong> {selectedRoute.duration}
              </p>
            </div>
          </InfoWindow>
        )}
      </GoogleMap>

      {/* Legend */}
      <div className="map-legend">
        <h4>Train Types</h4>
        {Object.entries(trainTypeColors)
          .filter(([key]) => !key.includes('/'))
          .map(([type, color]) => (
            <div key={type} className="legend-item">
              <span
                className="legend-color"
                style={{ backgroundColor: color }}
              ></span>
              <span>{type}</span>
            </div>
          ))}
      </div>
    </div>
  );
};

export default TrainMap;
