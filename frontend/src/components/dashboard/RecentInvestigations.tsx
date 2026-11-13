import { AlertCircle, FileImage, FileText, RefreshCw } from 'lucide-react';
import { EmptyState } from '../common/EmptyState';
import { formatDateTime, displayClaim, shortId, titleCaseStatus } from '../../utils/formatters';
import type { InvestigationSummary } from '../../types/investigation';

function StatusBadge({ status }: { status: string }) {
  return <span className={`status-badge status-badge--${status.replaceAll('_', '-')}`}><i />{titleCaseStatus(status)}</span>;
}

function InputType({ type }: { type: string }) {
  const icon = type === 'image' ? <FileImage size={14} /> : <FileText size={14} />;
  const label = type === 'text_image' ? 'Text + image' : type === 'image' ? 'Image' : 'Text';
  return <span className="input-type">{icon}{label}</span>;
}

interface RecentInvestigationsProps {
  investigations: InvestigationSummary[];
  isLoading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export function RecentInvestigations({
  investigations,
  isLoading = false,
  error = null,
  onRetry,
}: RecentInvestigationsProps) {
  const recent = investigations.slice(0, 8);
  return (
    <section className="panel recent-panel">
      <div className="panel-heading recent-heading">
        <div><div className="eyebrow">LATEST RECORDS</div><h2>Recent investigations</h2></div>
      </div>
      {isLoading ? (
        <div className="recent-skeleton" role="status" aria-label="Loading recent investigations">
          <span className="sr-only">Loading recent investigations</span>
          {Array.from({ length: 4 }, (_, index) => <div className="recent-skeleton-row" key={index}><i /><i /><i /><i /></div>)}
        </div>
      ) : error ? (
        <div className="recent-error" role="alert">
          <AlertCircle size={18} />
          <div><strong>Recent investigations unavailable</strong><p>{error}</p></div>
          {onRetry && <button className="button button--secondary" onClick={onRetry}><RefreshCw size={14} /> Retry</button>}
        </div>
      ) : recent.length === 0 ? (
        <EmptyState title="Nothing under investigation yet" description="New investigations will be listed here after they are created through the API." />
      ) : (
        <div className="table-scroll">
          <table className="investigation-table">
            <thead><tr><th>Investigation</th><th>Input</th><th>Claim preview</th><th>Status</th><th>Language</th><th>Created</th></tr></thead>
            <tbody>
              {recent.map((item) => (
                <tr key={item.investigation_id}>
                  <td><span className="investigation-id" title={item.investigation_id}>{shortId(item.investigation_id)}</span></td>
                  <td><InputType type={item.input_type} /></td>
                  <td><span className="claim-cell" title={displayClaim(item)}>{displayClaim(item)}</span></td>
                  <td><StatusBadge status={item.status} /></td>
                  <td><span className="language-cell">{item.claim?.language || '—'}</span></td>
                  <td><time className="created-cell" dateTime={item.created_at}>{formatDateTime(item.created_at)}</time></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!isLoading && !error && <div className="recent-footnote">Showing up to 8 of the records returned by the investigation history API.</div>}
    </section>
  );
}
