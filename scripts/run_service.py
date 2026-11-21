"""Run the API and optional worker with bounded cleanup of owned child processes."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stop_children(children: list, timeout: float = 10.0) -> None:
    """Stop and reap every owned child, with one shared shutdown deadline."""
    deadline = time.monotonic() + timeout
    for child in children:
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass  # Child exited between poll and terminate; still reap it.
    for child in children:
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            child.wait(timeout=5)


def main() -> int:
    try:
        port = int(os.getenv("PORT", "8000"))
    except ValueError as exc:
        raise ValueError("PORT must be an integer from 1 to 65535.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer from 1 to 65535.")
    enabled = os.getenv("NEPAL_WORKER_ENABLED", "0") == "1"
    configured_data = Path(os.getenv("NEPAL_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
    if enabled and configured_data != (ROOT / "data").resolve():
        raise ValueError("Scheduled source adapters use this project's data directory. Mount the durable volume at the project's data/ path, or run the API alone with a custom NEPAL_DATA_DIR.")
    children = []

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", str(port)], cwd=ROOT))
        if enabled:
            children.append(subprocess.Popen([sys.executable, "scripts/run_worker.py"], cwd=ROOT))
        while True:
            for child in children:
                result = child.poll()
                if result is not None:
                    return result or 1
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop_children(children)


if __name__ == "__main__":
    raise SystemExit(main())
