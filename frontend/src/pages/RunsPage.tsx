import { Link, useParams, useSearchParams } from "react-router-dom";

import { useRuns } from "../api/queries";
import { ErrorState } from "../components/ErrorState";
import { OutcomeBadge } from "../components/OutcomeBadge";

const STATUSES = ["", "running", "completed", "canceled"];
const SOURCES = ["", "ci", "local", "manual", "runner"];

export function RunsPage() {
  const { projectKey = "" } = useParams<{ projectKey: string }>();
  const [params, setParams] = useSearchParams();
  const filters = {
    status: params.get("status") ?? undefined,
    source: params.get("source") ?? undefined,
  };
  const { data: runs, isLoading, error } = useRuns(projectKey, filters);

  function setFilter(name: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  }

  if (error) return <ErrorState error={error} />;

  return (
    <>
      <h2>Runs</h2>
      <div className="panel">
        <label>
          Status{" "}
          <select
            data-testid="filter-status"
            value={filters.status ?? ""}
            onChange={(e) => setFilter("status", e.target.value)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s || "any"}
              </option>
            ))}
          </select>
        </label>{" "}
        <label>
          Source{" "}
          <select
            data-testid="filter-source"
            value={filters.source ?? ""}
            onChange={(e) => setFilter("source", e.target.value)}
          >
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {s || "any"}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {runs && runs.length === 0 && <p className="muted">No runs yet.</p>}
      {runs && runs.length > 0 && (
        <table data-testid="runs-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Source</th>
              <th>Started</th>
              <th>Results</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} data-testid="run-row">
                <td>
                  <Link to={`/runs/${run.id}`}>{run.name ?? run.external_id}</Link>
                  {run.plan_id && <span className="muted"> · plan</span>}
                </td>
                <td>{run.status}</td>
                <td>{run.source}</td>
                <td className="muted">
                  {new Date(run.started_at).toLocaleString()}
                </td>
                <td>
                  {Object.entries(run.by_outcome).length === 0 ? (
                    <span className="muted">none</span>
                  ) : (
                    Object.entries(run.by_outcome)
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([outcome, count]) => (
                        <OutcomeBadge key={outcome} outcome={outcome} count={count} />
                      ))
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
