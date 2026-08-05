import type { PlanProgress } from "../api/types";
import { OutcomeBadge } from "./OutcomeBadge";

export function PlanCoverage({ progress }: { progress: PlanProgress }) {
  return (
    <section className="panel" data-testid="plan-coverage">
      <h2>Plan coverage</h2>
      <p>
        <strong data-testid="coverage-ratio">
          {progress.cases_with_result} of {progress.total_cases}
        </strong>{" "}
        cases have a result{" "}
        {Object.entries(progress.by_outcome)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([outcome, count]) => (
            <OutcomeBadge key={outcome} outcome={outcome} count={count} />
          ))}
      </p>
      {progress.cases_without_result.length > 0 && (
        <p className="muted" data-testid="cases-without-result">
          Not executed: {progress.cases_without_result.join(", ")}
        </p>
      )}
    </section>
  );
}
