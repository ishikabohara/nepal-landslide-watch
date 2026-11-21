#!/usr/bin/env python3
"""Run the API with a periodically refreshed, public data snapshot."""

from __future__ import annotations

import logging
import os
import threading

from scripts import run_service, sync_public_runtime


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    os.environ["NEPAL_WORKER_ENABLED"] = "0"
    try:
        sync_public_runtime.sync()
    except Exception:
        logging.exception("Initial runtime download failed; the service will retry after startup")
    threading.Thread(target=sync_public_runtime.main, name="runtime-sync", daemon=True).start()
    return run_service.main()


if __name__ == "__main__":
    raise SystemExit(main())
