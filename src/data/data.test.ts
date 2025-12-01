import { europeanCities } from './cities';
import { trainRoutes } from './routes';

describe('European Cities Data', () => {
  test('contains cities data', () => {
    expect(europeanCities.length).toBeGreaterThan(0);
  });

  test('all cities have required properties', () => {
    europeanCities.forEach((city) => {
      expect(city).toHaveProperty('id');
      expect(city).toHaveProperty('name');
      expect(city).toHaveProperty('country');
      expect(city).toHaveProperty('position');
      expect(city).toHaveProperty('station');
      expect(city.position).toHaveProperty('lat');
      expect(city.position).toHaveProperty('lng');
    });
  });

  test('city IDs are unique', () => {
    const ids = europeanCities.map((city) => city.id);
    const uniqueIds = new Set(ids);
    expect(uniqueIds.size).toBe(ids.length);
  });

  test('contains major European cities', () => {
    const cityNames = europeanCities.map((city) => city.name);
    expect(cityNames).toContain('Paris');
    expect(cityNames).toContain('London');
    expect(cityNames).toContain('Berlin');
    expect(cityNames).toContain('Amsterdam');
  });
});

describe('Train Routes Data', () => {
  test('contains routes data', () => {
    expect(trainRoutes.length).toBeGreaterThan(0);
  });

  test('all routes have required properties', () => {
    trainRoutes.forEach((route) => {
      expect(route).toHaveProperty('id');
      expect(route).toHaveProperty('from');
      expect(route).toHaveProperty('to');
      expect(route).toHaveProperty('duration');
      expect(route).toHaveProperty('trainType');
    });
  });

  test('route IDs are unique', () => {
    const ids = trainRoutes.map((route) => route.id);
    const uniqueIds = new Set(ids);
    expect(uniqueIds.size).toBe(ids.length);
  });

  test('all route endpoints reference valid cities', () => {
    const cityIds = new Set(europeanCities.map((city) => city.id));
    trainRoutes.forEach((route) => {
      expect(cityIds.has(route.from)).toBe(true);
      expect(cityIds.has(route.to)).toBe(true);
    });
  });

  test('routes include various train types', () => {
    const trainTypes = new Set(trainRoutes.map((route) => route.trainType));
    expect(trainTypes.size).toBeGreaterThan(3);
  });
});
