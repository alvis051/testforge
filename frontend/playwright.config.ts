import { defineConfig } from "@playwright/test";

const PORT = 8123;
const DB = "e2e.db";

/**
 * One server: FastAPI serving the built SPA, exactly as production does.
 * The database is rebuilt and seeded per run so specs can assert on known data.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  reporter: [["list"], ["html", { open: "never" }]],
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "on-first-retry" },
  webServer: {
    command: [
      `rm -f ${DB}`,
      "uv run alembic -c server/alembic.ini upgrade head",
      "uv run tf seed",
      `uv run uvicorn testforge.main:create_app --factory --port ${PORT}`,
    ].join(" && "),
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      TESTFORGE_DATABASE_URL: `sqlite:///./${DB}`,
      TESTFORGE_FRONTEND_DIST: "frontend/dist",
    },
  },
});
