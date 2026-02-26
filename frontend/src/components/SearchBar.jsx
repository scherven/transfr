import { useState, useRef } from "react";
import { useOuterClick } from "../hooks/useOuterClick.js";
import SearchBarDropDown from "./SearchBarDropDown.jsx";

export default function SearchBar({ label, placeholder, onSelect }) {
  const innerRef = useOuterClick(() => setShowDropdown(false));
  const dropdownRef = useRef();

  const [showDropdown, setShowDropdown] = useState(false);
  const [inputVal, setInputVal] = useState("");
  const [loading, setLoading] = useState(false);

  function handleInput(e) {
    const val = e.target.value;
    if (dropdownRef.current) {
      setLoading(true);
      dropdownRef.current.handleChange(val, () => setLoading(false));
    }
  }

  return (
    <div className="search-bar-wrapper" ref={innerRef}>
      {label && <label className="search-label">{label}</label>}
      <div className="search-input-row">
        <input
          type="text"
          className={`search-input ${loading ? "loading" : ""}`}
          placeholder={placeholder}
          value={inputVal}
          onChange={handleInput}
          autoComplete="off"
        />
      </div>
      <SearchBarDropDown
        ref={dropdownRef}
        showDropdown={showDropdown}
        setShowDropdown={setShowDropdown}
        onSelect={onSelect}
        setInputVal={setInputVal}
      />
    </div>
  );
}
