const KNOWN = ["passed", "failed", "error", "skipped", "blocked"];

export function OutcomeBadge({ outcome, count }: { outcome: string; count?: number }) {
  const cls = KNOWN.includes(outcome) ? `badge-${outcome}` : "badge-neutral";
  return (
    <span className={`badge ${cls}`} data-testid={`outcome-${outcome}`}>
      {outcome}
      {count === undefined ? "" : ` ${count}`}
    </span>
  );
}
