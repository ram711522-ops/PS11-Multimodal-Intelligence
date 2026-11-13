export function LoadingState({ rows = 3, label = 'Loading investigations' }: { rows?: number; label?: string }) {
  return (
    <div className="loading-state" role="status" aria-label={label}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => <div className="skeleton-line" key={index} />)}
    </div>
  );
}
