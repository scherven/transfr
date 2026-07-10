import { useEffect, useRef } from "react";
import "leaflet/dist/leaflet.css";

export default function PathMap({ polyline, platform1Coords, platform2Coords }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !polyline?.length) return;

    // Dynamically import leaflet to avoid SSR issues
    import("leaflet").then((L) => {
      // Destroy existing map instance before re-creating
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }

      const map = L.map(containerRef.current);
      mapRef.current = map;

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap contributors",
        maxZoom: 22,
      }).addTo(map);

      // Platform 1 edge — blue
      if (platform1Coords?.length > 1) {
        L.polyline(platform1Coords, { color: "#0a5fd0", weight: 6, opacity: 0.9 }).addTo(map);
      }

      // Platform 2 edge — purple
      if (platform2Coords?.length > 1) {
        L.polyline(platform2Coords, { color: "#6f2fe3", weight: 6, opacity: 0.9 }).addTo(map);
      }

      // Walking path — orange, drawn on top
      const pathLine = L.polyline(polyline, { color: "#f5a623", weight: 4, opacity: 1 }).addTo(map);

      // Highlight path nodes as small circles
      for (const [lat, lon] of polyline) {
        L.circleMarker([lat, lon], {
          radius: 4,
          color: "#f5a623",
          fillColor: "#fff",
          fillOpacity: 0.8,
          weight: 2,
        }).addTo(map);
      }

      // Start marker (green) and end marker (red)
      if (polyline.length > 0) {
        L.circleMarker(polyline[0], {
          radius: 8,
          color: "#22c55e",
          fillColor: "#22c55e",
          fillOpacity: 1,
          weight: 2,
        }).addTo(map);
        L.circleMarker(polyline[polyline.length - 1], {
          radius: 8,
          color: "#ef4444",
          fillColor: "#ef4444",
          fillOpacity: 1,
          weight: 2,
        }).addTo(map);
      }

      map.fitBounds(pathLine.getBounds(), { padding: [32, 32] });
    });

    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, [polyline, platform1Coords, platform2Coords]);

  return <div ref={containerRef} className="path-map-container" />;
}
