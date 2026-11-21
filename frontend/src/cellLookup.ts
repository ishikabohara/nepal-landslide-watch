import type { Position } from 'geojson';
import type { Cell, Forecast } from './types';

function inRing(ring: Position[], lon: number, lat: number): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const cross = (lon - xi) * (yj - yi) - (lat - yi) * (xj - xi);
    if (Math.abs(cross) < 1e-10 && lon >= Math.min(xi, xj) - 1e-10 && lon <= Math.max(xi, xj) + 1e-10 && lat >= Math.min(yi, yj) - 1e-10 && lat <= Math.max(yi, yj) + 1e-10) return true;
    if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function inPolygon(rings: Position[][], lon: number, lat: number): boolean {
  return rings.length > 0 && inRing(rings[0], lon, lat) && !rings.slice(1).some(ring => inRing(ring, lon, lat));
}

/** Find a cell in the GeoJSON already downloaded for the colored map. */
export function findCellAt(forecast: Forecast | null, lat: number, lon: number): Cell | undefined {
  if (!forecast || !Number.isFinite(lat) || !Number.isFinite(lon)) return undefined;
  return forecast.features.find(cell => {
    const geometry = cell.geometry;
    if (geometry.type === 'Polygon') return inPolygon(geometry.coordinates, lon, lat);
    if (geometry.type === 'MultiPolygon') return geometry.coordinates.some(polygon => inPolygon(polygon, lon, lat));
    return false;
  });
}
