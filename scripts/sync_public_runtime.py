#!/usr/bin/env python3
"""Install the public, redacted runtime snapshot on an ephemeral web host."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("NEPAL_DATA_DIR", ROOT / "data"))
BASE = "https://github.com/ishikabohara/nepal-landslide-watch/releases/download/runtime"
LOG = logging.getLogger("runtime_sync")


def _download(name: str) -> bytes:
    request = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": "NepalLandslideWatch/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def sync() -> bool:
    """Return whether a new, fully checked snapshot was installed."""
    expected = _download("nepal-runtime.tar.gz.sha256").decode().split()[0]
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("Invalid published SHA-256 sidecar")
    DATA.mkdir(parents=True, exist_ok=True)
    digest_path = DATA / ".runtime-archive-sha256"
    if digest_path.exists() and digest_path.read_text().strip() == expected and (DATA / "published/current.json").exists():
        return False
    payload = _download("nepal-runtime.tar.gz")
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("Published runtime archive failed SHA-256 verification")
    with tempfile.TemporaryDirectory(prefix="nepal-runtime-", dir=DATA) as scratch:
        stage = Path(scratch)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                relative = Path(member.name)
                if (not member.isfile() or relative.is_absolute() or ".." in relative.parts
                        or not relative.parts or relative.parts[0] != "data"):
                    raise ValueError(f"Unexpected archive member: {member.name}")
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Missing member payload: {member.name}")
                target.write_bytes(source.read())
        checks = json.loads((stage / "data/runtime_checksums.json").read_text())["files"]
        for name, digest in checks.items():
            if hashlib.sha256((stage / "data" / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Runtime member failed SHA-256 verification: {name}")
        # Current pointer is last: readers keep the old complete run during an update.
        members = sorted((stage / "data").rglob("*"))
        for source in members:
            if not source.is_file() or source.name == "current.json":
                continue
            destination = DATA / source.relative_to(stage / "data")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        current = stage / "data/published/current.json"
        if current.is_file():
            os.replace(current, DATA / "published/current.json")
        digest_path.write_text(expected)
    LOG.info("Installed runtime snapshot %s", expected[:12])
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            sync()
        except Exception:
            LOG.exception("Runtime refresh failed; retaining the last complete publication")
        time.sleep(15 * 60)


if __name__ == "__main__":
    main()
