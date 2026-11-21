# Deploy and operate Nepal Landslide Watch

The application runs locally with real data and a fitted model. The free public deployment uses a Render web service and a GitHub Release asset for the redacted runtime snapshot. Never put passwords, tokens or raw report contact information in this repository. The Git repository contains source code; the public release contains only the packaged runtime data.

## Local server

From the existing project root:

```sh
make build
NEPAL_WORKER_ENABLED=1 make serve
```

Open `http://localhost:8000`. The supervisor starts the API and one worker and stops both when interrupted. The worker refreshes rainfall every 30 minutes and checks issuance every 30 seconds. Keep this machine awake with internet connectivity; it is not a cloud deployment.

## Docker on a server

The multi-stage Dockerfile builds the React application and installs the locked Python environment. It includes Linux OpenMP and ecCodes libraries. It deliberately excludes data and secrets from the image.

Copy the repository and its runtime data to a Linux host with Docker Compose, then run:

```sh
docker compose up --build -d
docker compose logs -f web
```

Compose mounts `./data` at `/app/data` and starts the scheduler. Put a TLS reverse proxy or the hosting provider's HTTPS routing in front of port 8000 for public access. Keep only one service instance with this file-based storage design. The Python service and frontend were tested locally; **the container image has not been built in this workspace because Docker is unavailable here**. Validate it on the target Docker host before treating deployment as complete.

## Transfer the fitted runtime

You can reproduce training on the server, but transferring the already measured model avoids unnecessary retraining and preserves its identity:

```sh
make package
```

This produces `artifacts/runtime/nepal-runtime.tar.gz` and its SHA-256 sidecar. The archive contains the trained model, full daily rainfall feature archive, terrain grid, places, sanitized scientific event fields, source metadata and published runs. It excludes raw downloads, credentials, contacts, report titles and free-form descriptions. A checksum manifest records every archived file. The redacted event file intentionally differs from the original local training-source hash; no trained parameter is changed.

Transfer the archive over your authenticated host/SSH connection. Verify the sidecar checksum on the destination before extraction. Extract this **trusted project-generated archive** from the deployment root (`/app` for the provided Docker image), so its `data/` content lands on the persistent volume:

```sh
sha256sum -c nepal-runtime.tar.gz.sha256
tar -xzf nepal-runtime.tar.gz -C /app
```

On macOS the checksum equivalent is `shasum -a 256 -c nepal-runtime.tar.gz.sha256`. Do not extract arbitrary archives or load third-party joblib files; the model artifact is executable serialization. Stop the service worker while replacing a complete dataset, then restart. The server must be seeded **before** its first actual forecast; an empty deployment serves an honest awaiting-data state.

The archive can also seed `data/` on a new local checkout by extracting it at that checkout's root. It does not include historical raw satellite tiles or original unredacted reports needed for an exact acquisition audit; retain those in the research backup.

## Free Render and GitHub deployment

`render.yaml` requests one **Free** Docker web service, without a disk or billing plan. The web service runs the API and downloads the latest checksummed runtime archive from the repository's public `runtime` GitHub Release at startup. While awake, it checks for a new archive every 15 minutes. It does not run the weather worker.

The GitHub Actions workflow `.github/workflows/publish-runtime.yml` runs daily at 23:18 Nepal time, restores the prior archive, refreshes recent rainfall, and attempts prospective issuance in the project's 23:50–00:00 window. It publishes a new redacted archive only after the update job completes. The release asset is public; never add raw reports, contacts, secrets, or private research data to the package allowlist. A failed source refresh or missed issuance keeps the last published map and must not be described as a current forecast.

1. Create the initial `runtime` GitHub Release with the locally checked `nepal-runtime.tar.gz` and `.sha256` assets. Preserve the tag; scheduled jobs replace only its assets.
2. Connect the public GitHub repository to Render and create a Blueprint from `render.yaml`. Verify **Free** is selected and **no disk or payment method** is requested.
3. Open the assigned HTTPS URL; check `/api/status`, the grid, search and location details. A manual GitHub workflow run can refresh the input archive immediately. Its default `refresh_only` option does not issue a prospective map outside the nightly window.
4. Monitor GitHub Actions and the `/api/status` manifest. GitHub schedules may be delayed or skipped; the worker never backdates a missed issuance. Public repositories may have scheduled workflows disabled after prolonged inactivity.

Render Free spins down after 15 minutes without traffic and can take about a minute to wake. Its filesystem is ephemeral, so each cold start re-downloads the public archive. The initial September 2026 simulation remains visibly historical until a fresh prospective run succeeds. This is a student research demonstration, not a guaranteed continuous or emergency service. See the provider's [free service limitations](https://render.com/docs/free) and [Blueprint reference](https://render.com/docs/blueprint-spec).

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8000` | HTTP port |
| `NEPAL_WORKER_ENABLED` | `0` | Set to `1` to start the worker with the API |
| `NEPAL_DATA_DIR` | Project `data/` | Read-only API data location; scheduled adapters require the project's own `data/` path |
| `NEPAL_FRONTEND_DIR` | `frontend/dist` | Built frontend location |
| `NEPAL_CORS_ORIGINS` | Local Vite origins | Comma-separated allowed cross-origin clients; same-origin production needs no extra client origin |
| `VITE_API_BASE` | `/api` | Optional frontend build-time API base |

No Earthdata account, commercial map token or proprietary weather credential is required for the selected inputs. Public sources can still be delayed or unavailable.

## Acceptance and operations

- `/healthz` returns 200; `/api/status` names the actual current model and includes a recent worker heartbeat.
- A visible Nepal layer contains 1,536 cells; location search and click inspection return real cell inputs.
- A historical date displays its actual target interval. Its manifest retains `mode=historical`; it must not be presented as a forecast genuinely issued then.
- Source retrieval time and observation time are distinct. No soil-moisture values appear without a source.
- The nightly operational manifest has `mode=operational`, a real `generated_at`/`issued_at` before `valid_from`, the required rainfall cutoff and hashes.
- Restart the service and confirm that it reloads the latest published archive from GitHub.
- Monitor the GitHub Actions run and `/api/status.latest_run`. The web server does not run a local worker, so its heartbeat does not indicate publication freshness.
- Keep backups of `data/model/`, `data/published/`, provenance and the original research snapshots. Never delete old runs merely to make a stale run appear current.

A failed source refresh retains the last publication and the failure in health metadata. Missing complete rainfall prevents issuance; a missed midnight window cannot be repaired by pretending a retrospective run was issued earlier. The scheduler is not an emergency notification system and sends no public alerts.
