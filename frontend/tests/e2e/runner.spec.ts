import { expect, test } from "@playwright/test";

test("the seeded run shows its runner job", async ({ page }) => {
  await page.goto("/projects/CHK/runs");
  await page.getByTestId("run-row").first().getByRole("link").first().click();

  const panel = page.getByTestId("job-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("job-status")).toHaveText("succeeded");
  await expect(panel).toContainText("main");
  await expect(page.getByTestId("job-output")).toContainText("4 passed");
});

test("a run that never went through the runner has no job panel", async ({ page }) => {
  await page.goto("/projects/CHK/runs");
  // The five older seeded runs are plan executions, not dispatches.
  await page.getByTestId("run-row").nth(1).getByRole("link").first().click();

  await expect(page.getByTestId("run-status")).toBeVisible();
  await expect(page.getByTestId("job-panel")).toHaveCount(0);
});

test("dispatching a plan queues a run and lands on its page", async ({ page, request }) => {
  // Dispatch creates a run. Doing that against CHK would change the run count and the
  // newest-run row that runs.spec.ts asserts on — and Playwright runs spec files in
  // parallel against one shared seeded database. So this spec builds its own project.
  await request.post("/api/projects", {
    data: {
      key: "DSP",
      name: "Dispatch demo",
      repo_url: "https://example.test/repo.git",
      default_ref: "main",
      test_command: "pytest -q",
    },
  });
  await request.post("/api/projects/DSP/cases", { data: { title: "Automated one" } });
  const planResponse = await request.post("/api/projects/DSP/plans", {
    data: { name: "Smoke", case_keys: ["DSP-1"] },
  });
  expect(planResponse.ok()).toBeTruthy();

  await page.goto("/projects/DSP/plans");
  await expect(page.getByTestId("dispatch-button")).toBeEnabled();
  await page.getByTestId("dispatch-button").click();

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/);
  // No worker runs during E2E, so the run stays queued — the honest state, and proof
  // that dispatch queued the work rather than executing it inline in the request.
  await expect(page.getByTestId("run-status")).toHaveText("queued");
  await expect(page.getByTestId("job-status")).toHaveText("queued");
});

test("a dispatch the backend rejects shows the reason inline", async ({ page, request }) => {
  // Runner config is present, so the button renders — but the plan holds only a manual
  // case, which the backend refuses with 409 plan_has_no_automated_cases. That is the
  // one dispatch failure a user can reach through the UI, and its own project again so
  // the rejected click cannot touch another spec's data.
  await request.post("/api/projects", {
    data: {
      key: "DER",
      name: "Dispatch error demo",
      repo_url: "https://example.test/repo.git",
      default_ref: "main",
      test_command: "pytest -q",
    },
  });
  await request.post("/api/projects/DER/cases", {
    data: { title: "Manual only", execution_type: "manual" },
  });
  await request.post("/api/projects/DER/plans", {
    data: { name: "Manual smoke", case_keys: ["DER-1"] },
  });

  await page.goto("/projects/DER/plans");
  await page.getByTestId("dispatch-button").click();

  // The backend's own message, not a generic "something went wrong": the user needs to
  // know it was the plan's contents that were wrong, not the runner.
  await expect(page.getByTestId("dispatch-error")).toContainText("no automated cases");
  // A rejected dispatch must not navigate — there is no run to navigate to.
  await expect(page).toHaveURL(/\/projects\/DER\/plans$/);
});

test("a project with no runner config cannot dispatch", async ({ page, request }) => {
  await request.post("/api/projects", { data: { key: "NOR", name: "No runner" } });
  await request.post("/api/projects/NOR/cases", { data: { title: "Automated one" } });
  await request.post("/api/projects/NOR/plans", {
    data: { name: "Smoke", case_keys: ["NOR-1"] },
  });

  await page.goto("/projects/NOR/plans");

  await expect(page.getByTestId("dispatch-disabled")).toBeVisible();
  await expect(page.getByTestId("dispatch-button")).toHaveCount(0);
});
