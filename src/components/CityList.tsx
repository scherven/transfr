import React, { useState, useMemo } from 'react';
import { City, TrainRoute } from '../types';
import { europeanCities } from '../data/cities';
import { trainRoutes } from '../data/routes';
import './CityList.css';

interface CityListProps {
  onCitySelect: (city: City) => void;
  onRouteSelect: (route: TrainRoute) => void;
  selectedCityId: string | null;
}

const CityList: React.FC<CityListProps> = ({
  onCitySelect,
  onRouteSelect,
  selectedCityId,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [expandedCity, setExpandedCity] = useState<string | null>(null);

  const citiesMap = useMemo(() => {
    const map = new Map<string, City>();
    europeanCities.forEach((city) => map.set(city.id, city));
    return map;
  }, []);

  const filteredCities = useMemo(() => {
    if (!searchTerm) return europeanCities;
    const term = searchTerm.toLowerCase();
    return europeanCities.filter(
      (city) =>
        city.name.toLowerCase().includes(term) ||
        city.country.toLowerCase().includes(term) ||
        city.station.toLowerCase().includes(term)
    );
  }, [searchTerm]);

  const getCityRoutes = (cityId: string): TrainRoute[] => {
    return trainRoutes.filter(
      (route) => route.from === cityId || route.to === cityId
    );
  };

  const handleCityClick = (city: City) => {
    onCitySelect(city);
    setExpandedCity(expandedCity === city.id ? null : city.id);
  };

  return (
    <div className="city-list">
      <div className="search-box">
        <input
          type="text"
          placeholder="Search cities..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>
      <div className="cities">
        {filteredCities.map((city) => {
          const routes = getCityRoutes(city.id);
          const isExpanded = expandedCity === city.id;
          const isSelected = selectedCityId === city.id;

          return (
            <div
              key={city.id}
              className={`city-item ${isSelected ? 'selected' : ''}`}
            >
              <div className="city-header" onClick={() => handleCityClick(city)}>
                <div className="city-info">
                  <span className="city-name">{city.name}</span>
                  <span className="city-country">{city.country}</span>
                </div>
                <span className="connections-count">
                  {routes.length} route{routes.length !== 1 ? 's' : ''}
                </span>
              </div>
              {isExpanded && (
                <div className="city-routes">
                  <p className="station-name">
                    <strong>Station:</strong> {city.station}
                  </p>
                  <div className="routes">
                    {routes.map((route) => {
                      const destinationId =
                        route.from === city.id ? route.to : route.from;
                      const destination = citiesMap.get(destinationId);
                      return (
                        <div
                          key={route.id}
                          className="route-item"
                          onClick={(e) => {
                            e.stopPropagation();
                            onRouteSelect(route);
                          }}
                        >
                          <span className="destination">
                            → {destination?.name}
                          </span>
                          <span className="train-type">{route.trainType}</span>
                          <span className="duration">{route.duration}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default CityList;
