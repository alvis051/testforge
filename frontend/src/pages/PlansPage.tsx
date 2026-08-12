import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useDispatchPlan, usePlanRuns, usePlans, useProjects } from "../api/queries";
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

function DispatchButton({
  planId,
  projectKey,
  configured,
}: {
  planId: string;
  projectKey: string;
  configured: boolean;
}) {
  const dispatch = useDispatchPlan(projectKey);
  const navigate = useNavigate();

  if (!configured) {
    return (
      <span
        className="muted"
        data-testid="dispatch-disabled"
        title="Set repo_url and test_command on the project to enable the runner"
      >
        no runner config
      </span>
    );
  }

  return (
    <>
      <button
        data-testid="dispatch-button"
        disabled={dispatch.isPending}
        onClick={() =>
          dispatch.mutate(planId, { onSuccess: (run) => navigate(`/runs/${run.id}`) })
        }
      >
        {dispatch.isPending ? "Dispatching…" : "Run on runner"}
      </button>
      {dispatch.isError && (
        <p data-testid="dispatch-error" style={{ color: "var(--fail)", margin: "0.25rem 0 0" }}>
          {dispatch.error instanceof ApiError
            ? dispatch.error.message
            : "Dispatch failed. Try again."}
        </p>
      )}
    </>
  );
}

export function PlansPage() {
  const { projectKey = "" } = useParams<{ projectKey: string }>();
  const { data: plans, isLoading, error } = usePlans(projectKey);
  const { data: projects } = useProjects();

  if (error) return <ErrorState error={error} />;

  const project = projects?.find((p) => p.key === projectKey);
  const configured = Boolean(project?.repo_url && project?.test_command);

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
              <th>Runner</th>
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
                <td>
                  <DispatchButton
                    planId={plan.id}
                    projectKey={projectKey}
                    configured={configured}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
