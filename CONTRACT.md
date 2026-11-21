# Nepal Landslide Watch — integration contract

All displayed environmental values, events, scores and metrics must originate in real source data or a trained model. Missing values are null. No fabricated demo data. JSON uses UTC ISO timestamps and snake_case.

## Data on disk

`data/processed/grid.geojson`: Nepal grid FeatureCollection, feature id and property `cell_id`.
`data/processed/events.json`: normalized real event array.
`data/processed/places.json`: optional real place array `{name, lat, lon, kind}`.
`data/model/model_card.json`: measured model/research metadata.
`data/sources.json`: source provenance/status array.
`data/published/current.json`: current manifest, atomically replaced after run completion.
`data/published/runs/{run_id}/manifest.json`: immutable manifest.
`data/published/runs/{run_id}/forecast.geojson`: immutable prediction FeatureCollection.

Manifest fields: `run_id`, `issued_at`, `valid_from`, `valid_to`, `generated_at`, `mode` (historical or operational), `model_version`, `target`, `grid_resolution_degrees`, `score_type`, `cell_count`, `data_cutoff`, `source_times`, `summary` (`low`, `moderate`, `high`, `very_high`, `unavailable`). Additional provenance fields allowed.

Forecast feature properties: `cell_id`, `lat`, `lon`, `place_name`, `score` (0..1 model score), `probability` (nullable; only a justified calibrated recorded-event probability), `risk_level` (low, moderate, high, very_high, unavailable), `rain_24h_mm`, `rain_72h_mm`, `rain_7d_mm`, `forecast_rain_24h_mm`, `soil_moisture`, `elevation_m`, `slope_deg`, `input_age_hours`, `quality_flags` (string array), `drivers` (array `{feature, label, value, contribution}`), `rainfall_series` (array `{time, rain_mm}`), `input_sources` (object). Use null rather than invented/missing environmental values. Additional features allowed.

## API

GET `/api/status`: `{service, status, latest_run: manifest|null, model_available, source_count}`
GET `/api/runs`: `{runs: manifest[]}`
GET `/api/forecast?run_id=...`: FeatureCollection, default current
GET `/api/location?lat=...&lon=...&run_id=...`: `{cell: GeoJSONFeature|null, history: [{run_id, issued_at, valid_from, valid_to, score, probability, risk_level, mode}], rainfall: [{time, rain_mm}]}`; outside modeled Nepal returns null, never nearest outside-country guess
GET `/api/search?q=...`: `{results: [{name, lat, lon, kind}]}`; also parse coordinates
GET `/api/model`: model_card JSON (or explicit unavailable status)
GET `/api/sources`: `{sources: [...]}`
GET `/api/events`: `{events: [...]}` (bounded/filtered, no personal contact information)

Empty data must produce clear empty/unavailable UI, never fake values. The interface shows forecast validity and source observation ages separately. Historical runs are conspicuously historical. Model probability is always labeled as a recorded-event target if non-null. Poll status periodically and update only on run changes. API safely serves the built frontend and uses no browser-side secrets.
