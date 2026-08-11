import type { FlakyCase } from "../api/types";

/** One outcome as a letter plus colour — never colour alone, so the sequence
 *  survives a colourblind reader, a screenshot, and forced-colours mode. */
function OutcomeMark({ outcome }: { outcome: string }) {
  const passed = outcome === "passed";
  return (
    <abbr
      className={`seq-mark ${passed ? "seq-pass" : "seq-fail"}`}
      title={outcome}
    >
      {passed ? "P" : "F"}
    </abbr>
  );
}

export function FlakyTable({ cases }: { cases: FlakyCase[] }) {
  if (cases.length === 0) {
    return (
      <p className="muted">
        No flaky tests detected. A test is flagged once it recovers on its own —
        two or more pass/fail transitions across at least five runs.
      </p>
    );
  }

  return (
    <table data-testid="flaky-table">
      <thead>
        <tr>
          <th>Case</th>
          <th>Title</th>
          <th>Score</th>
          <th>Flips</th>
          <th>Runs</th>
          <th>History (oldest first)</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((flaky) => (
          <tr key={flaky.case_key} data-testid="flaky-row">
            <td>{flaky.case_key}</td>
            <td>{flaky.title}</td>
            <td>{flaky.score.toFixed(2)}</td>
            <td>{flaky.transitions}</td>
            <td>{flaky.sample_size}</td>
            <td className="seq">
              {flaky.sequence.map((outcome, index) => (
                <OutcomeMark key={index} outcome={outcome} />
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
