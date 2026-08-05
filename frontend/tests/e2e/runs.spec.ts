import { expect, test } from "@playwright/test";

test("the landing page redirects to the seeded project's runs", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects\/CHK\/runs$/);
});

test("the seeded run appears with its outcome counts", async ({ page }) => {
  await page.goto("/projects/CHK/runs");

  const rows = page.getByTestId("run-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("nightly regression");
  await expect(rows.first().getByTestId("outcome-passed")).toContainText("4");
  await expect(rows.first().getByTestId("outcome-failed")).toContainText("1");
});

test("filtering by a status with no runs empties the table", async ({ page }) => {
  await page.goto("/projects/CHK/runs");
  await page.getByTestId("filter-status").selectOption("running");

  await expect(page.getByText("No runs yet.")).toBeVisible();
});

test("the plans page lists the seeded plan", async ({ page }) => {
  await page.goto("/projects/CHK/plans");

  const rows = page.getByTestId("plan-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Release 1.0 regression");
  await expect(rows.first()).toContainText("1.0");
});
