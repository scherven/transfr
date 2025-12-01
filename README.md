# 🚆 Transfr - European Train Routes

Can you really make that transfer?

An interactive React application that displays train routes between European cities using Google Maps.

## Features

- **Interactive Map**: View all European cities and train routes on Google Maps
- **City Information**: Click on cities to see station details and available connections
- **Route Details**: See train types (Eurostar, TGV, ICE, etc.) and travel durations
- **City Search**: Find cities by name, country, or station
- **Color-coded Routes**: Different train operators shown in different colors
- **Responsive Design**: Works on desktop and mobile devices

## Cities Included

The map includes major European cities with high-speed rail connections:
- Paris, London, Amsterdam, Brussels
- Berlin, Frankfurt, Munich
- Zurich, Milan, Rome
- Barcelona, Madrid
- Vienna, Prague, Copenhagen

## Getting Started

### Prerequisites

- Node.js 16+ installed
- A Google Maps API key with Maps JavaScript API enabled

### Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   npm install
   ```
3. Create a `.env` file in the project root:
   ```
   REACT_APP_GOOGLE_MAPS_API_KEY=your_api_key_here
   ```
4. Start the development server:
   ```bash
   npm start
   ```

### Getting a Google Maps API Key

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Maps JavaScript API**
4. Create credentials (API key)
5. (Optional) Restrict the API key to your domain for security

## Available Scripts

- `npm start` - Runs the app in development mode
- `npm test` - Launches the test runner
- `npm run build` - Builds for production
- `npm run eject` - Ejects from Create React App

## Technologies Used

- React 19 with TypeScript
- @react-google-maps/api for Google Maps integration
- Create React App for build tooling

## Project Structure

```
src/
├── components/
│   ├── TrainMap.tsx       # Main map component
│   ├── TrainMap.css
│   ├── CityList.tsx       # Sidebar city list
│   └── CityList.css
├── data/
│   ├── cities.ts          # European cities data
│   └── routes.ts          # Train routes data
├── types/
│   └── index.ts           # TypeScript interfaces
├── App.tsx                # Main app component
└── App.css
```

## License

MIT
