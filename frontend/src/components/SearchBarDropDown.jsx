import { useState, forwardRef, useImperativeHandle } from "react";

const SearchBarDropDown = forwardRef(function SearchBarDropDown(
  { showDropdown, setShowDropdown, onSelect, setInputVal },
  ref,
) {
  const [stations, setStations] = useState([]);

  useImperativeHandle(ref, () => ({
    async handleChange(value, completion) {
      setInputVal(value);
      if (value.length < 2) {
        setShowDropdown(false);
        completion();
        return;
      }

      try {
        const res = await fetch(
          `/api/autocomplete?q=${encodeURIComponent(value)}`,
        );
        if (res.ok) {
          const data = await res.json();
          setStations(data);
          setShowDropdown(data.length > 0);
        }
      } catch (err) {
        console.error("autocomplete error:", err);
      }
      completion();
    },
  }));

  function handleClick(station) {
    setInputVal(station.name);
    setShowDropdown(false);
    onSelect(station);
  }

  if (!showDropdown || stations.length === 0) return null;

  return (
    <div className="dropdown-menu">
      {stations.map((station) => (
        <button
          key={station.id}
          className="dropdown-item"
          type="button"
          onClick={() => handleClick(station)}
        >
          {station.name}
        </button>
      ))}
    </div>
  );
});

export default SearchBarDropDown;
