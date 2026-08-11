import { useParams } from "react-router-dom";

import { useFailureCategories, useFlakyCases, useRunTrends } from "../api/queries";
import { ErrorState } from "../components/ErrorState";
import { FlakyTable } from "../components/FlakyTable";

export function InsightsPage() {
  const { projectKey = "" } = useParams<{ projectKey: string }>();
  const flaky = useFlakyCases(projectKey);
  const trends = useRunTrends(projectKey);
  const categories = useFailureCategories(projectKey);

  return (
    <>
      <h2>Insights</h2>

      <section className="panel">
        <h3>Flaky tests</h3>
        {flaky.error && <ErrorState error={flaky.error} />}
        {flaky.isLoading && <p className="muted">Loading…</p>}
        {flaky.data && <FlakyTable cases={flaky.data} />}
      </section>

      <section className="panel">
        <h3>Pass rate over recent runs</h3>
        {trends.error && <ErrorState error={trends.error} />}
        {trends.isLoading && <p className="muted">Loading…</p>}
        {trends.data && <p className="muted">{trends.data.length} runs</p>}
      </section>

      <section className="panel">
        <h3>Failure categories</h3>
        {categories.error && <ErrorState error={categories.error} />}
        {categories.isLoading && <p className="muted">Loading…</p>}
        {categories.data && <p className="muted">{categories.data.length} categories</p>}
      </section>
    </>
  );
}
