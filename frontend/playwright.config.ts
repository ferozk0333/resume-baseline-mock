import { defineConfig } from "@playwright/test";

// Smoke test of the candidate flow against the API on fakes (no model calls, throwaway database).
const API_PORT = 8010;
const WEB_PORT = 3010;

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  workers: 1,                       // both tests share one demo database
  globalSetup: "./e2e/global-setup.ts",
  use: { baseURL: `http://localhost:${WEB_PORT}`, channel: "chrome", screenshot: "only-on-failure" },
  webServer: [
    {
      command: `rm -rf /tmp/rightstaff-e2e.db* /tmp/rightstaff-e2e-uploads && cd .. && PRINT_TIMINGS=0 .venv/bin/python scripts/seed_recruiter_demo.py --db /tmp/rightstaff-e2e.db && EMAIL_PROVIDER=outbox DEMO_EMAIL_TO=demo@example.org JOB_EXTRACT_FAKE=1 API_PORT=${API_PORT} make fake-api`,
      url: `http://localhost:${API_PORT}/baselines`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `NEXT_PUBLIC_API_URL=http://localhost:${API_PORT} npx next dev --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
