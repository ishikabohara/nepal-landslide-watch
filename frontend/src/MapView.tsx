import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import type { FeatureCollection, Point } from 'geojson';
import { Compass, Layers3, LocateFixed, Minus, Plus, Mountain, Maximize } from 'lucide-react';
import type { Cell, EventRecord, Forecast } from './types';
import { RISK_COLORS } from './format';

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
const NEPAL_BOUNDS: [[number, number], [number, number]] = [[80.05, 26.3], [88.22, 30.55]];

type Props = {
  forecast: Forecast | null;
  selected: Cell | null;
  events: EventRecord[];
  showEvents: boolean;
  showGrid: boolean;
  showHazard: boolean;
  focus: { lat: number; lon: number; nonce: number } | null;
  onPick: (lat: number, lon: number) => void;
  onToggleGrid: () => void;
  onToggleHazard: () => void;
  /** Ready after this forecast's own source has been drawn, independent of basemap tiles. */
  onForecastReadyChange?: (ready: boolean) => void;
};

/** Only the fields used by map expressions belong in the Web Worker payload. */
export function forecastMapData(forecast: Forecast | null): FeatureCollection {
  if (!forecast) return EMPTY;
  return {
    type: 'FeatureCollection',
    features: forecast.features.map(feature => ({
      type: 'Feature',
      ...(feature.id != null ? { id: feature.id } : {}),
      geometry: feature.geometry,
      properties: { cell_id: feature.properties.cell_id, risk_level: feature.properties.risk_level },
    })),
  };
}

