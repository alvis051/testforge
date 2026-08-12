import type { RunJob } from "../api/types";

export function JobPanel({ job }: { job: RunJob }) {
  return (
    <section className="panel" data-testid="job-panel">
      <h3>Runner job</h3>
      <dl className="job-grid">
        <dt>State</dt>
        <dd data-testid="job-status">{job.status}</dd>
        <dt>Ref</dt>
        <dd>{job.git_ref}</dd>
        <dt>Commit</dt>
        <dd>
          {job.resolved_sha ? (
            <code>{job.resolved_sha.slice(0, 10)}</code>
          ) : (
            <span className="muted">—</span>
          )}
        </dd>
        <dt>Attempts</dt>
        <dd>{job.attempts}</dd>
        <dt>Exit code</dt>
        <dd>{job.exit_code ?? <span className="muted">—</span>}</dd>
      </dl>
      {job.error && (
        <p className="callout" data-testid="job-error">
          {job.error}
        </p>
      )}
      {job.output_tail && <pre data-testid="job-output">{job.output_tail}</pre>}
    </section>
  );
}
