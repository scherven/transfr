import { TrainRoute } from '../types';

export const trainRoutes: TrainRoute[] = [
  // Eurostar routes
  {
    id: 'london-paris',
    from: 'london',
    to: 'paris',
    duration: '2h 16min',
    trainType: 'Eurostar',
  },
  {
    id: 'london-brussels',
    from: 'london',
    to: 'brussels',
    duration: '1h 51min',
    trainType: 'Eurostar',
  },
  {
    id: 'london-amsterdam',
    from: 'london',
    to: 'amsterdam',
    duration: '3h 52min',
    trainType: 'Eurostar',
  },

  // Thalys/TGV routes
  {
    id: 'paris-brussels',
    from: 'paris',
    to: 'brussels',
    duration: '1h 22min',
    trainType: 'Thalys',
  },
  {
    id: 'paris-amsterdam',
    from: 'paris',
    to: 'amsterdam',
    duration: '3h 18min',
    trainType: 'Thalys',
  },
  {
    id: 'brussels-amsterdam',
    from: 'brussels',
    to: 'amsterdam',
    duration: '1h 53min',
    trainType: 'Thalys',
  },
  {
    id: 'paris-barcelona',
    from: 'paris',
    to: 'barcelona',
    duration: '6h 25min',
    trainType: 'TGV',
  },

  // ICE routes (German high-speed)
  {
    id: 'frankfurt-berlin',
    from: 'frankfurt',
    to: 'berlin',
    duration: '4h 0min',
    trainType: 'ICE',
  },
  {
    id: 'frankfurt-munich',
    from: 'frankfurt',
    to: 'munich',
    duration: '3h 10min',
    trainType: 'ICE',
  },
  {
    id: 'berlin-munich',
    from: 'berlin',
    to: 'munich',
    duration: '4h 30min',
    trainType: 'ICE',
  },
  {
    id: 'berlin-amsterdam',
    from: 'berlin',
    to: 'amsterdam',
    duration: '6h 15min',
    trainType: 'ICE',
  },
  {
    id: 'frankfurt-amsterdam',
    from: 'frankfurt',
    to: 'amsterdam',
    duration: '4h 0min',
    trainType: 'ICE',
  },
  {
    id: 'frankfurt-brussels',
    from: 'frankfurt',
    to: 'brussels',
    duration: '2h 58min',
    trainType: 'ICE',
  },
  {
    id: 'frankfurt-paris',
    from: 'frankfurt',
    to: 'paris',
    duration: '3h 50min',
    trainType: 'ICE/TGV',
  },

  // Swiss routes
  {
    id: 'zurich-milan',
    from: 'zurich',
    to: 'milan',
    duration: '3h 20min',
    trainType: 'EuroCity',
  },
  {
    id: 'zurich-munich',
    from: 'zurich',
    to: 'munich',
    duration: '4h 20min',
    trainType: 'EuroCity',
  },
  {
    id: 'zurich-frankfurt',
    from: 'zurich',
    to: 'frankfurt',
    duration: '4h 0min',
    trainType: 'ICE',
  },
  {
    id: 'zurich-paris',
    from: 'zurich',
    to: 'paris',
    duration: '4h 3min',
    trainType: 'TGV',
  },

  // Italian routes
  {
    id: 'milan-rome',
    from: 'milan',
    to: 'rome',
    duration: '2h 55min',
    trainType: 'Frecciarossa',
  },

  // Spanish routes
  {
    id: 'barcelona-madrid',
    from: 'barcelona',
    to: 'madrid',
    duration: '2h 30min',
    trainType: 'AVE',
  },

  // Austrian routes
  {
    id: 'vienna-munich',
    from: 'vienna',
    to: 'munich',
    duration: '4h 0min',
    trainType: 'RailJet',
  },
  {
    id: 'vienna-zurich',
    from: 'vienna',
    to: 'zurich',
    duration: '7h 45min',
    trainType: 'RailJet/EuroCity',
  },
  {
    id: 'vienna-prague',
    from: 'vienna',
    to: 'prague',
    duration: '4h 0min',
    trainType: 'RailJet',
  },

  // Czech routes
  {
    id: 'prague-berlin',
    from: 'prague',
    to: 'berlin',
    duration: '4h 15min',
    trainType: 'EuroCity',
  },

  // Nordic routes
  {
    id: 'copenhagen-berlin',
    from: 'copenhagen',
    to: 'berlin',
    duration: '7h 30min',
    trainType: 'EuroCity',
  },
];
