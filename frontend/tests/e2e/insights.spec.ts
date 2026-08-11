import { expect, test } from "@playwright/test";

test("insights is reachable from the nav", async ({ page }) => {
  await page.goto("/projects/CHK/runs");
  await page.getByRole("link", { name: "Insights" }).click();

  await expect(page).toHaveURL(/\/projects\/CHK\/insights$/);
});

test("the self-recovering test is flagged and the broken one is not", async ({ page }) => {
  await page.goto("/projects/CHK/insights");

  const rows = page.getByTestId("flaky-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("CHK-1");

  // CHK-3 fails in the three most recent runs and stays failed — a regression,
  // not flakiness. Flagging it would defeat the whole point of the rule.
  await expect(page.getByTestId("flaky-table")).not.toContainText("CHK-3");
});

test("the flaky test's history is shown, not just a score", async ({ page }) => {
  await page.goto("/projects/CHK/insights");

  const history = page.getByTestId("flaky-row").first().locator(".seq-mark");
  await expect(history).toHaveCount(6);
  await expect(history.first()).toHaveText("P");
});

test("the trend chart plots the seeded runs", async ({ page }) => {
  await page.goto("/projects/CHK/insights");

  const chart = page.getByTestId("trend-chart");
  await expect(chart).toBeVisible();
  await expect(chart.locator(".chart-dot")).toHaveCount(6);
});

test("failure categories include the deliberately uncategorised one", async ({ page }) => {
  await page.goto("/projects/CHK/insights");

  const bars = page.getByTestId("category-bars");
  await expect(bars).toContainText("assertion");
  await expect(bars).toContainText("uncategorized");
});
