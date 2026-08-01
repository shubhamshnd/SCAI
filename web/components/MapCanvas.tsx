"use client";

/* The one Leaflet surface in the app. Both the location picker and the inline vendor map in an
   answer render through here, so pin styling, tile attribution and fit-to-bounds behaviour stay
   identical in both places.

   Always load this via next/dynamic with ssr:false — Leaflet touches `window` at import time. */

import { useEffect, useMemo } from "react";
import L from "leaflet";
import { Circle, MapContainer, Marker, Polyline, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";

import "leaflet/dist/leaflet.css";

export type MapMarker = {
  id: string;
  label: string;
  lat: number;
  lon: number;
  role?: "destination" | "supplier";
  highlight?: boolean;
  distanceKm?: number | null;
  durationHours?: number | null;
  note?: string;
};

type Props = {
  markers: MapMarker[];
  /** Sourcing radius drawn around the destination marker, in km. */
  radiusKm?: number;
  /** Enables click-to-place and pin dragging; omit for a read-only map. */
  onPick?: (lat: number, lon: number) => void;
  /** Draws a line from the destination to each supplier, so "distance from each" is visible. */
  showSpokes?: boolean;
  height?: number;
};

/* Leaflet's default marker is a bundled PNG whose path breaks under bundlers, and the usual
   workaround is re-pointing it at a CDN. A divIcon avoids the image entirely and lets the pins
   inherit the app's design tokens instead of shipping Leaflet's blue. */
function pinIcon(marker: MapMarker): L.DivIcon {
  const role = marker.role === "destination" ? "destination" : "supplier";
  const modifier = marker.highlight ? " map-pin--highlight" : "";
  return L.divIcon({
    className: "",
    html: `<span class="map-pin map-pin--${role}${modifier}"></span>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

function FitToMarkers({ markers, radiusKm }: { markers: MapMarker[]; radiusKm?: number }) {
  const map = useMap();
  // Re-fit only when the actual positions change; a bare `markers` dep would re-fit on every
  // parent render and fight the user's own panning.
  const signature = markers.map((m) => `${m.lat},${m.lon}`).join("|");

  useEffect(() => {
    if (markers.length === 0) return;
    if (markers.length === 1) {
      const only = markers[0];
      // A lone pin has no extent to fit, so frame it by the radius ring instead when there is
      // one — otherwise the map would slam to max zoom on a single point.
      if (radiusKm) {
        map.fitBounds(
          L.latLng(only.lat, only.lon).toBounds(radiusKm * 2000),
          { padding: [24, 24] }
        );
      } else {
        map.setView([only.lat, only.lon], 11);
      }
      return;
    }
    map.fitBounds(L.latLngBounds(markers.map((m) => [m.lat, m.lon] as [number, number])), {
      padding: [32, 32],
      maxZoom: 12,
    });
  }, [map, signature, radiusKm, markers]);

  return null;
}

function ClickToPlace({ onPick }: { onPick: (lat: number, lon: number) => void }) {
  useMapEvents({
    click: (event) => onPick(event.latlng.lat, event.latlng.lng),
  });
  return null;
}

export default function MapCanvas({ markers, radiusKm, onPick, showSpokes, height = 280 }: Props) {
  const destination = useMemo(() => markers.find((m) => m.role === "destination"), [markers]);
  const center: [number, number] = destination
    ? [destination.lat, destination.lon]
    : markers.length > 0
      ? [markers[0].lat, markers[0].lon]
      : [20.5937, 78.9629]; // India, for an empty picker before anything is chosen

  return (
    <MapContainer
      center={center}
      zoom={5}
      scrollWheelZoom={false}
      style={{ height, width: "100%" }}
      className="map-canvas"
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {onPick && <ClickToPlace onPick={onPick} />}
      <FitToMarkers markers={markers} radiusKm={radiusKm} />

      {destination && radiusKm ? (
        <Circle
          center={[destination.lat, destination.lon]}
          radius={radiusKm * 1000}
          pathOptions={{ color: "#3b6fd4", weight: 1, fillOpacity: 0.06 }}
        />
      ) : null}

      {showSpokes && destination
        ? markers
            .filter((m) => m.role !== "destination")
            .map((m) => (
              <Polyline
                key={`spoke-${m.id}`}
                positions={[
                  [destination.lat, destination.lon],
                  [m.lat, m.lon],
                ]}
                pathOptions={{
                  color: m.highlight ? "#3b6fd4" : "#8b93a7",
                  weight: m.highlight ? 2 : 1,
                  opacity: m.highlight ? 0.9 : 0.45,
                  dashArray: m.highlight ? undefined : "4 4",
                }}
              />
            ))
        : null}

      {markers.map((m) => (
        <Marker
          key={m.id}
          position={[m.lat, m.lon]}
          icon={pinIcon(m)}
          draggable={Boolean(onPick) && m.role === "destination"}
          eventHandlers={
            onPick && m.role === "destination"
              ? {
                  dragend: (event) => {
                    const { lat, lng } = event.target.getLatLng();
                    onPick(lat, lng);
                  },
                }
              : undefined
          }
        >
          <Popup>
            <strong>{m.label}</strong>
            {typeof m.distanceKm === "number" && (
              <>
                <br />
                {m.distanceKm.toFixed(1)} km by road
                {typeof m.durationHours === "number" && ` · ~${m.durationHours.toFixed(1)} h`}
              </>
            )}
            {m.note && (
              <>
                <br />
                {m.note}
              </>
            )}
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
