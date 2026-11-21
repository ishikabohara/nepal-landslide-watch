FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libeccodes0 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /root/.ssh && chmod 0700 /root/.ssh
WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY --from=frontend /build/dist frontend/dist
ENV PORT=8000 NEPAL_DATA_DIR=/app/data PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "scripts/run_service.py"]
