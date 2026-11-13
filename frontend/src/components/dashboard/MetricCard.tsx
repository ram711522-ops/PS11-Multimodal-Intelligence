import type { LucideIcon } from 'lucide-react';

interface MetricCardProps {
  label: string;
  value: number | null;
  note: string;
  icon: LucideIcon;
  tone: 'violet' | 'blue' | 'amber' | 'rose';
  loading?: boolean;
}

export function MetricCard({ label, value, note, icon: Icon, tone, loading = false }: MetricCardProps) {
  return (
    <article className="metric-card">
      <div className="metric-card-top">
        <span>{label}</span>
        <span className={`metric-icon metric-icon--${tone}`}><Icon size={17} strokeWidth={1.8} /></span>
      </div>
      {loading ? <div className="metric-skeleton" aria-label={`Loading ${label}`} /> : <strong className="metric-value">{value?.toLocaleString() ?? '—'}</strong>}
      <span className="metric-note">{loading ? 'Fetching live data' : note}</span>
    </article>
  );
}
