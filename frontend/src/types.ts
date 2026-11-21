import type { Feature, FeatureCollection, Geometry } from 'geojson';

export type RiskLevel = 'low' | 'moderate' | 'high' | 'very_high' | 'unavailable';
export type RainPoint = { time: string; rain_mm: number | null };
export type Driver = { feature: string; label: string; value: number | string | null; contribution: number | null };
export interface CellProperties {
  cell_id: string;
  lat: number;
  lon: number;
  place_name?: string | null;
  score: number | null;
  probability: number | null;
  risk_level: RiskLevel;
  rain_24h_mm: number | null;
  rain_72h_mm: number | null;
  rain_7d_mm: number | null;
  forecast_rain_24h_mm: number | null;
  soil_moisture: number | null;
  elevation_m: number | null;
  slope_deg: number | null;
  input_age_hours: number | null;
  quality_flags: string[];
  drivers: Driver[];
  rainfall_series: RainPoint[];
  input_sources: Record<string, unknown>;
  [key: string]: unknown;
}
export type Cell = Feature<Geometry, CellProperties>;
export type Forecast = FeatureCollection<Geometry, CellProperties> & { manifest: Manifest | null };
export interface Manifest {
  run_id: string;
  issued_at: string;
  valid_from: string;
  valid_to: string;
  generated_at: string;
  mode: 'historical' | 'operational';
  model_version: string;
  target: string;
  grid_resolution_degrees: number;
  score_type: string;
  cell_count: number;
  data_cutoff: string;
  source_times: Record<string, string | null>;
  summary: Record<RiskLevel, number>;
  [key: string]: unknown;
}
export interface Status {
  service: string;
  status: string;
  latest_run: Manifest | null;
  model_available: boolean;
  source_count: number;
}
export interface Place { name: string; lat: number; lon: number; kind: string }
export interface LocationResult {
  cell: Cell | null;
  history: { run_id: string; issued_at: string; valid_from: string; valid_to: string; score: number | null; probability: number | null; risk_level: RiskLevel; mode: string }[];
  rainfall: RainPoint[];
}
export interface EventRecord {
  id?: string | number;
  event_id?: string | number;
  title?: string;
  name?: string;
  date?: string;
  event_date?: string;
  occurred_at?: string;
  lat?: number;
  lon?: number;
  latitude?: number;
  longitude?: number;
  source?: string;
  location_accuracy?: string;
  [key: string]: unknown;
}
export type Source = Record<string, unknown>;
export type ModelCard = Record<string, unknown>;
