import { Link, useParams } from "react-router-dom";

import { useRun, useRunResults } from "../api/queries";
import { ErrorState } from "../components/ErrorState";
import { PlanCoverage } from "../components/PlanCoverage";
import { ResultsTable } from "../components/ResultsTable";

export function RunDetailPage() {
  const { runId = "" } = useParams<{ runId: string }>();
  const { data: run, isLoading, error } = useRun(runId);
  const { data: results } = useRunResults(runId);

  if (error) return <ErrorState error={error} />;
  if (isLoading || !run) return <p className="muted">Loading…</p>;

  const unresolved = (results ?? []).filter((r) => r.test_case_id === null);

  return (
    <>
      <h2 data-testid="run-name">{run.name ?? run.external_id}</h2>
      <p className="muted">
        {run.status} · {run.source} ·{" "}
        {new Date(run.started_at).toLocaleString()} · <Link to="/">all runs</Link>
      </p>

      {run.plan_progress && <PlanCoverage progress={run.plan_progress} />}

      {unresolved.length > 0 && (
        <div className="callout" data-testid="unresolved-callout">
          <strong>{unresolved.length} unresolved result(s).</strong> These tests ran but
          match no test case:{" "}
          {unresolved.map((r) => r.unresolved_case_key ?? r.test_identifier).join(", ")}
        </div>
      )}

      <h3>Results</h3>
      {results && results.length > 0 ? (
        <ResultsTable results={results} />
      ) : (
        <p className="muted">No results recorded.</p>
      )}
    </>
  );
}
