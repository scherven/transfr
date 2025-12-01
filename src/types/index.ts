export interface City {
  id: string;
  name: string;
  country: string;
  position: {
    lat: number;
    lng: number;
  };
  station: string;
}

export interface TrainRoute {
  id: string;
  from: string; // City ID
  to: string; // City ID
  duration: string; // Approximate duration
  trainType: string; // e.g., "TGV", "Eurostar", "ICE"
}
