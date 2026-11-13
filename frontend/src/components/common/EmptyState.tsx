import { Activity, FileSearch2 } from 'lucide-react';

export function EmptyState({ title, description, compact = false }: {
  title: string;
  description: string;
  compact?: boolean;
}) {
  const Icon = compact ? Activity : FileSearch2;
  return (
    <div className={`empty-state ${compact ? 'empty-state--compact' : ''}`}>
      <div className="empty-state-icon"><Icon size={19} /></div>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}
