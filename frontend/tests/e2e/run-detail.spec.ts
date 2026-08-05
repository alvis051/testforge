import { expect, test } from "@playwright/test";

async function openSeededRun(page: import("@playwright/test").Page) {
  await page.goto("/projects/CHK/runs");
  await page.getByTestId("run-row").first().getByRole("link").click();
  await expect(page.getByTestId("run-name")).toContainText("nightly regression");
}

test("failures sort above passes", async ({ page }) => {
  await openSeededRun(page);

  const outcomes = await page.getByTestId("result-row").evaluateAll((rows) =>
    rows.map((row) => row.getAttribute("data-outcome")),
  );

  expect(outcomes[0]).toBe("failed");
  expect(outcomes.at(-1)).toBe("passed");
});

test("a failure's message is readable without leaving the page", async ({ page }) => {
  await openSeededRun(page);
  await page.locator('[data-testid="result-row"][data-outcome="failed"]').click();

  const detail = page.getByTestId("result-detail");
  await expect(detail).toContainText("Retry payment");
  await expect(detail).toContainText("AssertionError");
});

test("plan coverage reports which cases were not executed", async ({ page }) => {
  await openSeededRun(page);

  const coverage = page.getByTestId("plan-coverage");
  // The seeded plan selects tag "checkout" — CHK-1, CHK-2, CHK-3, CHK-8 — and the
  // seeded results cover the first three. CHK-8 is the manual accessibility case,
  // which nothing executed.
  await expect(coverage.getByTestId("coverage-ratio")).toHaveText("3 of 4");
  await expect(coverage.getByTestId("cases-without-result")).toContainText("CHK-8");
});

test("unresolved results get their own callout", async ({ page }) => {
  await openSeededRun(page);

  const callout = page.getByTestId("unresolved-callout");
  await expect(callout).toBeVisible();
  await expect(callout).toContainText("CHK-404");
});

test("an unknown run renders the error state from the API's error code", async ({ page }) => {
  await page.goto("/runs/does-not-exist");

  const error = page.getByTestId("error-state");
  await expect(error).toBeVisible();
  await expect(error).toHaveAttribute("data-code", "run_not_found");
});
