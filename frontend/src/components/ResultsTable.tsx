import { Fragment, useState } from "react";

import type { ResultRow } from "../api/types";
import { OutcomeBadge } from "./OutcomeBadge";

/** Failures first — this dashboard exists to answer "what broke?". */
const ORDER: Record<string, number> = {
  failed: 0,
  error: 1,
  blocked: 2,
  skipped: 3,
  passed: 4,
};

function rank(outcome: string): number {
  return ORDER[outcome] ?? 99;
}

function ResultDetail({ result }: { result: ResultRow }) {
  return (
    <tr data-testid="result-detail">
      <td colSpan={4}>
        {result.failure_type && (
          <p className="muted">{result.failure_type}</p>
        )}
        {result.failure_message && <pre>{result.failure_message}</pre>}
        {result.stack_trace && <pre>{result.stack_trace}</pre>}
        {!result.failure_message && !result.stack_trace && (
          <p className="muted">No failure detail recorded.</p>
        )}
      </td>
    </tr>
  );
}

export function ResultsTable({ results }: { results: ResultRow[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const sorted = [...results].sort(
    (a, b) =>
      rank(a.outcome) - rank(b.outcome) ||
      a.test_identifier.localeCompare(b.test_identifier),
  );

  return (
    <table data-testid="results-table">
      <thead>
        <tr>
          <th>Outcome</th>
          <th>Test</th>
          <th>Case</th>
          <th>Duration</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((result) => (
          <Fragment key={result.id}>
            <tr
              data-testid="result-row"
              data-outcome={result.outcome}
              onClick={() => setExpanded(expanded === result.id ? null : result.id)}
              style={{ cursor: "pointer" }}
            >
              <td>
                <OutcomeBadge outcome={result.outcome} />
              </td>
              <td>{result.test_identifier}</td>
              <td className="muted">
                {result.unresolved_case_key ?? (result.test_case_id ? "linked" : "—")}
              </td>
              <td className="muted">
                {result.duration_ms === null ? "—" : `${result.duration_ms} ms`}
              </td>
            </tr>
            {expanded === result.id && <ResultDetail result={result} />}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
