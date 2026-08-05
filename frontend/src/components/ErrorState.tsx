import { ApiError } from "../api/client";

const MESSAGES: Record<string, string> = {
  run_not_found: "That run does not exist.",
  plan_not_found: "That plan does not exist.",
  project_not_found: "That project does not exist.",
};

export function ErrorState({ error }: { error: unknown }) {
  if (error instanceof ApiError) {
    return (
      <div className="panel" data-testid="error-state" data-code={error.code}>
        <h2>{MESSAGES[error.code] ?? "Something went wrong"}</h2>
        <p className="muted">
          {error.code} · {error.message}
        </p>
      </div>
    );
  }
  return (
    <div className="panel" data-testid="error-state" data-code="unknown_error">
      <h2>Something went wrong</h2>
    </div>
  );
}
