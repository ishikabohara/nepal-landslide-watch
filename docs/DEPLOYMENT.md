# Deploy and operate Nepal Landslide Watch

The application currently runs locally with real data and a fitted model. No public cloud URL has been created. Publishing requires the owner's hosting account and, for continuous operation on Render, a paid service with persistent storage. Never put passwords, tokens or raw report contact information in this repository. The public Git repository contains the website and service source, while the large research workspaces and runtime data stay local.

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

## Render configuration

The repository includes `render.yaml` for a single Docker web service with a persistent disk at `/app/data`. It selects the 1 CPU / 2 GB plan and a 5 GB disk. Review the current plan and displayed cost in your account before creation; there is no assumed budget approval. The worker starts disabled so the first deployment cannot write to an unseeded data directory.

1. Put the reviewed source repository in the Git provider/account you control. Keep `.env`, raw data and generated runtime archives excluded from git.
2. In Render, create a Blueprint from that repository and review its service, plan and disk. Alternatively create a Docker web service with the same settings manually.
3. Confirm `NEPAL_DATA_DIR=/app/data` and `NEPAL_WORKER_ENABLED=1`, disk mount `/app/data`, and health check `/healthz`.
4. Seed the mounted disk with the trusted runtime archive through the host's authenticated shell/transfer facilities. The initial worker setting is `NEPAL_WORKER_ENABLED=0`; verify the data before changing it to `1` and restarting the service.
5. Open the assigned HTTPS URL and perform the acceptance checks below. Confirm that the disk preserves publications after a restart.

Render's filesystem outside the disk is ephemeral; free web services cannot attach persistent disks. Disk-backed services do not support horizontal scaling in this architecture. Consult the provider's current [persistent disk documentation](https://render.com/docs/disks), [Docker deployment instructions](https://render.com/docs/docker), [Blueprint specification](https://render.com/docs/blueprint-spec) and [SSH documentation](https://render.com/docs/ssh). The Blueprint is a deployment package, not evidence that a public deployment has happened.

For a small demonstration, one continuously running instance is sufficient. If usage grows, move forecast artifacts to durable object storage, coordinate one writer and cache the read-only API before adding instances. Do not deploy several independent scheduler replicas onto separate disks.

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
- Restart the service and confirm that old run files and `published/current.json` remain on the disk.
- Monitor `/api/status.worker_health`: `heartbeat_stale`, `errors`, `rainfall`, `forecast_input_readiness`, `last_missed_forecast_target` and `next_forecast_attempt_at`. HTTP liveness alone is not forecast freshness.
- Keep backups of `data/model/`, `data/published/`, provenance and the original research snapshots. Never delete old runs merely to make a stale run appear current.

A failed source refresh retains the last publication and the failure in health metadata. Missing complete rainfall prevents issuance; a missed midnight window cannot be repaired by pretending a retrospective run was issued earlier. The scheduler is not an emergency notification system and sends no public alerts.
