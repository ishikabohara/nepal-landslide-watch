"""Small HTTP and provenance helpers; no credentials are embedded."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.request
import fcntl
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path


_SOURCE_THREAD_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_json(url: str, *, timeout: int = 90) -> dict:
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "NepalLandslideResearch/0.1"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(1 + attempt)
    raise RuntimeError(f"Could not retrieve {url}: {last_error}") from last_error


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # Unique files prevent simultaneous writers from truncating each other's
        # staging file. A rename exposes either the old or complete new document.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        # Directory sync preserves the rename after a host crash when supported.
        with suppress(OSError):
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_source(root: Path, record: dict) -> None:
    path = root / "data/sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # A lock around the whole read/modify/write transaction avoids lost sources
    # when independent scheduled providers finish at the same time. flock works
    # across macOS/Linux processes; the RLock also coordinates local threads.
    with _SOURCE_THREAD_LOCK, path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            if not isinstance(records, list):
                raise ValueError("Existing source provenance must be an array.")
            records = [item for item in records if item.get("id") != record["id"]]
            records.append(record)
            save_json(path, records)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
