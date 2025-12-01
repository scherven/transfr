import React from 'react';
import { render, screen } from '@testing-library/react';
import App from './App';

test('renders API key required message when no key is provided', () => {
  render(<App />);
  const titleElement = screen.getByText(/European Train Routes/i);
  expect(titleElement).toBeInTheDocument();
  const apiKeyMessage = screen.getByText(/Google Maps API Key Required/i);
  expect(apiKeyMessage).toBeInTheDocument();
});
