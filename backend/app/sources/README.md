# Input acquisition

Run commands from the repository root with the installed virtual environment:

```sh
.venv/bin/python scripts/acquire_data.py --only boundary
.venv/bin/python scripts/acquire_data.py --only events
.venv/bin/python scripts/acquire_data.py --only rain --start 2022-01-01 --end 2025-12-31
.venv/bin/python scripts/acquire_data.py --only recent
```

The first three commands prepare research inputs. The `recent` command refreshes seventeen days of rainfall and atomically merges its complete daily periods into the existing archive. It preserves historical days and leaves gaps absent; it does not retrain or publish a model. Do not schedule the full historical acquisition as a current-input refresh. In Python, `acquire_recent_rainfall(..., merge_recent=False)` acquires only the recent strip.

## Rainfall arrays

`data/processed/rainfall_daily.npz` contains:

| Key | Meaning |
| --- | --- |
| `days` | Strings `YYYY-MM-DD` naming the **UTC date of the interval end at 12:00** |
| `latitudes` | Cell centers, descending north to south |
| `longitudes` | Cell centers, ascending west to east |
| `rain_mm` | Float32 array `(day, latitude, longitude)`; sum over `[previous day 12:00 UTC, named day 12:00 UTC)` |
| `sample_count` | Number of finite half-hour rates contributing to each value; exactly 48 required |

For a prediction issued at local Nepal midnight on date D (UTC D−1 18:15), the newest permitted daily rainfall interval is the row labeled **D−1**, ending at UTC D−1 12:00. This is a 6-hour-15-minute gap. Any missing half-hour makes that grid cell's daily accumulation NaN. Missing values are never zero-filled.

The public replica exposes `precipitation_surface` in `kg m-2 s-1`, equivalent to mm/s. Multiply each half-hour mean rate by **1800 seconds**, then sum. `rainfall_recent_halfhour.npz` contains `times` (UTC interval starts), the same coordinates, and `rain_mm` (each half-hour accumulation). Seventeen days ensure at least fourteen complete noon-to-noon intervals even when the first and last dates are partial. Daily merging takes a cross-process file lock and uses an atomic replacement with a unique temporary filename.

`rainfall_provenance.json` records the exact Icechunk snapshot, source and destination units, aggregation interval, latest observation time, SHA256, and version caveats. Raw, cropped half-hour chunks remain under `data/raw/imerg_nepal_chunks/`. `--snapshot-id` pins a historical rerun to the recorded snapshot while that repository retains it. `--output-stem` writes a separate daily/provenance file for an additional interval.

The NASA originals require Earthdata authentication, but [dynamical.org's NASA IMERG Early replica](https://dynamical.org/catalog/nasa-imerg-analysis-early/) supports verified anonymous S3 reads. Use Icechunk 2 and native `s3_storage(anonymous=True)`; the HTTPS transport was substantially slower in this environment. This replica documents additional rounding error below 0.5%. Collection 07 contains V07B before March 4, 2026, and V07C afterward. A retrospectively processed archive is not an original-as-issued forecast replay. Preserve the deployment version change and observed delivery lag in evaluation.

## Labels and boundary

BIPAD hazard 17 is the landslide inventory. Its API `count` is a sentinel; acquisition exhausts pagination and counts actual unique incident IDs. Most triggers are unconfirmed, and reporting is delayed and incomplete. Date precision is conservatively one day. NASA COOLR adds explicitly reported trigger and location-accuracy fields. Raw copies retain necessary event fields without personal descriptions or contact information. Cross-source duplicates remain in `events.json` for explicit model-stage deduplication; an absence of a report is not a confirmed non-event.

`nepal_boundary.geojson` comes from geoBoundaries' public Nepal ADM0 simplified geometry. Rainfall is downloaded for a small national envelope; downstream modeling must mask cells to the national boundary.
