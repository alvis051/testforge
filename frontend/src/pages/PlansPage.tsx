import { Link, useParams } from "react-router-dom";

import { usePlanRuns, usePlans } from "../api/queries";
import { ErrorState } from "../components/ErrorState";

function PlanRuns({ planId }: { planId: string }) {
  const { data: runs } = usePlanRuns(planId);
  if (!runs?.length) return <span className="muted">no runs</span>;
  return (
    <>
      {runs.map((run) => (
        <span key={run.id}>
          <Link to={`/runs/${run.id}`}>{run.name ?? run.external_id}</Link>{" "}
        </span>
      ))}
    </>
  );
}

export function PlansPage() {
  const { projectKey = "" } = useParams<{ projectKey: string }>();
  const { data: plans, isLoading, error } = usePlans(projectKey);

  if (error) return <ErrorState error={error} />;

  return (
    <>
      <h2>Plans</h2>
      {isLoading && <p className="muted">Loading…</p>}
      {plans && plans.length === 0 && <p className="muted">No plans yet.</p>}
      {plans && plans.length > 0 && (
        <table data-testid="plans-table">
          <thead>
            <tr>
              <th>Plan</th>
              <th>Milestone</th>
              <th>Environment</th>
              <th>Cases</th>
              <th>Status</th>
              <th>Runs</th>
            </tr>
          </thead>
          <tbody>
            {plans.map((plan) => (
              <tr key={plan.id} data-testid="plan-row">
                <td>{plan.name}</td>
                <td>{plan.milestone ?? <span className="muted">—</span>}</td>
                <td>{plan.environment ?? <span className="muted">—</span>}</td>
                <td>{plan.case_count}</td>
                <td>{plan.status}</td>
                <td>
                  <PlanRuns planId={plan.id} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
