# Local development. `make dev` starts the API (port 8000) and the web app (port 3000) together.
PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn

.PHONY: dev api web test e2e fake-api demo

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

# Recruiter demo on fakes, kept between sessions in data/ (gitignored): the seeded demo plus the test
# profiles scored with real judging (scripts/score_test_profiles.py writes there by default).
demo:
	RIGHTSTAFF_DB=$${RIGHTSTAFF_DEMO_DB:-data/demo.db} RIGHTSTAFF_UPLOADS=data/demo-uploads API_PORT=$${API_PORT:-8000} \
	$(MAKE) fake-api

test:
	$(PY) -m pytest -q

e2e:
	cd frontend && npx playwright test
