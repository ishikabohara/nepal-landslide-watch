# Verification record — September 8, 2026

## Colored-grid loading correction

A subsequent user report identified delayed grid appearance. MapLibre's full
`load` event waited for external basemap and elevation tiles; forecast injection
now starts at `style.load`. Only geometry, cell ID and risk category go to the
map worker, reducing that payload from 3.97 MB to 472 KB while preserving all
1,536 cell geometries and colors. The app starts fetching predictions as soon as
the status response arrives, independently of optional model/source metadata.

The loading indicator remains until the forecast source has rendered. Existing
colors and their matching date remain visible during a refresh or failed update;
an unchanged immutable run does not rebuild the map source. Production build and
browser checks passed for initial loading, colored cells, refresh and archive-date
switching. Backend measurements were approximately 37 ms for a cold status read
and 78 ms for a cold forecast read on this workstation; no backend change was
needed for this correction.

The current product was verified against real local artifacts, not generated demonstration inputs.

## Automated checks

- **70 backend tests pass.** Coverage includes input cutoffs and missingness, label validation, GFS accumulation windows, atomic provenance updates, path/coordinate validation, event-field redaction, snapshot selection, worker deadlines/outages/restart behavior, process cleanup and gzip round trips.
- Publication regression cases simulate slow inference and slow staging crossing midnight; no late operational run or pointer is exposed. Concurrent same-run publishers retain the first completed immutable run.
- The production React/TypeScript/Vite build passes. npm reported zero dependency vulnerabilities at installation. MapLibre produces a bundle-size advisory, not a build failure.
- Read-only scientific reevaluation reconstructs **537,600** real test predictions. Every scalar metric matches the saved model card. No retraining was performed by the evaluator.
- All ten existing publications pass timestamp, geometry, feature-hash, rainfall-window-hash, category-count and probability-null checks.

## Running application and browser

The final supervisor serves `http://localhost:8000` with its automatic worker enabled. An actual HTTP check confirmed gzip forecast delivery, 1,536 cells, a healthy non-stale worker heartbeat and a successful rainfall refresh at **2026-09-08 07:16:41 UTC**. That retrieval retained the source's actual latest observation end at **02:00 UTC**; a later retrieval does not invent a newer observation.

Desktop browser checks verified:

- Visible real Nepal hazard cells, terrain relief and basemap attribution.
- Real GeoNames search for Pokhara and a selected containing cell with terrain, rainfall chart, percentile and model contributions.
- Changing from September 8, 2026 to July 1, 2025 updates the same selected cell's real rainfall and estimate.
- Report overlays follow the selected local day: July 1 displays three located reports, not an unrelated catalogue sample.
- Layer controls, archive selection, latest-run selection and data/model drawers.
- Clear percentile, historical-simulation, source-provenance and measured-performance disclosures.
- No browser errors were returned during the final interaction check.

Responsive styles and a mobile details panel are implemented. The available in-app browser retained its desktop viewport after a requested viewport override, so a phone-sized visual verification is **not claimed**. The override was reset afterward.

## Runtime transfer

`scripts/package_runtime.py` exported a 27.9 MB archive containing 38 checksummed files. It holds worker/publication locks during export and validates the current manifest's referenced run. A clean temporary extraction passed all file checksums and served the real model, 1,536 cells, ten runs, eight source records and place/location queries through the API. The 6,729 normalized events in this runtime export contain only allowlisted scientific fields.

## What remains external or prospective

- The Dockerfile, Compose file and Render Blueprint are supplied. Docker is unavailable on this workstation, so no container-build claim is made.
- A public HTTPS deployment has not been created; it needs the owner's hosting account and review of hosting costs.
- No genuinely future-issued operational forecast has yet reached its scheduled issue time during development. Scheduling and deadline behavior are tested, and the real acquisition worker is running. The first eligible scheduled attempt is **September 8 at 23:50 Nepal time**, for the September 9 calendar day, conditional on complete inputs and an awake/running host.
- The displayed runs are historical simulations. Physical warning effectiveness, prospective performance and accurate slope-failure probabilities have not been established.