export default function MapView({ forecast, selected, events, showEvents, showGrid, showHazard, focus, onPick, onToggleGrid, onToggleHazard, onForecastReadyChange }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const onPickRef = useRef(onPick);
  const onForecastReadyRef = useRef(onForecastReadyChange);
  const [styleReady, setStyleReady] = useState(false);
  const forecastData = useMemo(() => forecastMapData(forecast), [forecast]);
  const [terrain, setTerrain] = useState(true);
  const [layersOpen, setLayersOpen] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  onPickRef.current = onPick;
  onForecastReadyRef.current = onForecastReadyChange;

  useEffect(() => {
    if (!container.current) return;
    const instance = new maplibregl.Map({
      container: container.current,
      bounds: NEPAL_BOUNDS,
      fitBoundsOptions: { padding: { top: 95, right: 70, bottom: 135, left: 70 } },
      minZoom: 5,
      maxZoom: 14,
      maxBounds: [[77.2, 23.4], [91.2, 33.2]],
      attributionControl: { compact: true },
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            maxzoom: 19,
            attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
          },
          elevation: {
            type: 'raster-dem',
            tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
            tileSize: 256,
            encoding: 'terrarium',
            maxzoom: 12,
            attribution: 'Elevation: <a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md" target="_blank" rel="noopener">Mapzen / AWS Open Data</a>',
          },
          forecast: { type: 'geojson', data: EMPTY },
          selection: { type: 'geojson', data: EMPTY },
          incidents: { type: 'geojson', data: EMPTY },
        },
        layers: [
          { id: 'background', type: 'background', paint: { 'background-color': '#e4e9df' } },
          { id: 'basemap', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.85, 'raster-contrast': -0.08, 'raster-opacity': 0.65 } },
          { id: 'relief', type: 'hillshade', source: 'elevation', paint: { 'hillshade-exaggeration': 0.4, 'hillshade-shadow-color': '#5c7062', 'hillshade-highlight-color': '#f5f4e9', 'hillshade-accent-color': '#b7c5ae' } },
          { id: 'hazard', type: 'fill', source: 'forecast', paint: {
            'fill-color': ['match', ['get', 'risk_level'], 'low', RISK_COLORS.low, 'moderate', RISK_COLORS.moderate, 'high', RISK_COLORS.high, 'very_high', RISK_COLORS.very_high, RISK_COLORS.unavailable],
            'fill-opacity': ['interpolate', ['linear'], ['zoom'], 5, 0.72, 9, 0.51, 12, 0.32],
          } },
          { id: 'grid-lines', type: 'line', source: 'forecast', paint: { 'line-color': '#344b5e', 'line-opacity': ['interpolate', ['linear'], ['zoom'], 5, 0.06, 8, 0.18, 12, 0.45], 'line-width': 0.7 } },
          { id: 'selected-underlay', type: 'line', source: 'selection', paint: { 'line-color': '#fffdf5', 'line-width': 6 } },
          { id: 'selected-outline', type: 'line', source: 'selection', paint: { 'line-color': '#344b5e', 'line-width': 2.6 } },
          { id: 'event-circles', type: 'circle', source: 'incidents', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 3, 10, 6], 'circle-color': '#283e38', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.83 } },
        ],
      },
    });
    map.current = instance;
    instance.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: 'metric' }), 'bottom-left');
    // map.load waits for every source, including remote raster/terrain tiles.
    // style.load means our inline style's sources and layers can already be used.
    instance.on('style.load', () => setStyleReady(true));
    instance.on('click', event => onPickRef.current(event.lngLat.lat, event.lngLat.lng));
    instance.on('mouseenter', 'hazard', () => { instance.getCanvas().style.cursor = 'crosshair'; });
    instance.on('mouseleave', 'hazard', () => { instance.getCanvas().style.cursor = ''; });
    instance.on('error', (event) => {
      if (event.error?.message?.includes('WebGL')) setMapError('Your browser could not initialize the map. Try a browser with WebGL enabled.');
    });
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(container.current);
    return () => { resize.disconnect(); instance.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    onForecastReadyRef.current?.(false);
    if (!styleReady || !map.current) return;
    const instance = map.current;
    const source = instance.getSource('forecast') as maplibregl.GeoJSONSource;
    const hasForecast = forecastData.features.length > 0;
    const afterRender = () => {
      // A render event follows paint. Check only the model source; remote map
      // tiles are optional context and may still be loading or unavailable.
      if (instance.isSourceLoaded('forecast')) {
        instance.off('render', afterRender);
        onForecastReadyRef.current?.(hasForecast);
      }
    };
    instance.on('render', afterRender);
    source.setData(forecastData);
    instance.triggerRepaint();
    return () => { instance.off('render', afterRender); };
  }, [styleReady, forecastData]);
  useEffect(() => {
    if (!styleReady || !map.current) return;
    (map.current.getSource('selection') as maplibregl.GeoJSONSource).setData(selected ? { type: 'FeatureCollection', features: [selected] } : EMPTY);
  }, [styleReady, selected]);
  useEffect(() => {
    if (!styleReady || !map.current) return;
    const features = events.flatMap(event => {
      const lat = Number(event.lat ?? event.latitude);
      const lon = Number(event.lon ?? event.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < 26 || lat > 31 || lon < 80 || lon > 89) return [];
      return [{ type: 'Feature' as const, geometry: { type: 'Point' as const, coordinates: [lon, lat] }, properties: { event_id: String(event.id ?? event.event_id ?? '') } }];
    });
    (map.current.getSource('incidents') as maplibregl.GeoJSONSource).setData({ type: 'FeatureCollection', features } as FeatureCollection<Point>);
  }, [styleReady, events]);
  useEffect(() => {
    if (!styleReady || !map.current) return;
    map.current.setLayoutProperty('event-circles', 'visibility', showEvents ? 'visible' : 'none');
    map.current.setLayoutProperty('grid-lines', 'visibility', showGrid ? 'visible' : 'none');
    map.current.setLayoutProperty('hazard', 'visibility', showHazard ? 'visible' : 'none');
    map.current.setLayoutProperty('relief', 'visibility', terrain ? 'visible' : 'none');
  }, [styleReady, showEvents, showGrid, showHazard, terrain]);
  useEffect(() => {
    if (!focus || !styleReady || !map.current) return;
    map.current.flyTo({ center: [focus.lon, focus.lat], zoom: 9, duration: 900, essential: false });
  }, [focus, styleReady]);

  return <div className="map-canvas-wrap">
    <div ref={container} className="map-canvas" aria-label="Interactive landslide map. Click a grid cell to inspect its data." />
    {mapError && <div className="map-failure">{mapError}</div>}
    <div className="map-controls" aria-label="Map controls">
      <button title="Zoom in" aria-label="Zoom in" onClick={() => map.current?.zoomIn()}><Plus size={18} /></button>
      <button title="Zoom out" aria-label="Zoom out" onClick={() => map.current?.zoomOut()}><Minus size={18} /></button>
      <span className="control-divider" />
      <button title="Show full map" aria-label="Show full map" onClick={() => map.current?.fitBounds(NEPAL_BOUNDS, { padding: { top: 95, right: 70, bottom: 135, left: 70 }, duration: 700 })}><Maximize size={17} /></button>
      <button title="Reset north" aria-label="Reset north" onClick={() => map.current?.resetNorth()}><Compass size={18} /></button>
    </div>
    <div className="map-layer-control">
      {layersOpen && <div className="layer-popover">
        <h4>Map layers</h4>
        <label><span><Layers3 size={16} /> Model estimates</span><input type="checkbox" checked={showHazard} onChange={onToggleHazard} /></label>
        <label><span><LocateFixed size={16} /> Prediction grid</span><input type="checkbox" checked={showGrid} onChange={onToggleGrid} /></label>
        <label><span><Mountain size={16} /> Terrain relief</span><input type="checkbox" checked={terrain} onChange={() => setTerrain(v => !v)} /></label>
        <p>Terrain is geographic context. It does not increase the prediction resolution.</p>
      </div>}
      <button className={layersOpen ? 'map-layer-button active' : 'map-layer-button'} aria-expanded={layersOpen} onClick={() => setLayersOpen(v => !v)}><Layers3 size={17} /> Layers</button>
    </div>
  </div>;
}
