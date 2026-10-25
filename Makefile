# Local development. `make dev` starts the API (port 8000) and the web app (port 3000) together.
PY := .venv/bin/python
UVICORN := .venv/bin/uvicorn

.PHONY: dev api web test lint e2e fake-api demo live archive reseed reset-db prod-up prod-down

dev:
	@trap 'kill 0' INT TERM; \
	$(UVICORN) api.app:create_app --factory --reload --port 8000 & \
	(cd frontend && npm run dev -- --port 3000) & \
	wait

api:
	$(UVICORN) api.app:create_app --factory --reload --port 8000

web:
	cd frontend && npm run dev -- --port 3000

# API on fakes (FakeJudge, scripted extraction and plan) with a throwaway database: no model calls.
# Demo pool: benchmarks shown from 5 candidates (groups of 2), with a "demo pool" badge.
fake-api:
	RIGHTSTAFF_DB=$${RIGHTSTAFF_DB:-/tmp/rightstaff-e2e.db} RIGHTSTAFF_UPLOADS=$${RIGHTSTAFF_UPLOADS:-/tmp/rightstaff-e2e-uploads} \
	RIGHTSTAFF_DEPS_FACTORY=tests.ui_fakes:make_deps PRINT_TIMINGS=0 \
	MIN_POOL=$${MIN_POOL:-5} MIN_GROUP=$${MIN_GROUP:-2} \
	$(UVICORN) api.app:create_app --factory --port $${API_PORT:-8010}

# Recruiter demo on fakes, kept between sessions in data/ (gitignored): the seeded demo plus the test
# profiles scored with real judging (scripts/score_test_profiles.py writes there by default).
demo:
	RIGHTSTAFF_DB=$${RIGHTSTAFF_DEMO_DB:-data/demo.db} RIGHTSTAFF_UPLOADS=data/demo-uploads API_PORT=$${API_PORT:-8000} \
	$(MAKE) fake-api

# Real processing of uploads on the free tier: Jev judges; extraction, fallback judging, complexity and the plan go
# along their routes (Gemini, overflow to other free models; never Claude). LIVE=1 allows real provider calls:
# every other command (dev, api, fake-api, e2e, tests) refuses them. Same database as the demo (data/demo.db), so the recruiter jobs stay and real runs join the
# comparison pools (demo pool: 5 candidates, groups of 2). Stop `make demo` first: both serve port 8000.
live:
	LIVE=1 LLM_PROVIDER=gemini RIGHTSTAFF_DB=$${RIGHTSTAFF_DEMO_DB:-data/demo.db} MIN_POOL=$${MIN_POOL:-5} MIN_GROUP=$${MIN_GROUP:-2} \
	$(UVICORN) api.app:create_app --factory --port 8000

test:
	$(PY) -m pytest -q

lint:
	uvx ruff check .
	cd frontend && npx eslint . && npx tsc --noEmit

e2e:
	cd frontend && npx playwright test

# --- Prototype deployment (docs/deploy.md) ---------------------------------------------------------------------------
# The judgment archive: saved judgments of the synthetic demo profiles only (copy it to the server's data/).
archive:
	$(PY) scripts/judgment_archive.py export --db $${RIGHTSTAFF_DEMO_DB:-data/demo.db} --out data/judgment-archive.db
	$(PY) scripts/judgment_archive.py check --archive data/judgment-archive.db

# A clean demo database from the archive: synthetic profiles and demo jobs only; no model calls.
# On the server it runs inside the API container (see prod-reseed in docs/deploy.md).
reseed:
	$(PY) scripts/reseed.py --db $${RIGHTSTAFF_DB:-data/demo.db} --archive data/judgment-archive.db --force

# An empty database at the current schema with the seed baselines only (no profiles, jobs or runs); no model calls.
# The old file is moved aside to <db>.before-reset.
reset-db:
	$(PY) scripts/reset_db.py --db $${RIGHTSTAFF_DB:-data/demo.db} --force

prod-up:
	docker compose -f docker-compose.prod.yml up -d --build

prod-down:
	docker compose -f docker-compose.prod.yml down
