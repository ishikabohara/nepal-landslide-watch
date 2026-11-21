.PHONY: setup data terrain train demo test build serve worker evaluate package
setup:
	python3 -m venv .venv
	.venv/bin/python -m pip install -r requirements-lock.txt
	.venv/bin/python scripts/fix_macos_openmp.py
	cd frontend && npm ci
data:
	.venv/bin/python scripts/acquire_data.py --start 2018-01-01 --end 2025-12-31
	.venv/bin/python scripts/acquire_data.py --only recent
	.venv/bin/python scripts/acquire_places.py
terrain:
	.venv/bin/python scripts/build_terrain.py --boundary data/processed/nepal_boundary.geojson
train:
	.venv/bin/python scripts/train_model.py
demo:
	.venv/bin/python scripts/publish_forecast.py 2025-07-01
test:
	.venv/bin/python -m pytest backend/tests -q
	cd frontend && npm run build
build:
	cd frontend && npm run build
serve:
	.venv/bin/python scripts/run_service.py
worker:
	.venv/bin/python scripts/run_worker.py
evaluate:
	.venv/bin/python scripts/evaluate_model.py
package:
	.venv/bin/python scripts/package_runtime.py
