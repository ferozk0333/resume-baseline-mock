# Local development. `make dev` starts the API (port 8000) and the web app (port 3000) together.
PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn

.PHONY: dev api web test e2e fake-api

dev:
	@trap 'kill 0' INT TERM; \
	$(UVICORN) backend.main:create_app --factory --reload --port 8000 & \
	(cd frontend && npm run dev -- --port 3000) & \
	wait

api:
	$(UVICORN) backend.main:create_app --factory --reload --port 8000

web:
	cd frontend && npm run dev -- --port 3000

# API on fakes (FakeJudge, scripted extraction and plan) with a throwaway database: no model calls.
# Demo pool: benchmarks shown from 5 candidates (groups of 2), with a "demo pool" badge.
fake-api:
	RIGHTSTAFF_DB=$${RIGHTSTAFF_DB:-/tmp/rightstaff-e2e.db} RIGHTSTAFF_UPLOADS=$${RIGHTSTAFF_UPLOADS:-/tmp/rightstaff-e2e-uploads} \
	RIGHTSTAFF_DEPS_FACTORY=tests.ui_fakes:make_deps PRINT_TIMINGS=0 \
	MIN_POOL=$${MIN_POOL:-5} MIN_GROUP=$${MIN_GROUP:-2} \
	$(UVICORN) backend.main:create_app --factory --port $${API_PORT:-8010}

test:
	$(PY) -m pytest -q

e2e:
	cd frontend && npx playwright test
